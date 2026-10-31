"""
Uploaded-Artifact Deployment Tools

Deploy a zip file that an operator uploaded through the Command Center. The UI
stages the zip in the platform's artifact bucket (``config.ARTIFACT_BUCKET``, in
the base account) and passes its ``s3://`` URI to the agent. These tools read the
zip with *base-account* credentials and create the target resources with the
*active-account* credentials, so the same upload can be deployed into any
managed account without cross-account bucket policies:

  * Lambda       - the zip bytes are sent directly (or re-staged in the target
                   account when larger than Lambda's 50 MB direct-upload limit).
  * EC2          - the instance downloads the zip through a short-lived
                   presigned URL via SSM Run Command, extracts it, and
                   optionally runs a start command.
  * ECS Fargate  - the zip (with a root Dockerfile) is the CodeBuild context.
  * S3 website   - every file in the zip is uploaded with its content type.
"""

import hashlib
import io
import json
import posixpath
import re
import shlex
import time
import zipfile
from typing import Optional

from botocore.config import Config
from strands import tool
import structlog

from src.core.aws_clients import boto3, get_base_client
from src.tools.deployment_tools import (
    _build_manifest,
    _deploy_ecs_service_impl,
    _deploy_static_site_impl,
    _generate_default_name,
)

from config import ARTIFACT_BUCKET, MAX_ARTIFACT_MB, AWS_REGION as _ARTIFACT_REGION

logger = structlog.get_logger()

LAMBDA_DIRECT_UPLOAD_LIMIT = 50 * 1024 * 1024
LAMBDA_UNZIPPED_LIMIT = 250 * 1024 * 1024
LAMBDA_ROLE_NAME = "cloudops-lambda-execution-role"
EC2_SSM_ROLE_NAME = "CloudOpsEC2SSMRole"
PRESIGNED_URL_TTL_SECONDS = 3600

_IGNORED_PREFIXES = ("__MACOSX/",)
_IGNORED_NAMES = (".DS_Store",)


# ============================================================================
# Internal Helpers
# ============================================================================


def _fail(error_type: str, message: str, suggestion: str, **extra) -> str:
    return json.dumps({
        "status": "failed",
        "error_type": error_type,
        "error_message": message,
        "suggestion": suggestion,
        **extra,
    })


def _parse_artifact_uri(artifact_uri: str):
    """Return (bucket, key) for an uploaded artifact, or raise ValueError."""
    match = re.match(r"^s3://([^/]+)/(.+)$", (artifact_uri or "").strip())
    if not match:
        raise ValueError(f"Invalid artifact URI '{artifact_uri}'. Expected s3://bucket/key.")
    bucket, key = match.group(1), match.group(2)
    # Only artifacts staged by the Command Center may be read with base-account
    # credentials; anything else could expose unrelated platform data.
    if ARTIFACT_BUCKET and bucket != ARTIFACT_BUCKET:
        raise ValueError(
            f"Artifact must live in the upload bucket '{ARTIFACT_BUCKET}', got '{bucket}'."
        )
    if not key.lower().endswith(".zip"):
        raise ValueError("Only .zip artifacts are supported.")
    return bucket, key


def _artifact_s3_client():
    return get_base_client(
        "s3", region_name=_ARTIFACT_REGION, config=Config(signature_version="s3v4")
    )


def _load_artifact(artifact_uri: str) -> bytes:
    """Download the uploaded zip with base-account credentials and validate it."""
    bucket, key = _parse_artifact_uri(artifact_uri)
    s3 = _artifact_s3_client()
    head = s3.head_object(Bucket=bucket, Key=key)
    if head["ContentLength"] > MAX_ARTIFACT_MB * 1024 * 1024:
        raise ValueError(f"Artifact is larger than the {MAX_ARTIFACT_MB} MB limit.")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise ValueError("Artifact is not a valid zip file.")
    return body


def _member_names(zf: zipfile.ZipFile) -> list:
    """File entries worth deploying (skips directories and OS metadata)."""
    names = []
    for info in zf.infolist():
        name = info.filename
        if info.is_dir() or name.startswith(_IGNORED_PREFIXES):
            continue
        if posixpath.basename(name) in _IGNORED_NAMES:
            continue
        names.append(name)
    return names


def _common_root(names: list) -> str:
    """Return 'dir/' when every file sits under one top-level folder, else ''."""
    tops = {n.split("/", 1)[0] for n in names}
    if len(tops) == 1 and all("/" in n for n in names):
        return tops.pop() + "/"
    return ""


def _normalize_zip(zip_bytes: bytes):
    """
    Re-pack the archive without OS metadata and without a single wrapping
    folder (zipping a folder in Finder/Explorer adds one), so handlers and
    Dockerfiles resolve from the root.

    Returns (normalized_zip_bytes, {path: bytes}, stripped_root).
    """
    files = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = _member_names(zf)
        root = _common_root(names)
        for name in names:
            rel = name[len(root):]
            if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise ValueError(f"Unsafe path in archive: {name}")
            files[rel] = zf.read(name)

    if not files:
        raise ValueError("Archive contains no files.")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            info = zipfile.ZipInfo(rel, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return out.getvalue(), files, root


def _text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _detect_lambda_entry(files: dict):
    """Guess (runtime, handler) for a Lambda package. Either may be ''."""
    root_files = [p for p in files if "/" not in p]

    for path in sorted(root_files):
        if path.endswith(".py"):
            source = _text(files[path])
            for fn in ("lambda_handler", "handler"):
                if re.search(rf"^\s*(async\s+)?def\s+{fn}\s*\(", source, re.MULTILINE):
                    return "python3.12", f"{path[:-3]}.{fn}"

    for path in sorted(root_files):
        if path.endswith((".js", ".mjs", ".cjs")):
            source = _text(files[path])
            if re.search(r"exports\.handler\s*=|export\s+(const|async\s+function|function)\s+handler\b", source):
                return "nodejs20.x", f"{path.rsplit('.', 1)[0]}.handler"

    if any(p.endswith(".py") for p in files) or "requirements.txt" in files:
        return "python3.12", ""
    if any(p.endswith((".js", ".mjs")) for p in files) or "package.json" in files:
        return "nodejs20.x", ""
    return "", ""


def _account_id(region: str) -> str:
    return boto3.client("sts", region_name=region).get_caller_identity()["Account"]


def _ensure_role(role_name: str, service: str, managed_policy_arns: list, description: str) -> bool:
    """Create an IAM service role in the active account if missing. Returns True if created."""
    iam = boto3.client("iam")
    try:
        iam.get_role(RoleName=role_name)
        return False
    except iam.exceptions.NoSuchEntityException:
        pass

    trust = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": service},
            "Action": "sts:AssumeRole",
        }],
    }
    iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description=description,
        Tags=[{"Key": "ManagedBy", "Value": "CloudOps"}],
    )
    for arn in managed_policy_arns:
        iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)
    logger.info("iam_role_created", role=role_name)
    return True


def _ensure_staging_bucket(account_id: str, region: str) -> str:
    """Return the active account's private deployment staging bucket, creating it if needed."""
    bucket = f"cloudops-deployments-{account_id}"
    s3 = boto3.client("s3", region_name=region)
    try:
        s3.head_bucket(Bucket=bucket)
        return bucket
    except Exception as e:
        code = str(getattr(e, "response", {}).get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket", "NotFound"):
            raise

    params = {"Bucket": bucket}
    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**params)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    return bucket


def _role_exists(role_name: str) -> bool:
    iam = boto3.client("iam")
    try:
        iam.get_role(RoleName=role_name)
        return True
    except iam.exceptions.NoSuchEntityException:
        return False


# ============================================================================
# Inspection
# ============================================================================


@tool
def inspect_uploaded_artifact(artifact_uri: str) -> str:
    """Inspect an uploaded code zip: list its files, detect language/runtime/handler, and recommend deployment targets.

    Always call this first when the operator has uploaded a zip, before proposing a deployment plan.

    Args:
        artifact_uri: S3 URI of the uploaded zip (from the UPLOADED ARTIFACT note), e.g. s3://cloudops-artifacts-x/uploads/t/abc/app.zip.

    Returns:
        JSON with file list, sizes, detected runtime/handler, and recommended targets.
    """
    try:
        raw = _load_artifact(artifact_uri)
        normalized, files, root = _normalize_zip(raw)

        unzipped = sum(len(b) for b in files.values())
        runtime, handler = _detect_lambda_entry(files)
        has_dockerfile = "Dockerfile" in files
        has_index_html = "index.html" in files
        has_requirements = "requirements.txt" in files
        has_package_json = "package.json" in files
        has_node_modules = any(p.startswith("node_modules/") for p in files)
        has_vendored_py = any(
            "/" in p and (p.split("/", 1)[0].endswith((".dist-info", ".egg-info")))
            for p in files
        )

        recommended = []
        warnings = []
        if has_dockerfile:
            recommended.append("ecs")
        if handler:
            recommended.append("lambda")
        if has_index_html and not handler and not has_dockerfile:
            recommended.append("s3_static_site")
        recommended.append("ec2")

        if handler and unzipped > LAMBDA_UNZIPPED_LIMIT:
            warnings.append("Unzipped size exceeds Lambda's 250 MB limit; use EC2 or ECS instead.")
        if has_requirements and not has_vendored_py:
            warnings.append(
                "requirements.txt found but dependencies are not vendored in the zip. "
                "Lambda will not install them; on EC2 add 'pip install -r requirements.txt' to the run command."
            )
        if has_package_json and not has_node_modules:
            warnings.append(
                "package.json found without node_modules. Lambda will not run npm install; "
                "on EC2 add 'npm install' to the run command."
            )

        return json.dumps({
            "status": "success",
            "artifact_uri": artifact_uri,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "zip_size_bytes": len(raw),
            "unzipped_size_bytes": unzipped,
            "file_count": len(files),
            "stripped_top_level_folder": root.rstrip("/") or None,
            "files": sorted(files)[:100],
            "files_truncated": len(files) > 100,
            "detected": {
                "lambda_runtime": runtime or None,
                "lambda_handler": handler or None,
                "has_dockerfile": has_dockerfile,
                "has_index_html": has_index_html,
                "has_requirements_txt": has_requirements,
                "has_package_json": has_package_json,
            },
            "recommended_targets": recommended,
            "warnings": warnings,
        }, indent=2)

    except ValueError as e:
        return _fail("invalid_artifact", str(e), "Ask the operator to re-upload a valid .zip file.")
    except Exception as e:
        logger.error("inspect_uploaded_artifact_error", error=str(e))
        return _fail(
            "aws_api_error", f"Could not read the uploaded artifact: {e}",
            "Verify the agent runtime role has s3:GetObject on the artifact bucket.",
        )


# ============================================================================
# Lambda
# ============================================================================


@tool
def deploy_artifact_to_lambda(
    artifact_uri: str,
    function_name: str = '',
    runtime: str = '',
    handler: str = '',
    memory_mb: int = 256,
    timeout_seconds: int = 30,
    region: str = 'us-east-1',
) -> str:
    """Deploy an uploaded code zip to AWS Lambda in the active account. Creates the function or updates its code.

    Only call after the operator confirmed the deployment plan.

    Args:
        artifact_uri: S3 URI of the uploaded zip.
        function_name: Lambda function name. If empty, a default name is generated.
        runtime: Lambda runtime (e.g. python3.12, nodejs20.x). Auto-detected when empty.
        handler: Handler (e.g. app.lambda_handler, index.handler). Auto-detected when empty.
        memory_mb: Memory size in MB (128-10240).
        timeout_seconds: Timeout in seconds (1-900).
        region: AWS region for the function.

    Returns:
        JSON deployment manifest on success, or error details.
    """
    try:
        raw = _load_artifact(artifact_uri)
        package, files, _ = _normalize_zip(raw)
    except ValueError as e:
        return _fail("invalid_artifact", str(e), "Ask the operator to re-upload a valid .zip file.")
    except Exception as e:
        return _fail("aws_api_error", f"Could not read the uploaded artifact: {e}",
                     "Verify the agent runtime role has s3:GetObject on the artifact bucket.")

    try:
        detected_runtime, detected_handler = _detect_lambda_entry(files)
        runtime = runtime or detected_runtime
        handler = handler or detected_handler
        if not runtime or not handler:
            return _fail(
                "invalid_payload",
                "Could not determine the Lambda runtime and handler from the zip.",
                "Ask the operator for the runtime (e.g. python3.12) and handler (e.g. app.lambda_handler).",
                detected_runtime=detected_runtime or None,
                detected_handler=detected_handler or None,
            )
        if sum(len(b) for b in files.values()) > LAMBDA_UNZIPPED_LIMIT:
            return _fail("package_too_large", "Unzipped package exceeds Lambda's 250 MB limit.",
                         "Deploy to EC2 or ECS instead, or move dependencies into a Lambda layer.")

        function_name = function_name or _generate_default_name()
        account_id = _account_id(region)
        lambda_client = boto3.client("lambda", region_name=region)

        if len(package) > LAMBDA_DIRECT_UPLOAD_LIMIT:
            bucket = _ensure_staging_bucket(account_id, region)
            key = f"deployments/lambda/{function_name}/{int(time.time())}.zip"
            boto3.client("s3", region_name=region).put_object(Bucket=bucket, Key=key, Body=package)
            code = {"S3Bucket": bucket, "S3Key": key}
        else:
            code = {"ZipFile": package}

        try:
            existing = lambda_client.get_function(FunctionName=function_name)["Configuration"]
        except lambda_client.exceptions.ResourceNotFoundException:
            existing = None

        if existing:
            previous_version = existing.get("Version", "$LATEST")
            lambda_client.update_function_code(FunctionName=function_name, **code)
            lambda_client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
            lambda_client.update_function_configuration(
                FunctionName=function_name,
                Runtime=runtime,
                Handler=handler,
                MemorySize=memory_mb,
                Timeout=timeout_seconds,
            )
            lambda_client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
            published = lambda_client.publish_version(
                FunctionName=function_name,
                Description=f"CloudOps upload {posixpath.basename(artifact_uri)}",
            )
            action = "updated"
        else:
            previous_version = None
            role_created = _ensure_role(
                LAMBDA_ROLE_NAME, "lambda.amazonaws.com",
                ["arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"],
                "Execution role for functions deployed by CloudOps",
            )
            role_arn = f"arn:aws:iam::{account_id}:role/{LAMBDA_ROLE_NAME}"
            # A brand-new role takes a few seconds before Lambda can assume it.
            attempts = 8 if role_created else 1
            for attempt in range(attempts):
                try:
                    published = lambda_client.create_function(
                        FunctionName=function_name,
                        Runtime=runtime,
                        Role=role_arn,
                        Handler=handler,
                        Code=code,
                        MemorySize=memory_mb,
                        Timeout=timeout_seconds,
                        Publish=True,
                        Description=f"CloudOps upload {posixpath.basename(artifact_uri)}",
                        Tags={"ManagedBy": "CloudOps"},
                    )
                    break
                except lambda_client.exceptions.InvalidParameterValueException as e:
                    if "role" not in str(e).lower() or attempt == attempts - 1:
                        raise
                    time.sleep(5)
            lambda_client.get_waiter("function_active_v2").wait(FunctionName=function_name)
            action = "created"

        return _build_manifest(
            target_type="lambda",
            status="success",
            resource_arn=published.get("FunctionArn", ""),
            region=region,
            details={
                "function_name": function_name,
                "action": action,
                "version": published.get("Version"),
                "runtime": runtime,
                "handler": handler,
                "memory_mb": memory_mb,
                "timeout_seconds": timeout_seconds,
                "package_size_bytes": len(package),
                "code_sha256": published.get("CodeSha256"),
                "artifact_uri": artifact_uri,
            },
            rollback_info={"function_name": function_name, "previous_version": previous_version},
        )

    except Exception as e:
        logger.error("deploy_artifact_to_lambda_error", error=str(e))
        return _fail(
            "aws_api_error", f"Lambda deployment failed: {e}",
            "Check lambda:CreateFunction/UpdateFunctionCode and iam:CreateRole/PassRole permissions "
            "in the target account, and that the runtime/handler are correct.",
            target_type="lambda",
        )


# ============================================================================
# EC2
# ============================================================================


def _ami_parameter(instance_type: str) -> str:
    family = instance_type.split(".", 1)[0]
    arch = "arm64" if re.match(r"^[a-z]+\d+g", family) else "x86_64"
    return f"/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-{arch}"


@tool
def launch_ec2_for_artifact(
    name: str = '',
    instance_type: str = 't3.micro',
    subnet_id: str = '',
    security_group_id: str = '',
    region: str = 'us-east-1',
) -> str:
    """Launch a new Amazon Linux 2023 EC2 instance, managed by SSM, to run uploaded code on.

    Use only when the operator wants to run code on EC2 but has no existing instance,
    and only after they confirmed the plan (instance type, region, account). Creates the
    CloudOpsEC2SSMRole instance profile if missing. Then use check_ec2_ssm_status until
    the instance is Online, and deploy_artifact_to_ec2.

    Args:
        name: Name tag for the instance. If empty, a default name is generated.
        instance_type: EC2 instance type (e.g. t3.micro, t4g.small).
        subnet_id: Optional subnet. Defaults to the default VPC; it needs outbound internet for SSM.
        security_group_id: Optional security group. Defaults to the VPC default group.
        region: AWS region.

    Returns:
        JSON deployment manifest with the instance ID, or error details.
    """
    try:
        name = name or _generate_default_name()
        ec2 = boto3.client("ec2", region_name=region)
        iam = boto3.client("iam")

        role_created = _ensure_role(
            EC2_SSM_ROLE_NAME, "ec2.amazonaws.com",
            ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"],
            "Lets CloudOps manage EC2 instances through SSM",
        )
        try:
            iam.get_instance_profile(InstanceProfileName=EC2_SSM_ROLE_NAME)
        except iam.exceptions.NoSuchEntityException:
            iam.create_instance_profile(InstanceProfileName=EC2_SSM_ROLE_NAME)
            iam.add_role_to_instance_profile(
                InstanceProfileName=EC2_SSM_ROLE_NAME, RoleName=EC2_SSM_ROLE_NAME
            )
            role_created = True

        ami_id = boto3.client("ssm", region_name=region).get_parameter(
            Name=_ami_parameter(instance_type)
        )["Parameter"]["Value"]

        params = {
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "IamInstanceProfile": {"Name": EC2_SSM_ROLE_NAME},
            "MetadataOptions": {"HttpTokens": "required", "HttpEndpoint": "enabled"},
            "TagSpecifications": [{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": name}, {"Key": "ManagedBy", "Value": "CloudOps"}],
            }],
        }
        if subnet_id:
            params["SubnetId"] = subnet_id
        if security_group_id:
            params["SecurityGroupIds"] = [security_group_id]

        # A new instance profile takes a few seconds to become usable by EC2.
        attempts = 8 if role_created else 1
        for attempt in range(attempts):
            try:
                instance = ec2.run_instances(**params)["Instances"][0]
                break
            except Exception as e:
                if "instance profile" not in str(e).lower() or attempt == attempts - 1:
                    raise
                time.sleep(5)

        instance_id = instance["InstanceId"]
        ec2.get_waiter("instance_running").wait(InstanceIds=[instance_id])
        account_id = _account_id(region)

        return _build_manifest(
            target_type="ec2",
            status="success",
            resource_arn=f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}",
            region=region,
            details={
                "instance_id": instance_id,
                "name": name,
                "instance_type": instance_type,
                "ami_id": ami_id,
                "instance_profile": EC2_SSM_ROLE_NAME,
                "action": "launched",
                "next_step": "SSM registration takes 1-3 minutes. Use check_ec2_ssm_status, "
                             "then deploy_artifact_to_ec2.",
            },
            rollback_info={"instance_id": instance_id, "note": "Terminate the instance to roll back."},
        )

    except Exception as e:
        logger.error("launch_ec2_for_artifact_error", error=str(e))
        return _fail(
            "aws_api_error", f"Failed to launch EC2 instance: {e}",
            "Check ec2:RunInstances, iam:CreateRole/CreateInstanceProfile/PassRole permissions and "
            "that the region has a default VPC (or pass subnet_id).",
            target_type="ec2",
        )


@tool
def deploy_artifact_to_ec2(
    artifact_uri: str,
    instance_id: str,
    deploy_path: str = '',
    run_command: str = '',
    run_in_background: bool = True,
    wait_seconds: int = 60,
    region: str = 'us-east-1',
) -> str:
    """Deploy an uploaded code zip to an existing SSM-managed Linux EC2 instance and optionally run it.

    The instance downloads the zip through a short-lived presigned URL (works across
    accounts), extracts it, then runs run_command from the extracted folder. Only call
    after the operator confirmed the plan, including the exact run_command.

    Args:
        artifact_uri: S3 URI of the uploaded zip.
        instance_id: Target EC2 instance ID (must be Online in SSM).
        deploy_path: Directory to extract into. Defaults to /opt/cloudops/apps/<zip name>.
        run_command: Optional shell command to run after extraction, e.g. 'pip3 install -r requirements.txt && python3 app.py'.
        run_in_background: True to start run_command detached (for servers; output goes to cloudops-run.log). False to wait for it.
        wait_seconds: How long to wait for the SSM command before returning (max 300).
        region: AWS region of the instance.

    Returns:
        JSON deployment manifest with SSM command status and output, or error details.
    """
    try:
        bucket, key = _parse_artifact_uri(artifact_uri)
    except ValueError as e:
        return _fail("invalid_artifact", str(e), "Ask the operator to re-upload a valid .zip file.")

    if not re.match(r"^i-[0-9a-f]{8,17}$", (instance_id or "").strip()):
        return _fail("invalid_payload", f"'{instance_id}' is not a valid EC2 instance ID.",
                     "Provide an instance ID like i-0abc123def4567890.")
    instance_id = instance_id.strip()

    try:
        ssm = boto3.client("ssm", region_name=region)
        info = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        ).get("InstanceInformationList", [])
        if not info or info[0].get("PingStatus") != "Online":
            return _fail(
                "ssm_not_ready", f"Instance {instance_id} is not Online in SSM.",
                "Ensure the instance runs the SSM agent, has the AmazonSSMManagedInstanceCore "
                "instance profile and outbound internet (or SSM VPC endpoints). Use check_ec2_ssm_status.",
                instance_id=instance_id,
            )
        if info[0].get("PlatformType") == "Windows":
            return _fail("unsupported_platform", "Windows instances are not supported for zip deployment.",
                         "Use a Linux instance.", instance_id=instance_id)

        # Validate before handing the archive to the instance.
        _, _, root = _normalize_zip(_load_artifact(artifact_uri))

        app_name = re.sub(r"[^A-Za-z0-9_.-]", "-", posixpath.basename(key)[:-4]) or "app"
        deploy_path = (deploy_path or f"/opt/cloudops/apps/{app_name}").rstrip("/")
        workdir = f"{deploy_path}/{root}".rstrip("/")
        url = _artifact_s3_client().generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=PRESIGNED_URL_TTL_SECONDS
        )

        q_dest, q_url, q_work = shlex.quote(deploy_path), shlex.quote(url), shlex.quote(workdir)
        commands = [
            "set -eu",
            f"mkdir -p {q_dest}",
            "TMP=$(mktemp /tmp/cloudops-XXXXXX.zip)",
            f"if command -v curl >/dev/null 2>&1; then curl -fsSL -o \"$TMP\" {q_url}; "
            f"else python3 -c 'import sys,urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' {q_url} \"$TMP\"; fi",
            f"if command -v unzip >/dev/null 2>&1; then unzip -oq \"$TMP\" -d {q_dest}; "
            f"else python3 -m zipfile -e \"$TMP\" {q_dest}; fi",
            "rm -f \"$TMP\"",
            f"echo \"Extracted to {workdir}\"",
            f"cd {q_work}",
        ]
        if run_command:
            if run_in_background:
                commands += [
                    f"nohup bash -c {shlex.quote(run_command)} > cloudops-run.log 2>&1 < /dev/null &",
                    f"echo \"Started in background (PID $!). Logs: {workdir}/cloudops-run.log\"",
                ]
            else:
                commands.append(f"bash -c {shlex.quote(run_command)}")

        command_id = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands, "executionTimeout": ["1800"]},
            Comment=f"CloudOps deploy {app_name}"[:100],
            TimeoutSeconds=600,
        )["Command"]["CommandId"]

        status, stdout, stderr = "Pending", "", ""
        deadline = time.time() + max(5, min(wait_seconds, 300))
        while time.time() < deadline:
            time.sleep(3)
            try:
                inv = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except ssm.exceptions.InvocationDoesNotExist:
                continue
            status = inv.get("Status", status)
            stdout = inv.get("StandardOutputContent", "")
            stderr = inv.get("StandardErrorContent", "")
            if status not in ("Pending", "InProgress", "Delayed"):
                break

        account_id = _account_id(region)
        details = {
            "instance_id": instance_id,
            "deployment_path": workdir,
            "run_command": run_command or None,
            "run_in_background": run_in_background if run_command else None,
            "ssm_command_id": command_id,
            "command_status": status,
            "stdout_tail": stdout[-3000:],
            "stderr_tail": stderr[-3000:],
            "artifact_uri": artifact_uri,
        }
        if status in ("Pending", "InProgress", "Delayed"):
            details["note"] = "Still running. Use get_ssm_command_output with the command ID to follow up."

        failed = status in ("Failed", "Cancelled", "TimedOut")
        return _build_manifest(
            target_type="ec2",
            status="failed" if failed else "success",
            resource_arn=f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}",
            region=region,
            details=details,
            error=f"SSM command finished with status {status}" if failed else None,
            rollback_info={"deployment_path": deploy_path,
                           "note": "Stop the process and remove the directory to roll back."},
        )

    except ValueError as e:
        return _fail("invalid_artifact", str(e), "Ask the operator to re-upload a valid .zip file.")
    except Exception as e:
        logger.error("deploy_artifact_to_ec2_error", error=str(e))
        return _fail(
            "aws_api_error", f"EC2 deployment failed: {e}",
            "Check ssm:SendCommand permissions and that the instance is SSM-managed.",
            target_type="ec2", instance_id=instance_id,
        )


# ============================================================================
# ECS Fargate and S3 static site
# ============================================================================


@tool
def deploy_artifact_to_ecs(
    artifact_uri: str,
    service_name: str = '',
    cluster_name: str = 'cloudops-cluster',
    region: str = 'us-east-1',
) -> str:
    """Build an uploaded code zip (with a Dockerfile at its root) via CodeBuild, push to ECR, and run it on ECS Fargate.

    The container must listen on port 80. Only call after the operator confirmed the plan.

    Args:
        artifact_uri: S3 URI of the uploaded zip.
        service_name: ECS service / ECR repository name. If empty, a default name is generated.
        cluster_name: ECS cluster name (created if missing).
        region: AWS region.

    Returns:
        JSON deployment manifest, or error details.
    """
    try:
        package, files, _ = _normalize_zip(_load_artifact(artifact_uri))
    except ValueError as e:
        return _fail("invalid_artifact", str(e), "Ask the operator to re-upload a valid .zip file.")
    except Exception as e:
        return _fail("aws_api_error", f"Could not read the uploaded artifact: {e}",
                     "Verify the agent runtime role has s3:GetObject on the artifact bucket.")

    if "Dockerfile" not in files:
        return _fail("invalid_payload", "The zip has no Dockerfile at its root.",
                     "Add a Dockerfile, or deploy to Lambda/EC2 instead.")

    try:
        missing = [r for r in ("cloudops-codebuild-role", "cloudops-ecs-execution-role") if not _role_exists(r)]
        if missing:
            return _fail(
                "missing_prerequisite", f"Required IAM role(s) missing in this account: {', '.join(missing)}.",
                "Deploy infra/cross_account_role.yaml (CreateDeploymentRoles=true) in this account.",
            )
        _ensure_staging_bucket(_account_id(region), region)
    except Exception as e:
        return _fail("aws_api_error", f"Pre-deployment checks failed: {e}",
                     "Check iam:GetRole and s3:CreateBucket permissions in the target account.")

    return _deploy_ecs_service_impl(
        "", "", service_name=service_name or _generate_default_name(),
        cluster_name=cluster_name, region=region, source_zip=package,
    )


@tool
def deploy_artifact_to_static_site(
    artifact_uri: str,
    bucket_name: str = '',
    enable_cloudfront: bool = False,
    region: str = 'us-east-1',
) -> str:
    """Publish an uploaded zip of static web files (index.html at the root) as a public S3 website.

    Only call after the operator confirmed the plan; the bucket is made publicly readable.

    Args:
        artifact_uri: S3 URI of the uploaded zip.
        bucket_name: Target bucket. If empty, 'cloudops-static-<timestamp>' is created.
        enable_cloudfront: Also create a CloudFront distribution.
        region: AWS region for the bucket.

    Returns:
        JSON deployment manifest with the website URL, or error details.
    """
    try:
        _, files, _ = _normalize_zip(_load_artifact(artifact_uri))
    except ValueError as e:
        return _fail("invalid_artifact", str(e), "Ask the operator to re-upload a valid .zip file.")
    except Exception as e:
        return _fail("aws_api_error", f"Could not read the uploaded artifact: {e}",
                     "Verify the agent runtime role has s3:GetObject on the artifact bucket.")

    if "index.html" not in files:
        return _fail("invalid_payload", "The zip has no index.html at its root.",
                     "Static sites need an index.html; deploy to EC2 or ECS for application code.")

    return _deploy_static_site_impl(files, bucket_name, enable_cloudfront, region)
