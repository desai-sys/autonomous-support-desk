"""
Generic AWS API access, for anything the purpose-built tools don't cover.

``list_aws_operations`` and ``describe_aws_operation`` read the API definitions
bundled with the installed botocore, so the agent works from the exact current
operation names and parameters instead of what its model remembers (which may
predate newer AWS features). Upgrading boto3/botocore (see
.github/workflows/update-aws-sdk.yml) is what brings new APIs in.

``query_aws_api`` runs read-only operations (Describe*, List*, Get*, ...)
immediately. ``call_aws_api`` runs any operation and, being a mutating tool, is
always queued for operator approval first; the approval card shows the exact
service, operation and parameters. Both use the account-aware boto3 shim, so
they work in whichever account the request targets.
"""

import json
import re

import botocore
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


_TAGS_RE = re.compile(r"<[^>]+>")


def _doc(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", _TAGS_RE.sub(" ", text or "")).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


_MAX_DESCRIBE_CHARS = 12000


def _shape(shape, depth: int = 0, seen: frozenset = frozenset(), max_depth: int = 3,
           doc_chars: int = 160) -> object:
    """Compact, model-friendly description of a botocore shape."""
    kind = shape.type_name
    if kind == "structure":
        if depth >= max_depth or shape.name in seen:
            return {"type": "structure", "members": list(shape.members)}
        required = set(shape.required_members)
        return {
            name: {**({"required": True} if name in required else {}),
                   **({"doc": _doc(member.documentation, doc_chars)} if doc_chars else {}),
                   "shape": _shape(member, depth + 1, seen | {shape.name}, max_depth, doc_chars)}
            for name, member in shape.members.items()
        }
    if kind == "list":
        return {"type": "list", "of": _shape(shape.member, depth + 1, seen, max_depth, doc_chars)}
    if kind == "map":
        return {"type": "map", "key": shape.key.type_name,
                "value": _shape(shape.value, depth + 1, seen, max_depth, doc_chars)}
    desc = {"type": kind}
    if getattr(shape, "enum", None):
        desc["enum"] = shape.enum[:200]
        if len(shape.enum) > 200:
            desc["enum_truncated"] = f"{len(shape.enum)} values; first 200 shown"
    return desc


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


@tool
def list_aws_operations(service: str, keyword: str = '') -> str:
    """List the API operations an AWS service supports (optionally filtered by keyword), from the installed AWS SDK.

    Use it to find the exact operation name before describe_aws_operation / query_aws_api / call_aws_api.

    Args:
        service: boto3 client name, e.g. ec2, s3, rds, lambda, elbv2.
        keyword: Optional case-insensitive filter, e.g. 'peering' or 'snapshot'.

    Returns:
        JSON with the matching operation names (read-only ones flagged).
    """
    try:
        session = botocore.session.get_session()
        if service not in session.get_available_services():
            raise ValueError(f"Unknown AWS service '{service}'. Use the boto3 client name, e.g. ec2, s3, rds.")
        names = sorted(session.get_service_model(service).operation_names)
        if keyword:
            names = [n for n in names if keyword.lower().replace(" ", "") in n.lower()]
        return json.dumps({
            "service": service, "count": len(names),
            "operations": [{"name": n, "read_only": n.startswith(READ_PREFIXES)} for n in names[:200]],
            "truncated": len(names) > 200,
        })
    except Exception as e:
        return _error(e, service, "")


def _member(shape, path: str):
    """Follow a dotted parameter path (lists and maps are stepped through)."""
    for part in [p for p in path.split(".") if p]:
        while shape.type_name in ("list", "map"):
            shape = shape.member if shape.type_name == "list" else shape.value
        if shape.type_name != "structure" or part not in shape.members:
            raise ValueError(f"No parameter '{part}' in '{path}'.")
        shape = shape.members[part]
    return shape


@tool
def describe_aws_operation(service: str, operation: str, parameter: str = '') -> str:
    """Show an AWS API operation's exact current parameters (required fields, types, allowed values) from the installed AWS SDK.

    Call this before query_aws_api / call_aws_api whenever you are not certain of an
    operation's parameters, instead of guessing. Large operations are summarised;
    pass `parameter` (e.g. 'BlockDeviceMappings.Ebs') to see one nested parameter in full.

    Args:
        service: boto3 client name, e.g. ec2, s3, rds.
        operation: API operation, e.g. CreateVpcPeeringConnection or create_vpc_peering_connection.
        parameter: Optional dotted path of a nested parameter to describe in detail.

    Returns:
        JSON with the description, required parameters, the input shape and the output keys.
    """
    try:
        api_name = _operation(service, operation)
        op = botocore.session.get_session().get_service_model(service).operation_model(api_name)
        inputs = op.input_shape
        if inputs is not None and parameter:
            inputs = _member(inputs, parameter)
        result = {
            "service": service, "operation": api_name,
            "read_only": api_name.startswith(READ_PREFIXES),
            "description": _doc(op.documentation, 900) if not parameter else _doc(inputs.documentation, 900),
            "required": list(getattr(inputs, "required_members", []) or []) if inputs is not None else [],
            "parameters": {},
            "returns": list(op.output_shape.members) if op.output_shape and not parameter else [],
            "sdk_version": botocore.__version__,
        }
        if parameter:
            result["parameter"] = parameter
        # Shrink detail until it fits; nested parameters can be drilled into.
        for max_depth, doc_chars in ((3, 160), (2, 120), (2, 0), (1, 80), (1, 0)):
            result["parameters"] = _shape(inputs, max_depth=max_depth, doc_chars=doc_chars) if inputs is not None else {}
            text = json.dumps(result)
            if len(text) <= _MAX_DESCRIBE_CHARS:
                break
        if max_depth < 3:
            result["note"] = ("Summarised to fit. Call again with parameter='<Name>' (dotted for nested) "
                              "to see a parameter in full.")
            text = json.dumps(result)
        return text
    except Exception as e:
        return _error(e, service, operation)
