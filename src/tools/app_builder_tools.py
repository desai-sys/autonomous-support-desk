"""
Tools for applications built by the app builder (src/appbuilder).

Apps are written by the build pipeline, not through tool calls. These tools
cover the rest of an app's life:

  get_generated_app     read a version's files and template
  deploy_generated_app  create/update the stack, then call its URL outputs   [approval-gated]
  delete_generated_app  delete the stack                                      [approval-gated]
"""

import json
import time
import urllib.error
import urllib.request

from botocore.config import Config
from strands import tool
import structlog

import config
from src.core import request_context
from src.core.aws_clients import boto3, get_base_client
from src.services.app_bundles import AppBundleStore, BundleError
from src.tools.artifact_tools import _ensure_staging_bucket
from src.tools.deployment_tools import _build_manifest

logger = structlog.get_logger()

# Not "cloudops-app-": that is the platform's own stack, which the executor is denied.
STACK_PREFIX = "cloudops-gen-"
_TERMINAL_OK = {"CREATE_COMPLETE", "UPDATE_COMPLETE"}
_TERMINAL_BAD = {"CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED", "UPDATE_ROLLBACK_COMPLETE",
                 "UPDATE_ROLLBACK_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}


def _store() -> AppBundleStore:
    s3 = get_base_client("s3", region_name=config.AWS_REGION, config=Config(signature_version="s3v4"))
    return AppBundleStore(s3)


def _fail(error_type: str, message: str, suggestion: str = "", **extra) -> str:
    return json.dumps({"status": "failed", "error_type": error_type, "error_message": message,
                       "suggestion": suggestion, **extra})


def _stack_name(app_id: str, stack_name: str) -> str:
    return (stack_name or f"{STACK_PREFIX}{app_id}")[:128]


@tool
def get_generated_app(app_id: str, version: int = 0) -> str:
    """Read a generated app's files and template (version 0 = latest).

    Args:
        app_id: The app's name.
        version: Version number; 0 for the latest.

    Returns:
        JSON with manifest, files and template.
    """
    try:
        manifest, files, template = _store().load(request_context.tenant_id(), app_id, version)
    except BundleError as e:
        return _fail("not_found", str(e))
    except Exception as e:
        return _fail("storage_error", f"Could not read the app: {e}")
    return json.dumps({"manifest": manifest, "files": files, "template": template})


def _missing_execution_role(region: str) -> str:
    """Explain how to add the shared execution role if this account doesn't have it ('' if it does)."""
    from src.appbuilder.blueprints import EXECUTION_ROLE_NAME
    try:
        boto3.client("iam", region_name=region).get_role(RoleName=EXECUTION_ROLE_NAME)
        return ""
    except Exception as e:
        if "NoSuchEntity" not in str(e):
            return ""                       # can't tell (e.g. no iam:GetRole); let CloudFormation decide
    return (f"This account has no '{EXECUTION_ROLE_NAME}' role, which generated apps run as. In the platform "
            f"account it is created by scripts/deploy.py; in a connected account, re-run the command shown "
            f"on the console's Accounts page to update the cross-account stack.")


def _smoke_test(outputs: dict) -> list:
    """GET every http(s) URL among the stack outputs and report the response."""
    results = []
    for key, value in outputs.items():
        if not (isinstance(value, str) and value.startswith(("http://", "https://"))):
            continue
        try:
            # Only http(s) URLs reach here (checked above).
            req = urllib.request.Request(value, headers={"User-Agent": "CloudOps-smoke-test"})  # noqa: S310
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                body = resp.read(500).decode("utf-8", "replace")
                results.append({"output": key, "url": value, "status": resp.status, "body": body})
        except urllib.error.HTTPError as e:
            results.append({"output": key, "url": value, "status": e.code,
                            "body": e.read(300).decode("utf-8", "replace")})
        except Exception as e:
            results.append({"output": key, "url": value, "error": str(e)})
    return results


@tool
def deploy_generated_app(app_id: str, version: int, stack_name: str = '', region: str = 'us-east-1') -> str:
    """Deploy (create or update) a generated app's CloudFormation stack in the active account, then smoke-test it.

    Approval-gated: the operator sees the app's files and template before it runs.

    Args:
        app_id: The app's name.
        version: The version to deploy.
        stack_name: CloudFormation stack name; defaults to cloudops-gen-<app_id>.
        region: AWS region.

    Returns:
        JSON deployment manifest with stack outputs (e.g. the app URL) and smoke-test results.
    """
    tenant = request_context.tenant_id()
    store = _store()
    try:
        manifest, files, template = store.load(tenant, app_id, version)
        package = store.load_code(tenant, app_id, manifest["version"])
    except BundleError as e:
        return _fail("not_found", str(e))
    except Exception as e:
        return _fail("storage_error", f"Could not read the app: {e}")

    stack = _stack_name(app_id, stack_name)
    cfn = boto3.client("cloudformation", region_name=region)
    if manifest.get("blueprint"):
        missing = _missing_execution_role(region)
        if missing:
            return _fail("missing_execution_role", missing, stack_name=stack)
    try:
        account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
        params = []
        if package:
            bucket = _ensure_staging_bucket(account_id, region)
            key = f"generated-apps/{app_id}/v{manifest['version']}-{int(time.time())}.zip"
            boto3.client("s3", region_name=region).put_object(Bucket=bucket, Key=key, Body=package)
            params = [{"ParameterKey": "ArtifactBucket", "ParameterValue": bucket},
                      {"ParameterKey": "ArtifactKey", "ParameterValue": key}]

        try:
            status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        except Exception:
            status = None
        if status == "ROLLBACK_COMPLETE":  # a failed first create can't be updated
            cfn.delete_stack(StackName=stack)
            cfn.get_waiter("stack_delete_complete").wait(StackName=stack)
            status = None

        common = dict(StackName=stack, TemplateBody=template, Parameters=params,
                      Capabilities=["CAPABILITY_IAM", "CAPABILITY_AUTO_EXPAND"],
                      Tags=[{"Key": "ManagedBy", "Value": "CloudOps"},
                            {"Key": "cloudops:app", "Value": app_id},
                            {"Key": "cloudops:version", "Value": str(manifest["version"])}])
        started = time.time()
        if status:
            try:
                cfn.update_stack(**common)
                action = "updated"
            except Exception as e:
                if "No updates are to be performed" not in str(e):
                    raise
                action = "unchanged"
        else:
            cfn.create_stack(OnFailure="ROLLBACK", **common)
            action = "created"

        if action != "unchanged":
            deadline = time.time() + 900
            while time.time() < deadline:
                status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
                if status in _TERMINAL_OK or status in _TERMINAL_BAD:
                    break
                time.sleep(10)

        described = cfn.describe_stacks(StackName=stack)["Stacks"][0]
        status = described["StackStatus"]
        if status not in _TERMINAL_OK:
            failures = [
                f"{e['LogicalResourceId']}: {e.get('ResourceStatusReason', '')}"
                for e in cfn.describe_stack_events(StackName=stack)["StackEvents"]
                if e["ResourceStatus"].endswith("FAILED") and e["Timestamp"].timestamp() >= started - 5
            ][:10]
            return _fail("stack_failed", f"Stack {stack} ended in {status}.",
                         "Fix the template (prepare a new version) and deploy again.",
                         stack_name=stack, failures=failures)

        outputs = {o["OutputKey"]: o["OutputValue"] for o in described.get("Outputs", [])}
        smoke = _smoke_test(outputs)
        return _build_manifest(
            target_type="cloudformation",
            status="success",
            resource_arn=described["StackId"],
            region=region,
            details={"app_id": app_id, "version": manifest["version"], "stack_name": stack,
                     "action": action, "outputs": outputs, "smoke_test": smoke},
            rollback_info={"stack_name": stack,
                           "note": "Deploy an earlier version, or delete_generated_app to remove it."},
        )
    except Exception as e:
        logger.error("deploy_generated_app_failed", error=str(e))
        return _fail("aws_api_error", f"Deploying {stack} failed: {e}",
                     "Check CloudFormation permissions in the target account.", stack_name=stack)


@tool
def delete_generated_app(app_id: str, stack_name: str = '', region: str = 'us-east-1') -> str:
    """Delete a generated app's CloudFormation stack (and the resources it created). Approval-gated.

    Args:
        app_id: The app's name.
        stack_name: Stack name if it is not the default cloudops-gen-<app_id>.
        region: AWS region.

    Returns:
        JSON result.
    """
    stack = _stack_name(app_id, stack_name)
    try:
        cfn = boto3.client("cloudformation", region_name=region)
        cfn.delete_stack(StackName=stack)
        cfn.get_waiter("stack_delete_complete").wait(StackName=stack,
                                                      WaiterConfig={"Delay": 10, "MaxAttempts": 90})
        return json.dumps({"status": "success", "stack_name": stack, "action": "deleted"})
    except Exception as e:
        return _fail("aws_api_error", f"Deleting {stack} failed: {e}",
                     "Empty any S3 buckets the stack created, then try again.", stack_name=stack)
