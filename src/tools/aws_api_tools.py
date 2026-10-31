"""
Generic AWS API access, for anything the purpose-built tools don't cover.

``query_aws_api`` runs read-only operations (Describe*, List*, Get*, ...)
immediately. ``call_aws_api`` runs any operation and, being a mutating tool, is
always queued for operator approval first; the approval card shows the exact
service, operation and parameters. Both use the account-aware boto3 shim, so
they work in whichever account the request targets.
"""

import json
import re

import botocore.session
from strands import tool
import structlog

from src.core.aws_clients import boto3

logger = structlog.get_logger()

READ_PREFIXES = ("Describe", "List", "Get", "Search", "Lookup", "Scan", "Query", "BatchGet",
                 "Check", "Validate", "Estimate", "Preview", "Simulate", "Test", "View")
# Read operations that would hand out credentials or secrets.
SENSITIVE_READS = re.compile(r"(Token|Credential|Password|SecretValue|AuthorizationToken|SessionToken|"
                             r"FederationToken|Decrypt|GenerateDataKey|PrivateKey)")
_MAX_CHARS = 20000


def _operation(service: str, operation: str) -> str:
    """Validate service/operation against botocore's models; return the API name (e.g. DescribeVpcs)."""
    session = botocore.session.get_session()
    if service not in session.get_available_services():
        raise ValueError(f"Unknown AWS service '{service}'. Use the boto3 client name, e.g. ec2, s3, rds.")
    model = session.get_service_model(service)
    lookup = {name.lower(): name for name in model.operation_names}
    api_name = lookup.get(operation.replace("_", "").replace("-", "").lower())
    if not api_name:
        raise ValueError(f"'{service}' has no operation '{operation}'.")
    return api_name


def _params(parameters: str) -> dict:
    params = json.loads(parameters) if parameters and parameters.strip() else {}
    if not isinstance(params, dict):
        raise ValueError("parameters must be a JSON object.")
    return params


def _invoke(service: str, operation: str, parameters: str, region: str):
    api_name = _operation(service, operation)
    params = _params(parameters)
    client = boto3.client(service, region_name=region) if region else boto3.client(service)
    method = client.meta.method_to_api_mapping
    method = next(m for m, a in method.items() if a == api_name)
    result = getattr(client, method)(**params)
    result.pop("ResponseMetadata", None)
    text = json.dumps(result, default=str)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + f'... [truncated {len(text) - _MAX_CHARS} chars; narrow the request]'
    return api_name, text


def _error(e: Exception, service: str, operation: str) -> str:
    return json.dumps({"status": "failed", "service": service, "operation": operation,
                       "error_type": type(e).__name__, "error_message": str(e)})


@tool
def query_aws_api(service: str, operation: str, parameters: str = '{}', region: str = 'us-east-1') -> str:
    """Run a READ-ONLY AWS API operation (Describe*, List*, Get*, Search*, ...) when no specific tool fits.

    Args:
        service: boto3 client name, e.g. ec2, s3, rds, iam, elbv2, logs.
        operation: API operation, e.g. DescribeVpcs or describe_vpcs.
        parameters: JSON object of API parameters, e.g. '{"Filters": [{"Name": "tag:env", "Values": ["prod"]}]}'.
        region: AWS region (ignored by global services).

    Returns:
        JSON API response (truncated if very large), or error details.
    """
    try:
        api_name = _operation(service, operation)
        if not api_name.startswith(READ_PREFIXES):
            return json.dumps({"status": "failed", "error_type": "not_read_only",
                               "error_message": f"{api_name} changes resources; use call_aws_api so it "
                                                f"is queued for approval."})
        if SENSITIVE_READS.search(api_name) or _params(parameters).get("WithDecryption") is True:
            return json.dumps({"status": "failed", "error_type": "sensitive_operation",
                               "error_message": f"{api_name} returns secrets or credentials and is not "
                                                f"available to the agent."})
        _, text = _invoke(service, operation, parameters, region)
        return text
    except Exception as e:
        return _error(e, service, operation)


@tool
def call_aws_api(service: str, operation: str, parameters: str = '{}', region: str = 'us-east-1') -> str:
    """Run ANY AWS API operation that changes resources (Create*, Run*, Put*, Update*, Delete*, ...) when no specific tool fits.

    The call is queued for operator approval with these exact arguments before it runs.

    Args:
        service: boto3 client name, e.g. ec2, s3, rds, iam, lambda.
        operation: API operation, e.g. CreateVpc or create_vpc.
        parameters: JSON object of the API parameters.
        region: AWS region (ignored by global services).

    Returns:
        JSON API response, or error details.
    """
    try:
        api_name, text = _invoke(service, operation, parameters, region)
        logger.info("aws_api_called", service=service, operation=api_name)
        return json.dumps({"status": "success", "service": service, "operation": api_name,
                           "response": json.loads(text) if not text.endswith("narrow the request]") else text})
    except Exception as e:
        return _error(e, service, operation)
