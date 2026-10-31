#!/usr/bin/env python3
"""
Deploy the CloudOps platform into the AWS account and region of your current
credentials. Safe to re-run: every step is idempotent and only changed parts
are rebuilt.

    python scripts/deploy.py --region us-east-1 --admin-email you@example.com

Steps:
  1. Deploy infra/foundation.yaml (tables, buckets, knowledge base, Cognito, secrets, image build)
  2. Package the agent (manylinux aarch64 wheels + source) and upload it to S3
  3. Build the console image with CodeBuild (no local Docker needed)
  4. Deploy infra/app.yaml (AgentCore runtime, ECS console, CloudFront, schedule, alarms)
  5. Invite the first admin user (Cognito emails a temporary password)

See docs/DEPLOYMENT.md for prerequisites and options.
"""

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError, WaiterError
except ImportError:  # pragma: no cover
    sys.exit("boto3 is required: pip install -r requirements/dev.txt")

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
AGENT_SOURCES = ["agent_main.py", "config.py", "src"]
CONSOLE_SOURCES = ["Dockerfile", "config.py", "src", "ui", "requirements/console.txt",
                   ".streamlit/config.toml", "infra/cross_account_role.yaml"]
SKIP_PARTS = {"__pycache__", ".pytest_cache"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── Packaging ─────────────────────────────────────────────────────────────────

def _iter_files(paths):
    for rel in paths:
        path = ROOT / rel
        if path.is_file():
            yield path, rel
        else:
            for f in sorted(path.rglob("*")):
                if f.is_file() and not SKIP_PARTS & set(f.parts) and f.suffix not in (".pyc", ".pyo"):
                    yield f, f.relative_to(ROOT).as_posix()


def _zip_tree(entries) -> bytes:
    """Deterministic zip (fixed timestamps) of (path, archive_name) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in sorted(entries, key=lambda e: e[1]):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if os.access(path, os.X_OK) else 0o644) << 16
            zf.writestr(info, path.read_bytes())
    return buf.getvalue()


AGENT_LOCK = ROOT / "requirements/agent.lock.txt"


def build_agent_package() -> bytes:
    """
    Agent code + dependencies for AgentCore's managed Python 3.12 runtime (arm64).

    Dependencies come from requirements/agent.lock.txt, which is already resolved
    for linux/arm64 and installed with --no-deps. Resolving here instead would let
    pip apply the *host's* environment markers (e.g. pull in pywin32 on Windows).
    """
    target = BUILD / "agent"
    stamp = target / ".requirements.sha256"
    req = AGENT_LOCK.read_bytes()
    req_hash = hashlib.sha256(req).hexdigest()
    if not (stamp.exists() and stamp.read_text() == req_hash):
        log("Installing agent dependencies for linux/arm64 (Python 3.12)...")
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
            "--platform", "manylinux2014_aarch64", "--implementation", "cp",
            "--python-version", "3.12", "--only-binary=:all:",
            "--no-deps", "--require-hashes",
            "--target", str(target), "-r", str(AGENT_LOCK),
        ], check=True)
        stamp.write_text(req_hash)
    deps = [(p, p.relative_to(target).as_posix()) for p in target.rglob("*")
            if p.is_file() and p != stamp and not SKIP_PARTS & set(p.parts)]
    return _zip_tree(deps + list(_iter_files(AGENT_SOURCES)))


def build_console_context() -> bytes:
    return _zip_tree(_iter_files(CONSOLE_SOURCES))


# ── CloudFormation ────────────────────────────────────────────────────────────

def deploy_stack(cfn, name: str, template: Path, params: dict, s3=None, bucket=None) -> dict:
    """Create or update a stack via a change set; returns its outputs."""
    body = template.read_text()
    tmpl = {"TemplateBody": body}
    if len(body.encode()) > 51200:
        key = f"templates/{template.name}"
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode())
        region = cfn.meta.region_name
        tmpl = {"TemplateURL": f"https://{bucket}.s3.{region}.amazonaws.com/{key}"}

    try:
        stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
        exists = stack["StackStatus"] != "REVIEW_IN_PROGRESS"
        if stack["StackStatus"] == "ROLLBACK_COMPLETE":
            log(f"{name} is in ROLLBACK_COMPLETE (a failed first create); deleting it first.")
            cfn.delete_stack(StackName=name)
            cfn.get_waiter("stack_delete_complete").wait(StackName=name)
            exists = False
    except ClientError:
        exists = False

    change_set = f"deploy-{int(time.time())}"
    cfn.create_change_set(
        StackName=name, ChangeSetName=change_set, ChangeSetType="UPDATE" if exists else "CREATE",
        Parameters=[{"ParameterKey": k, "ParameterValue": str(v)} for k, v in params.items()],
        Capabilities=["CAPABILITY_NAMED_IAM", "CAPABILITY_AUTO_EXPAND"],
        Tags=[{"Key": "App", "Value": params.get("AppName", "cloudops")}], **tmpl,
    )
    try:
        cfn.get_waiter("change_set_create_complete").wait(
            StackName=name, ChangeSetName=change_set, WaiterConfig={"Delay": 5, "MaxAttempts": 120})
    except WaiterError:
        cs = cfn.describe_change_set(StackName=name, ChangeSetName=change_set)
        reason = cs.get("StatusReason", "")
        if "didn't contain changes" in reason or "No updates" in reason:
            cfn.delete_change_set(StackName=name, ChangeSetName=change_set)
            log(f"{name}: no changes.")
            return _outputs(cfn, name)
        sys.exit(f"Change set for {name} failed: {reason}")

    log(f"{name}: {'updating' if exists else 'creating'} (this can take 5-20 minutes)...")
    started = time.time()
    cfn.execute_change_set(StackName=name, ChangeSetName=change_set)
    waiter = "stack_update_complete" if exists else "stack_create_complete"
    try:
        cfn.get_waiter(waiter).wait(StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 240})
    except WaiterError:
        _print_failures(cfn, name, started)
        sys.exit(f"Deploying {name} failed. Fix the error above and re-run the script.")
    log(f"{name}: done.")
    return _outputs(cfn, name)


def _outputs(cfn, name: str) -> dict:
    stack = cfn.describe_stacks(StackName=name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def _print_failures(cfn, name: str, since: float) -> None:
    for event in cfn.describe_stack_events(StackName=name)["StackEvents"]:
        if event["Timestamp"].timestamp() < since - 5:
            break
        if event["ResourceStatus"].endswith("FAILED"):
            print(f"  ✗ {event['LogicalResourceId']}: {event.get('ResourceStatusReason', '')}")


# ── Steps ─────────────────────────────────────────────────────────────────────

def upload_agent(s3, bucket: str) -> str:
    package = build_agent_package()
    key = f"agent/agent-{hashlib.sha256(package).hexdigest()[:16]}.zip"
    try:
        s3.head_object(Bucket=bucket, Key=key)
        log(f"Agent package unchanged ({key}).")
    except ClientError:
        log(f"Uploading agent package ({len(package) / 1e6:.1f} MB) to s3://{bucket}/{key}")
        s3.put_object(Bucket=bucket, Key=key, Body=package)
    return key


def build_console_image(session, foundation: dict) -> str:
    context = build_console_context()
    tag = hashlib.sha256(context).hexdigest()[:16]
    repo_name = foundation["ConsoleRepositoryUri"].split("/", 1)[1]
    ecr = session.client("ecr")
    try:
        ecr.describe_images(repositoryName=repo_name, imageIds=[{"imageTag": tag}])
        log(f"Console image {tag} already built.")
        return tag
    except ClientError:
        pass

    bucket = foundation["ArtifactBucketName"]
    key = f"builds/console-{tag}.zip"
    session.client("s3").put_object(Bucket=bucket, Key=key, Body=context)
    codebuild = session.client("codebuild")
    build = codebuild.start_build(
        projectName=foundation["ConsoleBuildProject"],
        sourceTypeOverride="S3", sourceLocationOverride=f"{bucket}/{key}",
        environmentVariablesOverride=[{"name": "IMAGE_TAG", "value": tag, "type": "PLAINTEXT"}],
    )["build"]
    log(f"Building console image {tag} with CodeBuild ({build['id']})...")
    while True:
        time.sleep(15)
        build = codebuild.batch_get_builds(ids=[build["id"]])["builds"][0]
        if build["buildStatus"] != "IN_PROGRESS":
            break
    if build["buildStatus"] != "SUCCEEDED":
        link = build.get("logs", {}).get("deepLink", "")
        sys.exit(f"Console image build {build['buildStatus']}. Logs: {link}")
    log("Console image built.")
    return tag


def cloudfront_prefix_list(session) -> str:
    lists = session.client("ec2").describe_managed_prefix_lists(
        Filters=[{"Name": "prefix-list-name", "Values": ["com.amazonaws.global.cloudfront.origin-facing"]}]
    )["PrefixLists"]
    if not lists:
        sys.exit("CloudFront origin-facing prefix list not found in this region.")
    return lists[0]["PrefixListId"]


def secret_value(session, arn: str) -> str:
    return session.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]


def invite_admin(session, user_pool_id: str, email: str, tenant: str) -> None:
    idp = session.client("cognito-idp")
    try:
        idp.admin_create_user(
            UserPoolId=user_pool_id, Username=email,
            UserAttributes=[{"Name": "email", "Value": email},
                            {"Name": "email_verified", "Value": "true"},
                            {"Name": "custom:tenant_id", "Value": tenant}],
            DesiredDeliveryMediums=["EMAIL"],
        )
        log(f"Invited {email}; Cognito has emailed a temporary password.")
    except idp.exceptions.UsernameExistsException:
        log(f"User {email} already exists.")
    idp.admin_add_user_to_group(UserPoolId=user_pool_id, Username=email, GroupName="admin")


def preflight(session, region: str) -> str:
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10+ is required.")
    try:
        identity = session.client("sts").get_caller_identity()
    except Exception as e:
        sys.exit(f"No usable AWS credentials ({e}). Run `aws configure` or `aws sso login` first.")
    log(f"Deploying to account {identity['Account']} in {region} as {identity['Arn']}")
    return identity["Account"]


def main() -> None:
    # Windows consoles may not be UTF-8; never crash on a status symbol.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--profile", help="AWS CLI profile to use")
    p.add_argument("--app-name", default="cloudops", help="Lowercase letters/digits (default cloudops)")
    p.add_argument("--environment", default="prod")
    p.add_argument("--admin-email", help="Invite this person as the first admin")
    p.add_argument("--tenant", default="default", help="Tenant id for the first admin")
    p.add_argument("--alarm-email", default="", help="Email for CloudWatch alarm notifications")
    p.add_argument("--platform-access", choices=["PowerUser", "ReadOnly"], default="PowerUser",
                   help="What approved changes may do in this account (default PowerUser)")
    p.add_argument("--require-separate-approver", action="store_true",
                   help="Requesters cannot approve their own changes")
    p.add_argument("--daily-request-limit", type=int, default=0, help="Per-tenant agent requests/day (0 = unlimited)")
    p.add_argument("--console-tasks", type=int, default=2)
    p.add_argument("--agent-model", default="us.amazon.nova-pro-v1:0")
    p.add_argument("--router-model", default="us.amazon.nova-micro-v1:0")
    p.add_argument("--build-model", default="",
                   help="Model for the app builder (e.g. us.amazon.nova-premier-v1:0); default: --agent-model")
    p.add_argument("--domain", default="", help="Custom console domain (needs --certificate-arn)")
    p.add_argument("--certificate-arn", default="", help="ACM certificate in us-east-1 for --domain")
    args = p.parse_args()

    if bool(args.domain) != bool(args.certificate_arn):
        sys.exit("--domain and --certificate-arn must be given together.")

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    preflight(session, args.region)
    cfn, s3 = session.client("cloudformation"), session.client("s3")
    app = args.app_name

    foundation = deploy_stack(cfn, f"{app}-foundation", ROOT / "infra/foundation.yaml",
                              {"AppName": app})
    bucket = foundation["ArtifactBucketName"]

    agent_key = upload_agent(s3, bucket)
    image_tag = build_console_image(session, foundation)

    params = {
        "AppName": app,
        "Environment": args.environment,
        **{k: foundation[k] for k in (
            "ArtifactBucketName", "KnowledgeBucketName", "TicketsTableName", "AuditTableName",
            "ActionsTableName", "AccountsTableName", "UsageTableName", "KnowledgeBaseId",
            "KnowledgeBaseArn", "DataSourceId", "UserPoolId", "UserPoolDomain", "CookieSecretArn",
            "ConsoleRepositoryUri")},
        "ConsoleImageTag": image_tag,
        "AgentCodeKey": agent_key,
        "CrossAccountExternalId": secret_value(session, foundation["ExternalIdSecretArn"]),
        "OriginVerifySecret": secret_value(session, foundation["OriginVerifySecretArn"]),
        "CloudFrontPrefixListId": cloudfront_prefix_list(session),
        "PlatformAccountAccess": args.platform_access,
        "AgentModelId": args.agent_model,
        "RouterModelId": args.router_model,
        "BuildModelId": args.build_model,
        "RequireSeparateApprover": str(args.require_separate_approver).lower(),
        "DailyRequestLimitPerTenant": args.daily_request_limit,
        "ConsoleDesiredCount": args.console_tasks,
        "AlarmEmail": args.alarm_email,
        "DomainName": args.domain,
        "CertificateArn": args.certificate_arn,
    }
    outputs = deploy_stack(cfn, f"{app}-app", ROOT / "infra/app.yaml", params, s3=s3, bucket=bucket)

    if args.admin_email:
        invite_admin(session, foundation["UserPoolId"], args.admin_email, args.tenant)

    print("\n✅ CloudOps is deployed.\n")
    print(f"  Console:            {outputs['ConsoleUrl']}")
    print(f"  Agent runtime:      {outputs['AgentRuntimeArn']}")
    print(f"  Agent runtime role: {outputs['AgentRuntimeRoleArn']}")
    print(f"  External ID secret: {foundation['ExternalIdSecretArn']}")
    if args.domain:
        print(f"\n  Point a DNS CNAME/alias for {args.domain} at {outputs['CloudFrontDomainName']}.")
    print("\n  Next: sign in, then connect other AWS accounts on the Accounts page "
          "(docs/DEPLOYMENT.md, step 6).")


if __name__ == "__main__":
    main()
