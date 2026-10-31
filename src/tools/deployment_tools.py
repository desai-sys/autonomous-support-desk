"""
AWS Code Deployment Tools

Covers Lambda, ECS/ECR, EC2, App Runner, S3 static sites, and CloudFormation/SAM
deployment operations. Follows the same @tool decorator pattern as compute_tools.py.
"""

from src.core.aws_clients import boto3  # account-aware boto3 shim (cross-account support)
import json
import time
import base64
import io
import zipfile
import mimetypes
import re
from datetime import datetime, timezone
from typing import Optional
from strands import tool
import structlog

logger = structlog.get_logger()


# ============================================================================
# Internal Helpers
# ============================================================================


def _generate_default_name() -> str:
    """Generate a default resource name using pattern cloudops-deploy-{timestamp}."""
    timestamp = str(int(time.time()))
    return f"cloudops-deploy-{timestamp}"


def _package_code_to_zip(code_content: str, filename: str) -> bytes:
    """Package code content into a zip archive as bytes.

    Args:
        code_content: The source code string to package.
        filename: The filename to use inside the zip archive.

    Returns:
        Bytes of the zip archive.
    """
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, code_content)
    return zip_buffer.getvalue()


def _get_content_type(filename: str) -> str:
    """Get the MIME content type for a filename based on its extension.

    Args:
        filename: The filename to determine content type for.

    Returns:
        MIME type string, defaults to 'application/octet-stream' if unknown.
    """
    # Custom mapping for common web files that mimetypes may not cover well
    extension_map = {
        '.html': 'text/html',
        '.htm': 'text/html',
        '.css': 'text/css',
        '.js': 'application/javascript',
        '.json': 'application/json',
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.ico': 'image/x-icon',
        '.woff': 'font/woff',
        '.woff2': 'font/woff2',
        '.ttf': 'font/ttf',
        '.eot': 'application/vnd.ms-fontobject',
        '.txt': 'text/plain',
        '.xml': 'application/xml',
        '.pdf': 'application/pdf',
        '.zip': 'application/zip',
        '.map': 'application/json',
        '.webp': 'image/webp',
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
    }

    # Extract extension
    dot_index = filename.rfind('.')
    if dot_index != -1:
        ext = filename[dot_index:].lower()
        if ext in extension_map:
            return extension_map[ext]

    # Fallback to mimetypes library
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or 'application/octet-stream'


def _upload_to_s3(bucket: str, key: str, body: bytes) -> str:
    """Upload bytes to S3 and return the S3 URI.

    Args:
        bucket: S3 bucket name.
        key: S3 object key.
        body: Bytes to upload.

    Returns:
        S3 URI string (s3://bucket/key) on success, or error string on failure.
    """
    try:
        s3 = boto3.client('s3')
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        return f"s3://{bucket}/{key}"
    except Exception as e:
        logger.error("s3_upload_failed", bucket=bucket, key=key, error=str(e))
        return f"error: {str(e)}"


def _build_manifest(
    target_type: str,
    status: str,
    resource_arn: str,
    region: str,
    details: dict,
    error: Optional[str] = None,
    rollback_info: Optional[dict] = None,
) -> str:
    """Build a deployment manifest JSON string.

    Args:
        target_type: One of lambda, ecs, ec2, apprunner, s3, cloudformation.
        status: One of success, failed, pending_confirmation.
        resource_arn: Primary resource ARN.
        region: AWS region.
        details: Target-specific metadata dict.
        error: Error message if status is failed.
        rollback_info: Info needed for rollback.

    Returns:
        JSON string of the deployment manifest.
    """
    try:
        # Get account ID
        sts = boto3.client('sts', region_name=region)
        account_id = sts.get_caller_identity()['Account']
    except Exception:
        account_id = "unknown"

    manifest = {
        "target_type": target_type,
        "status": status,
        "resource_arn": resource_arn,
        "region": region,
        "account_id": account_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": details,
    }

    if error:
        manifest["error"] = error
    if rollback_info:
        manifest["rollback_info"] = rollback_info

    return json.dumps(manifest, indent=2, default=str)


# ============================================================================
# Payload Handling Tools
# ============================================================================


@tool
def prepare_code_payload(content: str = '', source_type: str = 'inline', s3_source_path: str = '') -> str:
    """Extract and prepare code payload from inline text, base64-encoded content, or S3 reference.

    Args:
        content: The code content (inline text or base64 string). Can be empty when source_type is 's3'.
        source_type: One of 'inline', 'base64', or 's3'.
        s3_source_path: S3 URI (s3://bucket/key) when source_type is 's3'.

    Returns:
        JSON string with extracted code_content and metadata, or error details.
    """
    try:
        logger.info("prepare_code_payload", source_type=source_type)

        # Auto-detect source type from s3_source_path if content is empty
        if (not content or not content.strip()) and s3_source_path:
            source_type = 's3'

        if source_type == 'inline':
            if not content or not content.strip():
                return json.dumps({
                    "status": "failed",
                    "error_type": "invalid_payload",
                    "error_message": "Code payload is empty. Please provide valid code content.",
                    "suggestion": "Paste your code inline in the chat message."
                })
            return json.dumps({
                "status": "success",
                "source_type": "inline",
                "code_content": content,
                "size_bytes": len(content.encode('utf-8')),
            })

        elif source_type == 'base64':
            if not content or not content.strip():
                return json.dumps({
                    "status": "failed",
                    "error_type": "invalid_payload",
                    "error_message": "Base64 content is empty.",
                    "suggestion": "Provide a valid base64-encoded string."
                })
            try:
                decoded = base64.b64decode(content)
                # Try to decode as UTF-8 text; if it fails, return as binary info
                try:
                    text_content = decoded.decode('utf-8')
                    return json.dumps({
                        "status": "success",
                        "source_type": "base64",
                        "code_content": text_content,
                        "size_bytes": len(decoded),
                        "encoding": "utf-8",
                    })
                except UnicodeDecodeError:
                    # Binary content (likely a zip archive)
                    return json.dumps({
                        "status": "success",
                        "source_type": "base64",
                        "code_content": content,
                        "size_bytes": len(decoded),
                        "encoding": "binary",
                        "note": "Content is binary (likely a zip archive). Use raw decoded bytes for deployment.",
                    })
            except Exception as decode_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "invalid_payload",
                    "error_message": f"Failed to decode base64 content: {str(decode_err)}",
                    "suggestion": "Ensure the content is valid base64-encoded data."
                })

        elif source_type == 's3':
            if not s3_source_path:
                return json.dumps({
                    "status": "failed",
                    "error_type": "invalid_payload",
                    "error_message": "S3 source path is required when source_type is 's3'.",
                    "suggestion": "Provide s3_source_path in the format s3://bucket-name/key."
                })
            # Parse S3 URI
            s3_match = re.match(r's3://([^/]+)/(.+)', s3_source_path)
            if not s3_match:
                return json.dumps({
                    "status": "failed",
                    "error_type": "invalid_payload",
                    "error_message": f"Invalid S3 path format: {s3_source_path}",
                    "suggestion": "Use format: s3://bucket-name/path/to/file"
                })

            bucket = s3_match.group(1)
            key = s3_match.group(2)

            try:
                s3_client = boto3.client('s3')
                response = s3_client.get_object(Bucket=bucket, Key=key)
                body = response['Body'].read()

                # Try to decode as text
                try:
                    text_content = body.decode('utf-8')
                    return json.dumps({
                        "status": "success",
                        "source_type": "s3",
                        "code_content": text_content,
                        "size_bytes": len(body),
                        "s3_source": s3_source_path,
                        "encoding": "utf-8",
                    })
                except UnicodeDecodeError:
                    # Binary content
                    return json.dumps({
                        "status": "success",
                        "source_type": "s3",
                        "code_content": base64.b64encode(body).decode('ascii'),
                        "size_bytes": len(body),
                        "s3_source": s3_source_path,
                        "encoding": "binary",
                        "note": "Content is binary. Encoded as base64 for transport.",
                    })
            except Exception as s3_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to retrieve S3 object: {str(s3_err)}",
                    "s3_path": s3_source_path,
                    "suggestion": "Verify the S3 path exists and you have GetObject permissions."
                })

        else:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": f"Unsupported source_type: {source_type}",
                "suggestion": "Use 'inline', 'base64', or 's3' as source_type."
            })

    except Exception as e:
        logger.error("prepare_code_payload_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error preparing code payload: {str(e)}",
            "suggestion": "Check the input parameters and try again."
        })


@tool
def detect_code_language(code_content: str) -> str:
    """Detect the programming language or framework from code content using pattern matching.

    Args:
        code_content: The source code to analyze.

    Returns:
        JSON string with detected language, confidence, and suggested runtime.
    """
    try:
        logger.info("detect_code_language")

        if not code_content or not code_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Code content is empty. Cannot detect language.",
                "suggestion": "Provide non-empty code content for language detection."
            })

        # Language detection patterns (ordered by specificity)
        patterns = [
            # Python patterns
            {
                "language": "python",
                "runtime": "python3.12",
                "patterns": [
                    r'\bimport\s+\w+',
                    r'\bfrom\s+\w+\s+import\b',
                    r'\bdef\s+\w+\s*\(',
                    r'\bclass\s+\w+\s*[:\(]',
                    r'\bprint\s*\(',
                    r'\blambda\s+',
                    r'if\s+__name__\s*==\s*["\']__main__["\']',
                ],
                "handler": "lambda_function.lambda_handler",
            },
            # Node.js / JavaScript patterns
            {
                "language": "nodejs",
                "runtime": "nodejs20.x",
                "patterns": [
                    r'\brequire\s*\(',
                    r'\bconst\s+\w+\s*=',
                    r'\blet\s+\w+\s*=',
                    r'\bvar\s+\w+\s*=',
                    r'\bmodule\.exports\b',
                    r'\bexports\.\w+',
                    r'\bconsole\.log\s*\(',
                    r'\basync\s+function\b',
                    r'\bawait\s+',
                    r'=>\s*\{',
                ],
                "handler": "index.handler",
            },
            # TypeScript patterns
            {
                "language": "typescript",
                "runtime": "nodejs20.x",
                "patterns": [
                    r'\binterface\s+\w+\s*\{',
                    r'\btype\s+\w+\s*=',
                    r':\s*(string|number|boolean|any|void)\b',
                    r'\bimport\s+\{[^}]+\}\s+from\b',
                    r'\bexport\s+(default\s+)?(class|function|const|interface|type)\b',
                ],
                "handler": "index.handler",
            },
            # Go patterns
            {
                "language": "go",
                "runtime": "provided.al2023",
                "patterns": [
                    r'\bpackage\s+main\b',
                    r'\bfunc\s+\w+\s*\(',
                    r'\bimport\s*\(',
                    r'\bfmt\.\w+',
                    r'\bgo\s+func\b',
                ],
                "handler": "bootstrap",
            },
            # Java patterns
            {
                "language": "java",
                "runtime": "java21",
                "patterns": [
                    r'\bpublic\s+class\b',
                    r'\bprivate\s+\w+\s+\w+\s*[;=]',
                    r'\bimport\s+java\.',
                    r'\bSystem\.out\.print',
                    r'\bpublic\s+static\s+void\s+main\b',
                ],
                "handler": "com.example.Handler::handleRequest",
            },
            # Ruby patterns
            {
                "language": "ruby",
                "runtime": "ruby3.3",
                "patterns": [
                    r'\brequire\s+["\']',
                    r'\bdef\s+\w+\b',
                    r'\bclass\s+\w+\s*<',
                    r'\bend\b',
                    r'\bputs\s+',
                    r'\battr_accessor\b',
                ],
                "handler": "lambda_function.handler",
            },
            # Rust patterns
            {
                "language": "rust",
                "runtime": "provided.al2023",
                "patterns": [
                    r'\bfn\s+main\s*\(\)',
                    r'\blet\s+mut\s+',
                    r'\buse\s+\w+::',
                    r'\bimpl\s+\w+',
                    r'\bpub\s+fn\b',
                ],
                "handler": "bootstrap",
            },
            # Dockerfile (container deployment)
            {
                "language": "dockerfile",
                "runtime": "container",
                "patterns": [
                    r'^FROM\s+',
                    r'^RUN\s+',
                    r'^COPY\s+',
                    r'^CMD\s+',
                    r'^ENTRYPOINT\s+',
                    r'^EXPOSE\s+',
                ],
                "handler": None,
            },
            # CloudFormation / SAM template
            {
                "language": "cloudformation",
                "runtime": "cloudformation",
                "patterns": [
                    r'AWSTemplateFormatVersion',
                    r'Resources:',
                    r'AWS::',
                    r'Transform:\s*AWS::Serverless',
                ],
                "handler": None,
            },
            # HTML (static site)
            {
                "language": "html",
                "runtime": "static",
                "patterns": [
                    r'<!DOCTYPE\s+html',
                    r'<html',
                    r'<head>',
                    r'<body>',
                ],
                "handler": None,
            },
        ]

        best_match = None
        best_score = 0

        for lang_info in patterns:
            score = 0
            for pattern in lang_info["patterns"]:
                if re.search(pattern, code_content, re.MULTILINE | re.IGNORECASE):
                    score += 1
            if score > best_score:
                best_score = score
                best_match = lang_info

        if best_match and best_score > 0:
            confidence = min(best_score / len(best_match["patterns"]), 1.0)
            result = {
                "status": "success",
                "language": best_match["language"],
                "runtime": best_match["runtime"],
                "confidence": round(confidence, 2),
                "markers_matched": best_score,
                "total_markers": len(best_match["patterns"]),
            }
            if best_match["handler"]:
                result["suggested_handler"] = best_match["handler"]
            return json.dumps(result)
        else:
            return json.dumps({
                "status": "success",
                "language": "unknown",
                "runtime": "unknown",
                "confidence": 0.0,
                "markers_matched": 0,
                "note": "Could not determine language from content. Please specify the runtime explicitly."
            })

    except Exception as e:
        logger.error("detect_code_language_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Error detecting code language: {str(e)}",
            "suggestion": "Provide the runtime explicitly if auto-detection fails."
        })


def _extract_resource_name_from_details(details: str, target: str) -> Optional[str]:
    """Attempt to extract a specific resource name from the deployment details text.

    Looks for common naming patterns based on target type (function names, service names,
    instance IDs, bucket names, stack names, etc.).

    Args:
        details: The deployment details text provided by the operator.
        target: The deployment target type.

    Returns:
        Extracted resource name if found, or None.
    """
    if not details:
        return None

    # Patterns to extract resource names by target type
    patterns_by_target = {
        "lambda": [
            r'function[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
            r'(?:named?|called?)\s+["\']?([a-zA-Z0-9_-]+)["\']?',
            r'lambda[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
        ],
        "ecs": [
            r'service[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
            r'(?:named?|called?)\s+["\']?([a-zA-Z0-9_-]+)["\']?',
            r'cluster[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
        ],
        "ec2": [
            r'(i-[0-9a-f]{8,17})',
            r'instance[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
        ],
        "apprunner": [
            r'service[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
            r'(?:named?|called?)\s+["\']?([a-zA-Z0-9_-]+)["\']?',
        ],
        "s3": [
            r'bucket[:\s]+["\']?([a-zA-Z0-9._-]+)["\']?',
            r'(?:named?|called?)\s+["\']?([a-zA-Z0-9._-]+)["\']?',
        ],
        "cloudformation": [
            r'stack[:\s]+["\']?([a-zA-Z0-9_-]+)["\']?',
            r'(?:named?|called?)\s+["\']?([a-zA-Z0-9_-]+)["\']?',
        ],
    }

    target_patterns = patterns_by_target.get(target.lower(), [])

    for pattern in target_patterns:
        match = re.search(pattern, details, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


@tool
def create_deployment_plan(target: str, details: str, region: str = 'us-east-1') -> str:
    """Generate a structured deployment plan for operator confirmation before executing any deployment.

    This tool ALWAYS requires operator confirmation before any deployment proceeds.
    The plan includes the target AWS account ID, region, action type, resource details,
    and estimated impact. The agent must wait for explicit operator approval.

    Args:
        target: Deployment target type (lambda, ecs, ec2, apprunner, s3, cloudformation).
        details: Description of what will be deployed (resource name, code summary, configuration).
        region: AWS region for deployment.

    Returns:
        JSON string with the deployment plan requiring operator confirmation.
    """
    try:
        logger.info("create_deployment_plan", target=target, region=region)

        # Validate target
        valid_targets = ["lambda", "ecs", "ec2", "apprunner", "s3", "cloudformation"]
        if target.lower() not in valid_targets:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_target",
                "error_message": f"Invalid deployment target: {target}",
                "suggestion": f"Use one of: {', '.join(valid_targets)}"
            })

        # Validate region — must be non-empty
        if not region or not region.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "Region is required and cannot be empty.",
                "suggestion": "Provide a valid AWS region (e.g., 'us-east-1', 'eu-west-1')."
            })
        region = region.strip()

        # Get account ID with retry — never allow "unknown"
        account_id = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                sts = boto3.client('sts', region_name=region)
                identity = sts.get_caller_identity()
                account_id = identity['Account']
                if account_id:
                    break
            except Exception as sts_err:
                logger.warning(
                    "sts_get_caller_identity_retry",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    error=str(sts_err),
                )
                if attempt < max_retries - 1:
                    time.sleep(1 * (attempt + 1))  # Backoff: 1s, 2s

        if not account_id:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": (
                    "Unable to determine AWS account ID. "
                    "STS GetCallerIdentity failed after 3 attempts."
                ),
                "suggestion": (
                    "Verify that valid AWS credentials are configured and that the "
                    "execution role has sts:GetCallerIdentity permission."
                )
            })

        plan_id = f"deploy-plan-{int(time.time())}"

        # Determine action based on details
        action = "create"
        details_lower = details.lower()
        if "update" in details_lower or "existing" in details_lower:
            action = "update"
        elif "rollback" in details_lower or "revert" in details_lower:
            action = "rollback"

        # Extract resource name from details, or generate a default
        extracted_name = _extract_resource_name_from_details(details, target)
        resource_name = extracted_name if extracted_name else _generate_default_name()

        # Build changes summary based on target type
        target_summaries = {
            "lambda": "Deploy code as AWS Lambda function with default configuration (256MB memory, 30s timeout)",
            "ecs": "Build container image via CodeBuild, push to ECR, deploy to ECS Fargate service",
            "ec2": "Upload code to S3, deploy to EC2 instance via SSM Run Command",
            "apprunner": "Build and deploy to AWS App Runner with managed HTTPS endpoint",
            "s3": "Upload static files to S3 bucket with website hosting configuration",
            "cloudformation": "Deploy CloudFormation stack with provided template and parameters",
        }

        # Estimate impact
        impact_map = {
            "create": "No existing resources affected. New resources will be created.",
            "update": "Existing resource will be updated. Previous version will be available for rollback.",
            "rollback": "Current version will be replaced with the previous known-good state.",
        }

        plan = {
            "status": "pending_confirmation",
            "plan_id": plan_id,
            "target_type": target.lower(),
            "action": action,
            "account_id": account_id,
            "region": region,
            "resource_name": resource_name,
            "changes_summary": f"{target_summaries.get(target.lower(), 'Deploy to ' + target)}. Details: {details}",
            "estimated_impact": impact_map.get(action, "Impact assessment unavailable."),
            "requires_confirmation": True,
            "status_message": "Awaiting operator confirmation...",
            "message": "Please review this deployment plan and confirm to proceed. Reply 'yes' or 'confirm' to execute.",
        }

        return json.dumps(plan, indent=2)

    except Exception as e:
        logger.error("create_deployment_plan_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Error creating deployment plan: {str(e)}",
            "suggestion": "Check the target and details parameters and try again."
        })


# ============================================================================
# EC2 Deployment Tools
# ============================================================================


@tool
def check_ec2_ssm_status(instance_id: str, region: str = 'us-east-1') -> str:
    """Check whether an EC2 instance is reachable via AWS Systems Manager (SSM).

    Queries SSM for instance information and ping status to determine if the
    instance can receive Run Commands for code deployment.

    Args:
        instance_id: The EC2 instance ID to check (e.g., 'i-0abc123def456').
        region: AWS region where the instance resides.

    Returns:
        JSON string with SSM connectivity status and actionable guidance.
    """
    try:
        logger.info("check_ec2_ssm_status", instance_id=instance_id, region=region)

        if not instance_id or not instance_id.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "instance_id is required and cannot be empty.",
                "suggestion": "Provide a valid EC2 instance ID (e.g., 'i-0abc123def456')."
            })

        ssm_client = boto3.client('ssm', region_name=region)

        # Query SSM for the instance information filtered by instance ID
        response = ssm_client.describe_instance_information(
            Filters=[
                {
                    'Key': 'InstanceIds',
                    'Values': [instance_id.strip()]
                }
            ]
        )

        instance_info_list = response.get('InstanceInformationList', [])

        if not instance_info_list:
            # Instance not found in SSM — not managed or agent not installed
            return json.dumps({
                "status": "offline",
                "instance_id": instance_id,
                "ssm_managed": False,
                "ping_status": "NotAvailable",
                "message": (
                    f"Instance {instance_id} is not registered with AWS Systems Manager. "
                    "The SSM agent may not be installed or the instance may lack the required "
                    "IAM role (AmazonSSMManagedInstanceCore policy)."
                ),
                "suggestions": [
                    "Ensure the SSM agent is installed and running on the instance.",
                    "Attach an IAM instance profile with the AmazonSSMManagedInstanceCore policy.",
                    "Verify the instance has network connectivity to the SSM endpoint.",
                    "Check that the instance is in a running state."
                ]
            })

        # Instance found — check ping status
        instance_info = instance_info_list[0]
        ping_status = instance_info.get('PingStatus', 'Unknown')
        agent_version = instance_info.get('AgentVersion', 'Unknown')
        platform_type = instance_info.get('PlatformType', 'Unknown')
        platform_name = instance_info.get('PlatformName', 'Unknown')
        last_ping_time = instance_info.get('LastPingDateTime', None)

        if ping_status == 'Online':
            return json.dumps({
                "status": "online",
                "instance_id": instance_id,
                "ssm_managed": True,
                "ping_status": "Online",
                "agent_version": agent_version,
                "platform_type": platform_type,
                "platform_name": platform_name,
                "last_ping_time": str(last_ping_time) if last_ping_time else None,
                "message": (
                    f"Instance {instance_id} is online and reachable via SSM. "
                    "Ready for code deployment via Run Command."
                )
            })
        else:
            # Instance is registered but not currently responding
            return json.dumps({
                "status": "offline",
                "instance_id": instance_id,
                "ssm_managed": True,
                "ping_status": ping_status,
                "agent_version": agent_version,
                "platform_type": platform_type,
                "platform_name": platform_name,
                "last_ping_time": str(last_ping_time) if last_ping_time else None,
                "message": (
                    f"Instance {instance_id} is registered with SSM but currently not responding "
                    f"(ping status: {ping_status}). The SSM agent may be stopped or the instance "
                    "may be in a stopped/terminated state."
                ),
                "suggestions": [
                    "Verify the instance is in a running state.",
                    "Restart the SSM agent on the instance.",
                    "Check instance system logs for agent errors.",
                    "Ensure the instance has outbound connectivity to the SSM endpoint."
                ]
            })

    except Exception as e:
        logger.error("check_ec2_ssm_status_error", instance_id=instance_id, error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "aws_api_error",
            "instance_id": instance_id,
            "error_message": f"Failed to check SSM status for instance {instance_id}: {str(e)}",
            "suggestion": "Verify the instance ID is correct and you have ssm:DescribeInstanceInformation permission."
        })


# ============================================================================
# Lambda Deployment Tools
# ============================================================================


@tool
def deploy_lambda_function(
    code_content: str,
    function_name: str = '',
    runtime: str = 'python3.12',
    handler: str = 'lambda_function.lambda_handler',
    region: str = 'us-east-1',
) -> str:
    """Package and deploy code to AWS Lambda. Creates a new function or updates an existing one.

    Args:
        code_content: The source code to deploy.
        function_name: Lambda function name. If empty, a default name is generated.
        runtime: Lambda runtime (e.g. python3.12, nodejs20.x).
        handler: Handler entry point (e.g. lambda_function.lambda_handler).
        region: AWS region for deployment.

    Returns:
        JSON string with deployment manifest on success, or error details on failure.
    """
    try:
        logger.info("deploy_lambda_function", function_name=function_name, runtime=runtime, region=region)

        # Validate code content
        if not code_content or not code_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Code content is empty. Cannot deploy an empty function.",
                "suggestion": "Provide valid source code for the Lambda function."
            })

        # Generate default name if not provided
        if not function_name:
            function_name = _generate_default_name()

        # Determine filename from runtime
        if runtime.startswith('python'):
            filename = 'lambda_function.py'
        elif runtime.startswith('nodejs'):
            filename = 'index.js'
        else:
            filename = 'handler.txt'

        # Package code into zip
        zip_bytes = _package_code_to_zip(code_content, filename)
        code_size = len(zip_bytes)

        # 50MB threshold for S3 fallback
        max_direct_upload = 50 * 1024 * 1024

        lambda_client = boto3.client('lambda', region_name=region)

        # Check if function already exists
        function_exists = False
        try:
            lambda_client.get_function(FunctionName=function_name)
            function_exists = True
        except lambda_client.exceptions.ResourceNotFoundException:
            function_exists = False
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Error checking if function exists: {str(e)}",
                "suggestion": "Verify the function name and AWS credentials, then try again."
            })

        # Determine deployment method (direct zip or S3)
        s3_staging_key = None
        if code_size > max_direct_upload:
            # Upload to S3 first
            staging_bucket = f"cloudops-deployments-{region}"
            s3_staging_key = f"deployments/lambda/{function_name}/{int(time.time())}.zip"
            s3_result = _upload_to_s3(staging_bucket, s3_staging_key, zip_bytes)
            if s3_result.startswith("error:"):
                return json.dumps({
                    "status": "failed",
                    "error_type": "s3_upload_error",
                    "error_message": f"Failed to upload large package to S3: {s3_result}",
                    "suggestion": "Ensure the S3 staging bucket exists and you have PutObject permissions."
                })
            code_param = {"S3Bucket": staging_bucket, "S3Key": s3_staging_key}
        else:
            code_param = {"ZipFile": zip_bytes}

        if function_exists:
            # Update existing function code
            try:
                update_response = lambda_client.update_function_code(
                    FunctionName=function_name,
                    **code_param,
                )

                # Wait briefly for update to propagate, then publish version
                try:
                    version_response = lambda_client.publish_version(
                        FunctionName=function_name,
                        Description=f"Deployed via CloudOps at {datetime.now(timezone.utc).isoformat()}",
                    )
                    version = version_response.get('Version', '$LATEST')
                except Exception:
                    version = '$LATEST'

                function_arn = update_response.get('FunctionArn', '')
                code_sha256 = update_response.get('CodeSha256', '')

                details = {
                    "function_arn": function_arn,
                    "version": version,
                    "runtime": runtime,
                    "code_size_bytes": code_size,
                    "code_sha256": code_sha256,
                    "action": "updated",
                }
                if s3_staging_key:
                    details["s3_staging_key"] = s3_staging_key

                return _build_manifest(
                    target_type="lambda",
                    status="success",
                    resource_arn=function_arn,
                    region=region,
                    details=details,
                    rollback_info={"function_name": function_name, "previous_version": version},
                )

            except Exception as e:
                error_msg = str(e)
                logger.error("lambda_update_failed", function_name=function_name, error=error_msg)
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to update Lambda function '{function_name}': {error_msg}",
                    "target_type": "lambda",
                    "suggestion": "Check that the function exists and the runtime is supported. "
                                  "Verify IAM permissions include lambda:UpdateFunctionCode and lambda:PublishVersion."
                })

        else:
            # Create new function
            try:
                # Use a default execution role
                sts_client = boto3.client('sts', region_name=region)
                account_id = sts_client.get_caller_identity()['Account']
                default_role = f"arn:aws:iam::{account_id}:role/cloudops-lambda-execution-role"

                create_response = lambda_client.create_function(
                    FunctionName=function_name,
                    Runtime=runtime,
                    Role=default_role,
                    Handler=handler,
                    Code=code_param,
                    Description=f"Deployed via CloudOps at {datetime.now(timezone.utc).isoformat()}",
                    Timeout=30,
                    MemorySize=256,
                    Publish=True,
                )

                function_arn = create_response.get('FunctionArn', '')
                version = create_response.get('Version', '$LATEST')
                code_sha256 = create_response.get('CodeSha256', '')

                details = {
                    "function_arn": function_arn,
                    "version": version,
                    "runtime": runtime,
                    "code_size_bytes": code_size,
                    "code_sha256": code_sha256,
                    "action": "created",
                    "memory_mb": 256,
                    "timeout_seconds": 30,
                }
                if s3_staging_key:
                    details["s3_staging_key"] = s3_staging_key

                return _build_manifest(
                    target_type="lambda",
                    status="success",
                    resource_arn=function_arn,
                    region=region,
                    details=details,
                    rollback_info={"function_name": function_name, "version": version},
                )

            except Exception as e:
                error_msg = str(e)
                logger.error("lambda_create_failed", function_name=function_name, error=error_msg)
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create Lambda function '{function_name}': {error_msg}",
                    "target_type": "lambda",
                    "suggestion": "Ensure the IAM role 'cloudops-lambda-execution-role' exists in your account. "
                                  "Verify the runtime is supported and code content is valid. "
                                  "Check IAM permissions include lambda:CreateFunction."
                })

    except Exception as e:
        logger.error("deploy_lambda_function_unexpected_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during Lambda deployment: {str(e)}",
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


@tool
def rollback_lambda_function(function_name: str, target_version: str = '', region: str = 'us-east-1') -> str:
    """Rollback a Lambda function to a previous published version.

    Lists function versions, identifies the previous published version (or uses a
    specified target_version), and updates the function code to that version's code.

    Args:
        function_name: The Lambda function name to roll back.
        target_version: Specific version number to roll back to. If empty, uses the
                        second-most-recent published version (not $LATEST).
        region: AWS region where the function is deployed.

    Returns:
        JSON string with deployment manifest confirming the rolled-back state,
        or error details if rollback cannot be performed.
    """
    try:
        logger.info("rollback_lambda_function", function_name=function_name, region=region,
                    target_version=target_version)

        # Validate function name
        if not function_name or not function_name.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "function_name is required and cannot be empty.",
                "suggestion": "Provide the name of the Lambda function you want to roll back."
            })

        lambda_client = boto3.client('lambda', region_name=region)

        # Verify the function exists
        try:
            lambda_client.get_function(FunctionName=function_name)
        except lambda_client.exceptions.ResourceNotFoundException:
            return json.dumps({
                "status": "failed",
                "error_type": "resource_not_found",
                "error_message": f"Lambda function '{function_name}' does not exist in {region}.",
                "suggestion": "Verify the function name is correct and check the region. "
                              "Use list_lambda_functions to see available functions."
            })
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Error checking function '{function_name}': {str(e)}",
                "suggestion": "Verify AWS credentials and permissions (lambda:GetFunction)."
            })

        # List all published versions (excluding $LATEST)
        try:
            versions = []
            paginator = lambda_client.get_paginator('list_versions_by_function')
            for page in paginator.paginate(FunctionName=function_name):
                for version_info in page.get('Versions', []):
                    ver = version_info.get('Version', '')
                    if ver != '$LATEST':
                        versions.append(version_info)
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to list versions for '{function_name}': {str(e)}",
                "suggestion": "Verify IAM permissions include lambda:ListVersionsByFunction."
            })

        # Check if there are enough versions to roll back
        if len(versions) < 2 and not target_version:
            return json.dumps({
                "status": "failed",
                "error_type": "no_previous_version",
                "error_message": (
                    f"Cannot rollback '{function_name}': no previous published version exists. "
                    f"Found {len(versions)} published version(s)."
                ),
                "suggestion": (
                    "A rollback requires at least two published versions. Suggestions:\n"
                    "- Redeploy the function with corrected code using deploy_lambda_function.\n"
                    "- If you have the original code, deploy it as a new version.\n"
                    "- Check if the code is available in your S3 staging bucket or source control."
                ),
                "function_name": function_name,
                "versions_found": len(versions),
            })

        # Sort versions by version number (numeric) in descending order
        versions.sort(key=lambda v: int(v['Version']), reverse=True)

        # Determine the target rollback version
        if target_version:
            # Use the specified target version
            target_version_info = None
            for v in versions:
                if v['Version'] == target_version:
                    target_version_info = v
                    break

            if not target_version_info:
                available = [v['Version'] for v in versions[:10]]
                return json.dumps({
                    "status": "failed",
                    "error_type": "invalid_version",
                    "error_message": (
                        f"Target version '{target_version}' not found for function '{function_name}'."
                    ),
                    "suggestion": (
                        f"Available versions (most recent first): {', '.join(available)}. "
                        "Specify a valid published version number."
                    ),
                    "function_name": function_name,
                    "available_versions": available,
                })
        else:
            # Use the second-most-recent published version
            target_version_info = versions[1]
            target_version = target_version_info['Version']

        # Get the code location for the target version
        try:
            version_detail = lambda_client.get_function(
                FunctionName=function_name,
                Qualifier=target_version
            )
            code_location = version_detail.get('Code', {}).get('Location', '')
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": (
                    f"Failed to get code for version {target_version}: {str(e)}"
                ),
                "suggestion": "Verify the version still exists and has not been deleted."
            })

        # Download the code from the presigned URL and update the function
        try:
            import urllib.request
            response = urllib.request.urlopen(code_location)
            code_bytes = response.read()
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "code_download_error",
                "error_message": (
                    f"Failed to download code for version {target_version}: {str(e)}"
                ),
                "suggestion": "The code location URL may have expired. Try again or deploy fresh code."
            })

        # Update the function code with the previous version's code
        try:
            update_response = lambda_client.update_function_code(
                FunctionName=function_name,
                ZipFile=code_bytes,
            )

            # Publish the rolled-back code as a new version
            try:
                publish_response = lambda_client.publish_version(
                    FunctionName=function_name,
                    Description=f"Rollback to version {target_version} via CloudOps at {datetime.now(timezone.utc).isoformat()}",
                )
                new_version = publish_response.get('Version', '$LATEST')
            except Exception:
                new_version = '$LATEST'

            function_arn = update_response.get('FunctionArn', '')
            code_sha256 = update_response.get('CodeSha256', '')
            code_size = update_response.get('CodeSize', 0)
            runtime = update_response.get('Runtime', 'unknown')

            details = {
                "function_arn": function_arn,
                "version": new_version,
                "runtime": runtime,
                "code_size_bytes": code_size,
                "code_sha256": code_sha256,
                "action": "rollback",
                "rolled_back_to_version": target_version,
                "previous_latest_version": versions[0]['Version'] if versions else "unknown",
            }

            return _build_manifest(
                target_type="lambda",
                status="success",
                resource_arn=function_arn,
                region=region,
                details=details,
                rollback_info={
                    "function_name": function_name,
                    "restored_from_version": target_version,
                    "new_version": new_version,
                },
            )

        except Exception as e:
            error_msg = str(e)
            logger.error("lambda_rollback_update_failed", function_name=function_name,
                         target_version=target_version, error=error_msg)
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to update function code during rollback: {error_msg}",
                "target_type": "lambda",
                "function_name": function_name,
                "target_version": target_version,
                "suggestion": "Verify IAM permissions include lambda:UpdateFunctionCode and lambda:PublishVersion."
            })

    except Exception as e:
        logger.error("rollback_lambda_function_unexpected_error", function_name=function_name, error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during Lambda rollback: {str(e)}",
            "target_type": "lambda",
            "function_name": function_name,
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


@tool
def deploy_to_ec2(code_content: str, instance_id: str, deploy_path: str = '', region: str = 'us-east-1') -> str:
    """Upload code to S3 and deploy to an EC2 instance via SSM Run Command.

    Stages the code as a zip archive in an S3 bucket, then uses AWS Systems Manager
    Run Command to pull the archive from S3 and extract it to the target path on
    the EC2 instance.

    Args:
        code_content: The source code to deploy.
        instance_id: The target EC2 instance ID (e.g., i-0abc123def456).
        deploy_path: Target path on the instance. Defaults to /opt/cloudops/deployments/{timestamp}/.
        region: AWS region for deployment.

    Returns:
        JSON string with deployment manifest or error details.
    """
    try:
        logger.info("deploy_to_ec2", instance_id=instance_id, region=region)

        # Validate inputs
        if not code_content or not code_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Code content is empty. Please provide valid code to deploy.",
                "suggestion": "Paste your code inline or provide a base64-encoded archive."
            })

        if not instance_id or not instance_id.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Instance ID is required for EC2 deployment.",
                "suggestion": "Provide a valid EC2 instance ID (e.g., i-0abc123def456)."
            })

        # Generate timestamp for this deployment
        timestamp = str(int(time.time()))

        # Set default deploy path if not specified
        if not deploy_path or not deploy_path.strip():
            deploy_path = f"/opt/cloudops/deployments/{timestamp}/"

        # Ensure deploy_path ends with /
        if not deploy_path.endswith('/'):
            deploy_path += '/'

        # Get AWS account ID for the staging bucket name
        try:
            sts_client = boto3.client('sts', region_name=region)
            account_id = sts_client.get_caller_identity()['Account']
        except Exception as sts_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to get AWS account ID: {str(sts_err)}",
                "suggestion": "Verify AWS credentials are configured and have STS permissions."
            })

        # Package code into a zip archive
        zip_bytes = _package_code_to_zip(code_content, "deploy_payload.py")

        # Upload to S3 staging bucket
        staging_bucket = f"cloudops-deployments-{account_id}"
        s3_key = f"ec2/{instance_id}/{timestamp}.zip"

        try:
            s3_client = boto3.client('s3', region_name=region)
            s3_client.put_object(
                Bucket=staging_bucket,
                Key=s3_key,
                Body=zip_bytes
            )
            s3_artifact_location = f"s3://{staging_bucket}/{s3_key}"
        except Exception as s3_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to upload code to S3 staging bucket: {str(s3_err)}",
                "target_type": "ec2",
                "suggestion": f"Verify the S3 bucket '{staging_bucket}' exists and you have PutObject permissions."
            })

        # Execute SSM Run Command to pull from S3 and deploy
        ssm_commands = [
            f"mkdir -p {deploy_path}",
            f"aws s3 cp {s3_artifact_location} /tmp/deploy_{timestamp}.zip",
            f"unzip -o /tmp/deploy_{timestamp}.zip -d {deploy_path}",
            f"rm -f /tmp/deploy_{timestamp}.zip",
            f"echo 'Deployment completed to {deploy_path}'"
        ]

        try:
            ssm_client = boto3.client('ssm', region_name=region)
            ssm_response = ssm_client.send_command(
                InstanceIds=[instance_id],
                DocumentName='AWS-RunShellScript',
                Parameters={
                    'commands': ssm_commands,
                    'executionTimeout': ['300']
                },
                Comment=f"CloudOps deployment to {deploy_path}",
                TimeoutSeconds=600
            )
            ssm_command_id = ssm_response['Command']['CommandId']
        except Exception as ssm_err:
            error_message = str(ssm_err)
            # Check for common SSM errors
            if 'InvalidInstanceId' in error_message:
                suggestion = (
                    "The instance may not have the SSM agent running or may not be registered with SSM. "
                    "Use check_ec2_ssm_status to verify, or ensure the instance has the AmazonSSMManagedInstanceCore IAM policy attached."
                )
            else:
                suggestion = "Verify the instance ID is correct and that the SSM agent is running on the target instance."

            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to execute SSM Run Command: {error_message}",
                "target_type": "ec2",
                "instance_id": instance_id,
                "suggestion": suggestion
            })

        # Wait briefly and check command status
        try:
            # Give SSM a moment to start execution
            time.sleep(2)

            invocation_response = ssm_client.get_command_invocation(
                CommandId=ssm_command_id,
                InstanceId=instance_id
            )

            command_status = invocation_response.get('Status', 'Pending')

            # If the command already failed, report the error
            if command_status in ('Failed', 'Cancelled', 'TimedOut'):
                return json.dumps({
                    "status": "failed",
                    "error_type": "ssm_command_failed",
                    "error_message": f"SSM command execution failed with status: {command_status}",
                    "target_type": "ec2",
                    "instance_id": instance_id,
                    "ssm_command_id": ssm_command_id,
                    "command_output": invocation_response.get('StandardOutputContent', ''),
                    "error_output": invocation_response.get('StandardErrorContent', ''),
                    "exit_code": invocation_response.get('ResponseCode', -1),
                    "suggestion": "Review the command output and error details above. Check instance permissions and disk space."
                })

        except ssm_client.exceptions.InvocationDoesNotExist:
            # Command still pending - this is normal for async execution
            command_status = "InProgress"
        except Exception:
            # Non-critical - we still have the command ID for later checking
            command_status = "Pending"

        # Build and return success manifest
        details = {
            "instance_id": instance_id,
            "s3_artifact_location": s3_artifact_location,
            "deployment_path": deploy_path,
            "ssm_command_id": ssm_command_id,
            "command_status": command_status,
            "code_size_bytes": len(zip_bytes),
        }

        return _build_manifest(
            target_type="ec2",
            status="success",
            resource_arn=f"arn:aws:ec2:{region}:{account_id}:instance/{instance_id}",
            region=region,
            details=details,
            rollback_info={
                "deployment_path": deploy_path,
                "s3_artifact": s3_artifact_location,
                "note": "To rollback, remove the deployed files or deploy a previous version."
            }
        )

    except Exception as e:
        logger.error("deploy_to_ec2_error", error=str(e), instance_id=instance_id)
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during EC2 deployment: {str(e)}",
            "target_type": "ec2",
            "instance_id": instance_id,
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


# ============================================================================
# S3 Static Site Deployment Tools
# ============================================================================


@tool
def deploy_static_site(
    files_content: str,
    bucket_name: str = '',
    enable_cloudfront: bool = False,
    region: str = 'us-east-1',
) -> str:
    """Upload static website files to an S3 bucket with hosting enabled and optional CloudFront distribution.

    Parses a JSON map of filename→content, creates the S3 bucket with static website
    hosting if it does not exist, uploads each file with the correct content-type header,
    and optionally creates a CloudFront distribution.

    Args:
        files_content: JSON string mapping filename to file content (e.g. '{"index.html": "<html>...</html>"}').
        bucket_name: Target S3 bucket name. If empty, generates 'cloudops-static-{timestamp}'.
        enable_cloudfront: Whether to create/update a CloudFront distribution for the site.
        region: AWS region for the S3 bucket.

    Returns:
        JSON string with deployment manifest including bucket_name, website_endpoint,
        cloudfront_domain (if applicable), files_uploaded count, and any partial failures.
    """
    try:
        logger.info("deploy_static_site", bucket_name=bucket_name, enable_cloudfront=enable_cloudfront, region=region)

        # 1. Parse files_content JSON string into a dict
        if not files_content or not files_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "files_content is empty. Provide a JSON map of filename to content.",
                "suggestion": "Pass a JSON string like '{\"index.html\": \"<html>...</html>\", \"style.css\": \"body{}\"}'"
            })

        try:
            files_map = json.loads(files_content)
        except json.JSONDecodeError as parse_err:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": f"Failed to parse files_content as JSON: {str(parse_err)}",
                "suggestion": "Ensure files_content is a valid JSON string mapping filenames to their content."
            })

        if not isinstance(files_map, dict) or not files_map:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "files_content must be a non-empty JSON object mapping filenames to content.",
                "suggestion": "Provide at least one file, e.g. '{\"index.html\": \"<html></html>\"}'"
            })

        # 2. Generate bucket name if not provided
        if not bucket_name or not bucket_name.strip():
            timestamp = str(int(time.time()))
            bucket_name = f"cloudops-static-{timestamp}"
        else:
            bucket_name = bucket_name.strip()

        # 3. Create bucket with static website hosting if it doesn't exist
        s3_client = boto3.client('s3', region_name=region)

        bucket_exists = False
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            bucket_exists = True
        except Exception:
            bucket_exists = False

        if not bucket_exists:
            try:
                # Create the bucket
                create_params = {'Bucket': bucket_name}
                # LocationConstraint is required for all regions except us-east-1
                if region != 'us-east-1':
                    create_params['CreateBucketConfiguration'] = {
                        'LocationConstraint': region
                    }
                s3_client.create_bucket(**create_params)

                # Enable static website hosting with index.html as IndexDocument
                s3_client.put_bucket_website(
                    Bucket=bucket_name,
                    WebsiteConfiguration={
                        'IndexDocument': {'Suffix': 'index.html'},
                        'ErrorDocument': {'Key': 'error.html'},
                    }
                )

                # Set bucket policy to allow public read (required for static hosting)
                bucket_policy = {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "PublicReadGetObject",
                            "Effect": "Allow",
                            "Principal": "*",
                            "Action": "s3:GetObject",
                            "Resource": f"arn:aws:s3:::{bucket_name}/*"
                        }
                    ]
                }
                s3_client.put_bucket_policy(
                    Bucket=bucket_name,
                    Policy=json.dumps(bucket_policy)
                )

            except Exception as bucket_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create S3 bucket '{bucket_name}': {str(bucket_err)}",
                    "suggestion": "Check that the bucket name is globally unique and valid. "
                                  "Verify IAM permissions include s3:CreateBucket and s3:PutBucketWebsite."
                })

        # 4. Upload each file with correct content-type, tracking successes and failures
        succeeded_files = []
        failed_files = []
        total_size_bytes = 0

        for filename, content in files_map.items():
            try:
                content_type = _get_content_type(filename)

                # Encode content to bytes
                if isinstance(content, str):
                    body = content.encode('utf-8')
                elif isinstance(content, bytes):
                    body = content
                else:
                    body = str(content).encode('utf-8')

                s3_client.put_object(
                    Bucket=bucket_name,
                    Key=filename,
                    Body=body,
                    ContentType=content_type,
                )

                total_size_bytes += len(body)
                succeeded_files.append(filename)

            except Exception as upload_err:
                failed_files.append({
                    "filename": filename,
                    "error": str(upload_err)
                })

        # 5. If all files failed, report failure
        if not succeeded_files:
            return json.dumps({
                "status": "failed",
                "error_type": "upload_error",
                "error_message": "All file uploads failed.",
                "failed_files": failed_files,
                "suggestion": "Check S3 permissions and bucket configuration."
            })

        # 6. Build website endpoint
        if region == 'us-east-1':
            website_endpoint = f"http://{bucket_name}.s3-website-{region}.amazonaws.com"
        else:
            website_endpoint = f"http://{bucket_name}.s3-website.{region}.amazonaws.com"

        # 7. Optionally create/update CloudFront distribution
        cloudfront_domain = None
        if enable_cloudfront:
            try:
                cf_client = boto3.client('cloudfront', region_name=region)

                # Create a CloudFront distribution with the S3 website as origin
                distribution_config = {
                    'CallerReference': f"cloudops-static-{bucket_name}-{int(time.time())}",
                    'Comment': f"CloudOps static site distribution for {bucket_name}",
                    'Enabled': True,
                    'Origins': {
                        'Quantity': 1,
                        'Items': [
                            {
                                'Id': f"S3-{bucket_name}",
                                'DomainName': f"{bucket_name}.s3-website-{region}.amazonaws.com",
                                'CustomOriginConfig': {
                                    'HTTPPort': 80,
                                    'HTTPSPort': 443,
                                    'OriginProtocolPolicy': 'http-only',
                                }
                            }
                        ]
                    },
                    'DefaultCacheBehavior': {
                        'TargetOriginId': f"S3-{bucket_name}",
                        'ViewerProtocolPolicy': 'redirect-to-https',
                        'AllowedMethods': {
                            'Quantity': 2,
                            'Items': ['GET', 'HEAD'],
                        },
                        'ForwardedValues': {
                            'QueryString': False,
                            'Cookies': {'Forward': 'none'},
                        },
                        'MinTTL': 0,
                        'DefaultTTL': 86400,
                        'MaxTTL': 31536000,
                    },
                    'DefaultRootObject': 'index.html',
                }

                cf_response = cf_client.create_distribution(
                    DistributionConfig=distribution_config
                )
                cloudfront_domain = cf_response['Distribution']['DomainName']

            except Exception as cf_err:
                # CloudFront failure is not fatal — report it but continue
                logger.warning("cloudfront_creation_failed", error=str(cf_err))
                cloudfront_domain = None

        # 8. Return manifest with results
        details = {
            "bucket_name": bucket_name,
            "website_endpoint": website_endpoint,
            "files_uploaded": len(succeeded_files),
            "total_size_bytes": total_size_bytes,
        }

        if cloudfront_domain:
            details["cloudfront_domain"] = cloudfront_domain

        if failed_files:
            details["partial_failures"] = failed_files
            details["succeeded_files"] = succeeded_files

        return _build_manifest(
            target_type="s3",
            status="success",
            resource_arn=f"arn:aws:s3:::{bucket_name}",
            region=region,
            details=details,
            rollback_info={
                "bucket_name": bucket_name,
                "files_uploaded": succeeded_files,
                "note": "To rollback, delete the uploaded files from the bucket."
            }
        )

    except Exception as e:
        logger.error("deploy_static_site_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during static site deployment: {str(e)}",
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


# ============================================================================
# App Runner Deployment Tools
# ============================================================================


@tool
def deploy_apprunner_source(
    code_content: str,
    runtime: str,
    build_command: str,
    start_command: str,
    service_name: str = '',
    region: str = 'us-east-1',
) -> str:
    """Deploy application code to AWS App Runner using source-based configuration.

    Uploads source code to an S3 staging bucket, then creates or updates an App Runner
    service configured for source-code deployment with the specified runtime, build command,
    and start command. Supports Python 3 and Node.js 16+ runtimes.

    Args:
        code_content: The source code to deploy.
        runtime: Runtime environment (e.g., 'PYTHON_3', 'NODEJS_16', 'NODEJS_18').
        build_command: Command to build the application (e.g., 'pip install -r requirements.txt').
        start_command: Command to start the application (e.g., 'python app.py').
        service_name: App Runner service name. If empty, a default name is generated.
        region: AWS region for deployment.

    Returns:
        JSON string with deployment manifest containing service_arn, service_url, status,
        and source configuration details, or error details on failure.
    """
    try:
        logger.info("deploy_apprunner_source", service_name=service_name, runtime=runtime, region=region)

        # Validate code content
        if not code_content or not code_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Code content is empty. Cannot deploy an empty application.",
                "suggestion": "Provide valid source code for the App Runner service."
            })

        # Validate runtime
        supported_runtimes = ['PYTHON_3', 'NODEJS_16', 'NODEJS_18', 'NODEJS_20']
        runtime_upper = runtime.upper().replace(' ', '_').replace('.', '_')
        # Normalize common variations
        runtime_map = {
            'PYTHON_3': 'PYTHON_3',
            'PYTHON3': 'PYTHON_3',
            'PYTHON_3_12': 'PYTHON_3',
            'PYTHON_3_11': 'PYTHON_3',
            'PYTHON_3_10': 'PYTHON_3',
            'NODEJS_16': 'NODEJS_16',
            'NODEJS16': 'NODEJS_16',
            'NODE_16': 'NODEJS_16',
            'NODEJS_18': 'NODEJS_18',
            'NODEJS18': 'NODEJS_18',
            'NODE_18': 'NODEJS_18',
            'NODEJS_20': 'NODEJS_20',
            'NODEJS20': 'NODEJS_20',
            'NODE_20': 'NODEJS_20',
        }

        normalized_runtime = runtime_map.get(runtime_upper)
        if not normalized_runtime:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": f"Unsupported runtime: '{runtime}'. Supported runtimes are Python 3 and Node.js 16+.",
                "supported_runtimes": supported_runtimes,
                "suggestion": "Use one of: PYTHON_3, NODEJS_16, NODEJS_18, NODEJS_20."
            })

        # Validate build and start commands
        if not build_command or not build_command.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "build_command is required for source-based App Runner deployment.",
                "suggestion": "Provide a build command (e.g., 'pip install -r requirements.txt' for Python)."
            })

        if not start_command or not start_command.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "start_command is required for source-based App Runner deployment.",
                "suggestion": "Provide a start command (e.g., 'python app.py' for Python, 'node index.js' for Node.js)."
            })

        # Generate default service name if not provided
        if not service_name or not service_name.strip():
            service_name = _generate_default_name()

        # Get AWS account ID
        try:
            sts_client = boto3.client('sts', region_name=region)
            account_id = sts_client.get_caller_identity()['Account']
        except Exception as sts_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to get AWS account ID: {str(sts_err)}",
                "suggestion": "Verify AWS credentials are configured and have STS permissions."
            })

        # Upload source code to S3 staging bucket
        timestamp = str(int(time.time()))
        staging_bucket = f"cloudops-deployments-{account_id}"
        s3_key = f"deployments/apprunner/{service_name}/{timestamp}/source.zip"

        # Determine filename based on runtime
        if normalized_runtime == 'PYTHON_3':
            filename = 'app.py'
        else:
            filename = 'index.js'

        zip_bytes = _package_code_to_zip(code_content, filename)

        try:
            s3_client = boto3.client('s3', region_name=region)
            s3_client.put_object(
                Bucket=staging_bucket,
                Key=s3_key,
                Body=zip_bytes,
            )
            s3_location = f"s3://{staging_bucket}/{s3_key}"
        except Exception as s3_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to upload source code to S3 staging bucket: {str(s3_err)}",
                "target_type": "apprunner",
                "suggestion": f"Verify the S3 bucket '{staging_bucket}' exists and you have PutObject permissions."
            })

        # Create App Runner client
        apprunner_client = boto3.client('apprunner', region_name=region)

        # Check if the App Runner service already exists
        service_exists = False
        existing_service_arn = None
        try:
            list_response = apprunner_client.list_services()
            for svc in list_response.get('ServiceSummaryList', []):
                if svc.get('ServiceName') == service_name:
                    service_exists = True
                    existing_service_arn = svc.get('ServiceArn')
                    break
        except Exception as list_err:
            logger.warning("apprunner_list_services_failed", error=str(list_err))
            # Non-fatal: proceed with create attempt

        # Build the source configuration for App Runner
        source_configuration = {
            'CodeRepository': {
                'RepositoryUrl': s3_location,
                'SourceCodeVersion': {
                    'Type': 'BRANCH',
                    'Value': 'main',
                },
                'CodeConfiguration': {
                    'ConfigurationSource': 'API',
                    'CodeConfigurationValues': {
                        'Runtime': normalized_runtime,
                        'BuildCommand': build_command.strip(),
                        'StartCommand': start_command.strip(),
                        'Port': '8080',
                    },
                },
            },
            'AutoDeploymentsEnabled': False,
        }

        if service_exists and existing_service_arn:
            # Update existing App Runner service
            try:
                update_response = apprunner_client.update_service(
                    ServiceArn=existing_service_arn,
                    SourceConfiguration=source_configuration,
                )

                service_info = update_response.get('Service', {})
                service_arn = service_info.get('ServiceArn', existing_service_arn)
                service_url = service_info.get('ServiceUrl', '')
                status = service_info.get('Status', 'OPERATION_IN_PROGRESS')

                if service_url and not service_url.startswith('https://'):
                    service_url = f"https://{service_url}"

                details = {
                    "service_arn": service_arn,
                    "service_url": service_url,
                    "status": status,
                    "action": "updated",
                    "source_config": {
                        "runtime": normalized_runtime,
                        "build_command": build_command.strip(),
                        "start_command": start_command.strip(),
                        "s3_source": s3_location,
                    },
                }

                return _build_manifest(
                    target_type="apprunner",
                    status="success",
                    resource_arn=service_arn,
                    region=region,
                    details=details,
                    rollback_info={
                        "service_name": service_name,
                        "service_arn": service_arn,
                        "note": "App Runner maintains previous deployment for automatic rollback.",
                    },
                )

            except Exception as update_err:
                error_msg = str(update_err)
                logger.error("apprunner_update_failed", service_name=service_name, error=error_msg)

                # Attempt to retrieve CloudWatch log group for debugging
                log_group = f"/aws/apprunner/{service_name}"
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to update App Runner service '{service_name}': {error_msg}",
                    "target_type": "apprunner",
                    "cloudwatch_log_group": log_group,
                    "suggestion": (
                        f"Check CloudWatch logs at '{log_group}' for more details. "
                        "Verify the runtime, build command, and start command are correct."
                    ),
                })

        else:
            # Create new App Runner service
            try:
                create_response = apprunner_client.create_service(
                    ServiceName=service_name,
                    SourceConfiguration=source_configuration,
                    InstanceConfiguration={
                        'Cpu': '1024',
                        'Memory': '2048',
                    },
                    HealthCheckConfiguration={
                        'Protocol': 'TCP',
                        'Path': '/',
                        'Interval': 10,
                        'Timeout': 5,
                        'HealthyThreshold': 1,
                        'UnhealthyThreshold': 5,
                    },
                )

                service_info = create_response.get('Service', {})
                service_arn = service_info.get('ServiceArn', '')
                service_url = service_info.get('ServiceUrl', '')
                status = service_info.get('Status', 'CREATE_IN_PROGRESS')

                if service_url and not service_url.startswith('https://'):
                    service_url = f"https://{service_url}"

                details = {
                    "service_arn": service_arn,
                    "service_url": service_url,
                    "status": status,
                    "action": "created",
                    "source_config": {
                        "runtime": normalized_runtime,
                        "build_command": build_command.strip(),
                        "start_command": start_command.strip(),
                        "s3_source": s3_location,
                    },
                    "instance_config": {
                        "cpu": "1024",
                        "memory": "2048",
                    },
                }

                return _build_manifest(
                    target_type="apprunner",
                    status="success",
                    resource_arn=service_arn,
                    region=region,
                    details=details,
                    rollback_info={
                        "service_name": service_name,
                        "service_arn": service_arn,
                        "note": "First deployment — no previous version available for rollback.",
                    },
                )

            except Exception as create_err:
                error_msg = str(create_err)
                logger.error("apprunner_create_failed", service_name=service_name, error=error_msg)

                # Attempt to retrieve CloudWatch log group for debugging
                log_group = f"/aws/apprunner/{service_name}"
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create App Runner service '{service_name}': {error_msg}",
                    "target_type": "apprunner",
                    "cloudwatch_log_group": log_group,
                    "suggestion": (
                        f"Check CloudWatch logs at '{log_group}' for more details. "
                        "Verify the runtime is supported, and the build/start commands are correct. "
                        "Ensure IAM permissions include apprunner:CreateService."
                    ),
                })

    except Exception as e:
        logger.error("deploy_apprunner_source_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during App Runner source deployment: {str(e)}",
            "target_type": "apprunner",
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


@tool
def deploy_apprunner_service(
    dockerfile_content: str,
    app_code: str,
    service_name: str = '',
    region: str = 'us-east-1',
) -> str:
    """Build a container image via CodeBuild, push to ECR, and deploy to AWS App Runner.

    Creates an ECR repository if needed, stages the Dockerfile and app code to S3,
    triggers a CodeBuild project to build and push the image, then creates or updates
    an App Runner service with the ECR image source.

    Args:
        dockerfile_content: The Dockerfile content for the container build.
        app_code: The application source code to include in the container.
        service_name: App Runner service name. If empty, a default name is generated.
        region: AWS region for deployment.

    Returns:
        JSON string with deployment manifest containing service_arn, service_url, status,
        image_uri on success, or error details on failure.
    """
    try:
        logger.info("deploy_apprunner_service", service_name=service_name, region=region)

        # Validate inputs
        if not dockerfile_content or not dockerfile_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Dockerfile content is empty. Cannot build a container without a Dockerfile.",
                "suggestion": "Provide a valid Dockerfile for the App Runner container build."
            })

        if not app_code or not app_code.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Application code is empty. Cannot deploy an empty application.",
                "suggestion": "Provide valid application source code for the container."
            })

        # Generate default service name if not provided
        if not service_name or not service_name.strip():
            service_name = _generate_default_name()

        # Get AWS account ID
        try:
            sts_client = boto3.client('sts', region_name=region)
            account_id = sts_client.get_caller_identity()['Account']
        except Exception as sts_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to get AWS account ID: {str(sts_err)}",
                "suggestion": "Verify AWS credentials are configured and have STS permissions."
            })

        timestamp = str(int(time.time()))
        ecr_repo_name = service_name
        image_tag = "latest"
        image_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{ecr_repo_name}:{image_tag}"

        # Step 1: Create ECR repository if it does not exist
        ecr_client = boto3.client('ecr', region_name=region)
        try:
            ecr_client.describe_repositories(repositoryNames=[ecr_repo_name])
            logger.info("ecr_repo_exists", repo=ecr_repo_name)
        except ecr_client.exceptions.RepositoryNotFoundException:
            try:
                ecr_client.create_repository(
                    repositoryName=ecr_repo_name,
                    imageScanningConfiguration={'scanOnPush': True},
                    imageTagMutability='MUTABLE',
                )
                logger.info("ecr_repo_created", repo=ecr_repo_name)
            except Exception as ecr_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create ECR repository '{ecr_repo_name}': {str(ecr_err)}",
                    "target_type": "apprunner",
                    "suggestion": "Verify IAM permissions include ecr:CreateRepository."
                })
        except Exception as ecr_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to check ECR repository: {str(ecr_err)}",
                "target_type": "apprunner",
                "suggestion": "Verify IAM permissions include ecr:DescribeRepositories."
            })

        # Step 2: Upload Dockerfile and app code to S3 staging bucket
        staging_bucket = f"cloudops-deployments-{account_id}"
        s3_prefix = f"apprunner/{service_name}/{timestamp}"

        try:
            s3_client = boto3.client('s3', region_name=region)

            # Upload Dockerfile
            s3_client.put_object(
                Bucket=staging_bucket,
                Key=f"{s3_prefix}/Dockerfile",
                Body=dockerfile_content.encode('utf-8'),
            )

            # Upload app code as a zip
            app_zip = _package_code_to_zip(app_code, "app.py")
            s3_client.put_object(
                Bucket=staging_bucket,
                Key=f"{s3_prefix}/app.zip",
                Body=app_zip,
            )
            logger.info("s3_staging_uploaded", bucket=staging_bucket, prefix=s3_prefix)
        except Exception as s3_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to upload build artifacts to S3: {str(s3_err)}",
                "target_type": "apprunner",
                "suggestion": f"Verify the S3 bucket '{staging_bucket}' exists and you have PutObject permissions."
            })

        # Step 3: Trigger CodeBuild to build and push image to ECR
        codebuild_project_name = f"cloudops-apprunner-build-{service_name}"

        buildspec = {
            "version": "0.2",
            "phases": {
                "pre_build": {
                    "commands": [
                        f"aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {account_id}.dkr.ecr.{region}.amazonaws.com",
                        f"aws s3 cp s3://{staging_bucket}/{s3_prefix}/app.zip app.zip",
                        "unzip -o app.zip",
                    ]
                },
                "build": {
                    "commands": [
                        f"docker build -t {ecr_repo_name} .",
                        f"docker tag {ecr_repo_name}:latest {image_uri}",
                    ]
                },
                "post_build": {
                    "commands": [
                        f"docker push {image_uri}",
                    ]
                },
            },
        }

        codebuild_client = boto3.client('codebuild', region_name=region)

        try:
            # Start the build using the inline buildspec and S3 source for Dockerfile
            build_response = codebuild_client.start_build(
                projectName=codebuild_project_name,
                sourceTypeOverride='S3',
                sourceLocationOverride=f"{staging_bucket}/{s3_prefix}/",
                buildspecOverride=json.dumps(buildspec),
                environmentVariablesOverride=[
                    {'name': 'IMAGE_URI', 'value': image_uri, 'type': 'PLAINTEXT'},
                    {'name': 'ECR_REPO', 'value': ecr_repo_name, 'type': 'PLAINTEXT'},
                ],
            )
            build_id = build_response['build']['id']
            logger.info("codebuild_started", build_id=build_id)
        except Exception as cb_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to start CodeBuild for image build: {str(cb_err)}",
                "target_type": "apprunner",
                "codebuild_project": codebuild_project_name,
                "suggestion": (
                    f"Ensure the CodeBuild project '{codebuild_project_name}' exists with Docker support. "
                    "The project needs the 'aws/codebuild/amazonlinux2-x86_64-standard:5.0' image with privileged mode enabled."
                )
            })

        # Poll CodeBuild for completion (timeout after 10 minutes)
        max_wait = 600  # 10 minutes
        poll_interval = 10
        elapsed = 0
        build_status = "IN_PROGRESS"

        while elapsed < max_wait and build_status == "IN_PROGRESS":
            time.sleep(poll_interval)
            elapsed += poll_interval
            try:
                builds_response = codebuild_client.batch_get_builds(ids=[build_id])
                build_info = builds_response['builds'][0]
                build_status = build_info.get('buildStatus', 'IN_PROGRESS')
            except Exception:
                pass

        if build_status != "SUCCEEDED":
            # Build failed — get log details
            log_group = f"/aws/codebuild/{codebuild_project_name}"
            error_phases = ""
            try:
                builds_response = codebuild_client.batch_get_builds(ids=[build_id])
                build_info = builds_response['builds'][0]
                phases = build_info.get('phases', [])
                failed_phases = [p for p in phases if p.get('phaseStatus') == 'FAILED']
                if failed_phases:
                    error_phases = json.dumps(failed_phases, default=str)
            except Exception:
                pass

            return json.dumps({
                "status": "failed",
                "error_type": "build_failure",
                "error_message": f"CodeBuild image build failed with status: {build_status}",
                "target_type": "apprunner",
                "build_id": build_id,
                "codebuild_project": codebuild_project_name,
                "failed_phases": error_phases,
                "cloudwatch_log_group": log_group,
                "suggestion": (
                    f"Check CloudWatch logs at '{log_group}' for build details. "
                    "Common issues: invalid Dockerfile, missing dependencies, or ECR push permissions."
                )
            })

        # Step 4: Create or update App Runner service with the ECR image
        apprunner_client = boto3.client('apprunner', region_name=region)

        # Check if service already exists
        existing_service_arn = None
        try:
            paginator = apprunner_client.get_paginator('list_services')
            for page in paginator.paginate():
                for svc in page.get('ServiceSummaryList', []):
                    if svc.get('ServiceName') == service_name:
                        existing_service_arn = svc.get('ServiceArn')
                        break
                if existing_service_arn:
                    break
        except Exception:
            pass

        # Construct the App Runner access role ARN for ECR
        access_role_arn = f"arn:aws:iam::{account_id}:role/cloudops-apprunner-ecr-access-role"

        if existing_service_arn:
            # Update existing service
            try:
                update_response = apprunner_client.update_service(
                    ServiceArn=existing_service_arn,
                    SourceConfiguration={
                        'ImageRepository': {
                            'ImageIdentifier': image_uri,
                            'ImageRepositoryType': 'ECR',
                            'ImageConfiguration': {
                                'Port': '8080',
                            },
                        },
                        'AuthenticationConfiguration': {
                            'AccessRoleArn': access_role_arn,
                        },
                        'AutoDeploymentsEnabled': False,
                    },
                )
                service_info = update_response.get('Service', {})
                service_arn = service_info.get('ServiceArn', existing_service_arn)
                service_url = service_info.get('ServiceUrl', '')
                service_status = service_info.get('Status', 'OPERATION_IN_PROGRESS')

                if service_url and not service_url.startswith('https://'):
                    service_url = f"https://{service_url}"

                details = {
                    "service_arn": service_arn,
                    "service_url": service_url,
                    "status": service_status,
                    "image_uri": image_uri,
                    "action": "updated",
                    "build_id": build_id,
                }

                return _build_manifest(
                    target_type="apprunner",
                    status="success",
                    resource_arn=service_arn,
                    region=region,
                    details=details,
                    rollback_info={
                        "service_name": service_name,
                        "service_arn": service_arn,
                        "note": "To rollback, redeploy with a previous image tag."
                    },
                )

            except Exception as update_err:
                log_group = f"/aws/apprunner/{service_name}"
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to update App Runner service '{service_name}': {str(update_err)}",
                    "target_type": "apprunner",
                    "service_arn": existing_service_arn,
                    "cloudwatch_log_group": log_group,
                    "suggestion": (
                        f"Check CloudWatch logs at '{log_group}' for details. "
                        "Verify the ECR access role has permissions to pull from the repository."
                    )
                })
        else:
            # Create new App Runner service
            try:
                create_response = apprunner_client.create_service(
                    ServiceName=service_name,
                    SourceConfiguration={
                        'ImageRepository': {
                            'ImageIdentifier': image_uri,
                            'ImageRepositoryType': 'ECR',
                            'ImageConfiguration': {
                                'Port': '8080',
                            },
                        },
                        'AuthenticationConfiguration': {
                            'AccessRoleArn': access_role_arn,
                        },
                        'AutoDeploymentsEnabled': False,
                    },
                    InstanceConfiguration={
                        'Cpu': '1024',
                        'Memory': '2048',
                    },
                    HealthCheckConfiguration={
                        'Protocol': 'TCP',
                        'Path': '/',
                        'Interval': 10,
                        'Timeout': 5,
                        'HealthyThreshold': 1,
                        'UnhealthyThreshold': 5,
                    },
                )
                service_info = create_response.get('Service', {})
                service_arn = service_info.get('ServiceArn', '')
                service_url = service_info.get('ServiceUrl', '')
                service_status = service_info.get('Status', 'OPERATION_IN_PROGRESS')

                if service_url and not service_url.startswith('https://'):
                    service_url = f"https://{service_url}"

                details = {
                    "service_arn": service_arn,
                    "service_url": service_url,
                    "status": service_status,
                    "image_uri": image_uri,
                    "action": "created",
                    "build_id": build_id,
                }

                return _build_manifest(
                    target_type="apprunner",
                    status="success",
                    resource_arn=service_arn,
                    region=region,
                    details=details,
                    rollback_info={
                        "service_name": service_name,
                        "service_arn": service_arn,
                        "note": "To rollback, redeploy with a previous image tag or delete the service."
                    },
                )

            except Exception as create_err:
                log_group = f"/aws/apprunner/{service_name}"
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create App Runner service '{service_name}': {str(create_err)}",
                    "target_type": "apprunner",
                    "cloudwatch_log_group": log_group,
                    "suggestion": (
                        f"Check CloudWatch logs at '{log_group}' for details. "
                        "Verify the IAM role 'cloudops-apprunner-ecr-access-role' exists and has "
                        "ECR pull permissions. Ensure the App Runner service name is unique in this region."
                    )
                })

    except Exception as e:
        logger.error("deploy_apprunner_service_error", error=str(e), service_name=service_name)
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during App Runner deployment: {str(e)}",
            "target_type": "apprunner",
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


# ============================================================================
# ECS/ECR Deployment Tools
# ============================================================================


@tool
def deploy_ecs_service(
    dockerfile_content: str,
    app_code: str,
    service_name: str = '',
    cluster_name: str = 'cloudops-cluster',
    region: str = 'us-east-1',
) -> str:
    """Build a Docker image via CodeBuild, push to ECR, and deploy to ECS Fargate.

    Creates an ECR repository if it does not exist, uploads the Dockerfile and application
    code to an S3 staging bucket, triggers AWS CodeBuild to build the image and push to ECR,
    then creates or updates an ECS Fargate service with the new image.

    Args:
        dockerfile_content: The Dockerfile content for building the container image.
        app_code: The application source code to include in the container build.
        service_name: ECS service and ECR repository name. If empty, a default name is generated.
        cluster_name: ECS cluster name. Defaults to 'cloudops-cluster'.
        region: AWS region for deployment.

    Returns:
        JSON string with deployment manifest on success, or error details on failure.
    """
    try:
        logger.info("deploy_ecs_service", service_name=service_name, cluster_name=cluster_name, region=region)

        # Validate inputs
        if not dockerfile_content or not dockerfile_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Dockerfile content is empty. Cannot build a container image without a Dockerfile.",
                "suggestion": "Provide valid Dockerfile content for the container build."
            })

        if not app_code or not app_code.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Application code is empty. Cannot build a container without application code.",
                "suggestion": "Provide the application source code to include in the container."
            })

        # Generate default service name if not provided
        if not service_name or not service_name.strip():
            service_name = _generate_default_name()

        # Get AWS account ID
        try:
            sts_client = boto3.client('sts', region_name=region)
            account_id = sts_client.get_caller_identity()['Account']
        except Exception as sts_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to get AWS account ID: {str(sts_err)}",
                "suggestion": "Verify AWS credentials are configured and have STS permissions."
            })

        # Initialize clients
        ecr_client = boto3.client('ecr', region_name=region)
        ecs_client = boto3.client('ecs', region_name=region)
        codebuild_client = boto3.client('codebuild', region_name=region)
        s3_client = boto3.client('s3', region_name=region)

        # ---- Step 1: Create ECR repository if it does not exist ----
        ecr_repo_uri = None
        try:
            describe_response = ecr_client.describe_repositories(repositoryNames=[service_name])
            ecr_repo_uri = describe_response['repositories'][0]['repositoryUri']
            logger.info("ecr_repo_exists", repo=service_name, uri=ecr_repo_uri)
        except ecr_client.exceptions.RepositoryNotFoundException:
            try:
                create_response = ecr_client.create_repository(
                    repositoryName=service_name,
                    imageScanningConfiguration={'scanOnPush': True},
                    imageTagMutability='MUTABLE',
                )
                ecr_repo_uri = create_response['repository']['repositoryUri']
                logger.info("ecr_repo_created", repo=service_name, uri=ecr_repo_uri)
            except Exception as ecr_create_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create ECR repository '{service_name}': {str(ecr_create_err)}",
                    "suggestion": "Verify IAM permissions include ecr:CreateRepository."
                })
        except Exception as ecr_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to check ECR repository '{service_name}': {str(ecr_err)}",
                "suggestion": "Verify IAM permissions include ecr:DescribeRepositories."
            })

        # ---- Step 2: Upload Dockerfile and app code to S3 staging ----
        staging_bucket = f"cloudops-deployments-{account_id}"
        timestamp = str(int(time.time()))
        s3_prefix = f"deployments/ecs/{service_name}/{timestamp}"

        # Create a zip archive containing Dockerfile and app code
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('Dockerfile', dockerfile_content)
            zf.writestr('app.py', app_code)
        zip_bytes = zip_buffer.getvalue()

        s3_key = f"{s3_prefix}/source.zip"
        try:
            s3_client.put_object(
                Bucket=staging_bucket,
                Key=s3_key,
                Body=zip_bytes,
            )
            logger.info("s3_upload_success", bucket=staging_bucket, key=s3_key)
        except Exception as s3_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to upload build artifacts to S3: {str(s3_err)}",
                "suggestion": f"Verify the S3 bucket '{staging_bucket}' exists and you have PutObject permissions."
            })

        # ---- Step 3: Trigger CodeBuild to build image and push to ECR ----
        image_tag = "latest"
        ecr_image_uri = f"{ecr_repo_uri}:{image_tag}"

        # Build a dynamic buildspec
        buildspec = {
            "version": "0.2",
            "phases": {
                "pre_build": {
                    "commands": [
                        "echo Logging in to Amazon ECR...",
                        f"aws ecr get-login-password --region {region} | docker login --username AWS --password-stdin {account_id}.dkr.ecr.{region}.amazonaws.com",
                    ]
                },
                "build": {
                    "commands": [
                        "echo Building the Docker image...",
                        f"docker build -t {service_name} .",
                        f"docker tag {service_name}:latest {ecr_image_uri}",
                    ]
                },
                "post_build": {
                    "commands": [
                        "echo Pushing the Docker image to ECR...",
                        f"docker push {ecr_image_uri}",
                        "echo Build completed successfully.",
                    ]
                },
            },
        }

        codebuild_project_name = f"cloudops-build-{service_name}"

        # Create or update the CodeBuild project
        try:
            codebuild_client.create_project(
                name=codebuild_project_name,
                description=f"CloudOps container build for {service_name}",
                source={
                    'type': 'S3',
                    'location': f"{staging_bucket}/{s3_key}",
                    'buildspec': json.dumps(buildspec),
                },
                artifacts={'type': 'NO_ARTIFACTS'},
                environment={
                    'type': 'LINUX_CONTAINER',
                    'image': 'aws/codebuild/standard:7.0',
                    'computeType': 'BUILD_GENERAL1_SMALL',
                    'privilegedMode': True,
                    'environmentVariables': [
                        {'name': 'AWS_DEFAULT_REGION', 'value': region, 'type': 'PLAINTEXT'},
                        {'name': 'AWS_ACCOUNT_ID', 'value': account_id, 'type': 'PLAINTEXT'},
                        {'name': 'IMAGE_REPO_NAME', 'value': service_name, 'type': 'PLAINTEXT'},
                        {'name': 'IMAGE_TAG', 'value': image_tag, 'type': 'PLAINTEXT'},
                    ],
                },
                serviceRole=f"arn:aws:iam::{account_id}:role/cloudops-codebuild-role",
            )
            logger.info("codebuild_project_created", project=codebuild_project_name)
        except codebuild_client.exceptions.ResourceAlreadyExistsException:
            # Project already exists — update its source location
            try:
                codebuild_client.update_project(
                    name=codebuild_project_name,
                    source={
                        'type': 'S3',
                        'location': f"{staging_bucket}/{s3_key}",
                        'buildspec': json.dumps(buildspec),
                    },
                )
                logger.info("codebuild_project_updated", project=codebuild_project_name)
            except Exception as update_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to update CodeBuild project: {str(update_err)}",
                    "suggestion": "Verify IAM permissions include codebuild:UpdateProject."
                })
        except Exception as cb_create_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to create CodeBuild project: {str(cb_create_err)}",
                "suggestion": "Verify IAM permissions include codebuild:CreateProject and that the CodeBuild service role exists."
            })

        # Start the build
        try:
            build_response = codebuild_client.start_build(projectName=codebuild_project_name)
            build_id = build_response['build']['id']
            logger.info("codebuild_started", build_id=build_id)
        except Exception as build_start_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to start CodeBuild build: {str(build_start_err)}",
                "suggestion": "Verify the CodeBuild project exists and you have codebuild:StartBuild permission."
            })

        # Poll for build completion (timeout after 10 minutes)
        build_status = "IN_PROGRESS"
        max_wait_seconds = 600
        poll_interval = 10
        elapsed = 0

        while build_status == "IN_PROGRESS" and elapsed < max_wait_seconds:
            time.sleep(poll_interval)
            elapsed += poll_interval
            try:
                batch_response = codebuild_client.batch_get_builds(ids=[build_id])
                builds = batch_response.get('builds', [])
                if builds:
                    build_status = builds[0].get('buildStatus', 'IN_PROGRESS')
            except Exception:
                # Non-critical polling error, continue waiting
                pass

        # Handle build timeout
        if build_status == "IN_PROGRESS":
            return json.dumps({
                "status": "failed",
                "error_type": "build_timeout",
                "error_message": f"CodeBuild timed out after {max_wait_seconds} seconds. Build ID: {build_id}",
                "codebuild_build_id": build_id,
                "suggestion": "Check the CodeBuild console for the build status. The build may still complete."
            })

        # Handle build failure
        if build_status != "SUCCEEDED":
            # Fetch build logs for error reporting
            build_logs = ""
            failure_phase = ""
            try:
                batch_response = codebuild_client.batch_get_builds(ids=[build_id])
                builds = batch_response.get('builds', [])
                if builds:
                    build_info = builds[0]
                    phases = build_info.get('phases', [])
                    for phase in phases:
                        if phase.get('phaseStatus') == 'FAILED':
                            failure_phase = phase.get('phaseType', 'UNKNOWN')
                            contexts = phase.get('contexts', [])
                            if contexts:
                                build_logs = contexts[0].get('message', '')
                            break
                    # Also check logs location
                    logs_info = build_info.get('logs', {})
                    if logs_info.get('deepLink'):
                        build_logs += f"\nCloudWatch Logs: {logs_info['deepLink']}"
            except Exception:
                build_logs = "Unable to retrieve build logs."

            return json.dumps({
                "status": "failed",
                "error_type": "build_failure",
                "error_message": f"CodeBuild failed with status: {build_status}",
                "codebuild_build_id": build_id,
                "failure_phase": failure_phase,
                "build_logs": build_logs,
                "target_type": "ecs",
                "suggestion": "Review the build logs above. Common issues: Dockerfile syntax errors, "
                              "missing dependencies, or ECR push permission issues."
            })

        # ---- Step 4: Register task definition and create/update ECS service ----
        # Define the task definition
        task_def_family = service_name

        try:
            task_def_response = ecs_client.register_task_definition(
                family=task_def_family,
                networkMode='awsvpc',
                requiresCompatibilities=['FARGATE'],
                cpu='256',
                memory='512',
                executionRoleArn=f"arn:aws:iam::{account_id}:role/cloudops-ecs-execution-role",
                containerDefinitions=[
                    {
                        'name': service_name,
                        'image': ecr_image_uri,
                        'essential': True,
                        'portMappings': [
                            {
                                'containerPort': 80,
                                'protocol': 'tcp',
                            }
                        ],
                        'logConfiguration': {
                            'logDriver': 'awslogs',
                            'options': {
                                'awslogs-group': f"/ecs/{service_name}",
                                'awslogs-region': region,
                                'awslogs-stream-prefix': 'ecs',
                            },
                        },
                    }
                ],
            )
            task_definition_arn = task_def_response['taskDefinition']['taskDefinitionArn']
            logger.info("task_definition_registered", arn=task_definition_arn)
        except Exception as td_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to register task definition: {str(td_err)}",
                "codebuild_build_id": build_id,
                "ecr_image_uri": ecr_image_uri,
                "suggestion": "Verify IAM permissions include ecs:RegisterTaskDefinition and "
                              "that the ECS execution role exists."
            })

        # Check if service already exists
        service_exists = False
        service_arn = ""
        try:
            describe_svc = ecs_client.describe_services(
                cluster=cluster_name,
                services=[service_name],
            )
            services = describe_svc.get('services', [])
            for svc in services:
                if svc.get('status') == 'ACTIVE':
                    service_exists = True
                    service_arn = svc.get('serviceArn', '')
                    break
        except Exception:
            # If cluster doesn't exist or other error, we'll try to create
            service_exists = False

        if service_exists:
            # Update existing service with new task definition (rolling deployment)
            try:
                update_response = ecs_client.update_service(
                    cluster=cluster_name,
                    service=service_name,
                    taskDefinition=task_definition_arn,
                    forceNewDeployment=True,
                )
                service_arn = update_response['service']['serviceArn']
                desired_count = update_response['service'].get('desiredCount', 1)
                logger.info("ecs_service_updated", service=service_name, task_def=task_definition_arn)

                details = {
                    "ecr_image_uri": ecr_image_uri,
                    "task_definition_arn": task_definition_arn,
                    "service_arn": service_arn,
                    "desired_count": desired_count,
                    "codebuild_build_id": build_id,
                    "action": "updated",
                    "deployment_type": "rolling",
                }

                return _build_manifest(
                    target_type="ecs",
                    status="success",
                    resource_arn=service_arn,
                    region=region,
                    details=details,
                    rollback_info={
                        "service_name": service_name,
                        "cluster_name": cluster_name,
                        "previous_task_definition": task_definition_arn,
                    },
                )

            except Exception as update_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to update ECS service '{service_name}': {str(update_err)}",
                    "codebuild_build_id": build_id,
                    "task_definition_arn": task_definition_arn,
                    "suggestion": "Verify the ECS service exists in the cluster and you have ecs:UpdateService permission."
                })
        else:
            # Create new Fargate service
            try:
                # Ensure cluster exists
                try:
                    ecs_client.create_cluster(clusterName=cluster_name)
                    logger.info("ecs_cluster_created", cluster=cluster_name)
                except Exception:
                    # Cluster may already exist, which is fine
                    pass

                # Get default VPC subnets and security group for networking
                ec2_client = boto3.client('ec2', region_name=region)

                # Find default VPC
                vpcs = ec2_client.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
                if vpcs['Vpcs']:
                    vpc_id = vpcs['Vpcs'][0]['VpcId']
                else:
                    # Use first available VPC
                    all_vpcs = ec2_client.describe_vpcs()
                    if all_vpcs['Vpcs']:
                        vpc_id = all_vpcs['Vpcs'][0]['VpcId']
                    else:
                        return json.dumps({
                            "status": "failed",
                            "error_type": "aws_api_error",
                            "error_message": "No VPC found. Cannot create ECS service without network configuration.",
                            "suggestion": "Create a VPC or provide network configuration for the ECS service."
                        })

                # Get subnets for the VPC
                subnets_response = ec2_client.describe_subnets(
                    Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]
                )
                subnet_ids = [s['SubnetId'] for s in subnets_response['Subnets'][:2]]

                if not subnet_ids:
                    return json.dumps({
                        "status": "failed",
                        "error_type": "aws_api_error",
                        "error_message": "No subnets found in the VPC. Cannot create ECS service.",
                        "suggestion": "Create subnets in the VPC or specify network configuration."
                    })

                # Get or create a security group
                sg_name = f"cloudops-ecs-{service_name}-sg"
                try:
                    sg_response = ec2_client.create_security_group(
                        GroupName=sg_name,
                        Description=f"Security group for ECS service {service_name}",
                        VpcId=vpc_id,
                    )
                    security_group_id = sg_response['GroupId']

                    # Allow inbound traffic on port 80
                    ec2_client.authorize_security_group_ingress(
                        GroupId=security_group_id,
                        IpPermissions=[
                            {
                                'IpProtocol': 'tcp',
                                'FromPort': 80,
                                'ToPort': 80,
                                'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'Allow HTTP'}],
                            }
                        ],
                    )
                except Exception:
                    # Security group may already exist, find it
                    try:
                        existing_sgs = ec2_client.describe_security_groups(
                            Filters=[
                                {'Name': 'group-name', 'Values': [sg_name]},
                                {'Name': 'vpc-id', 'Values': [vpc_id]},
                            ]
                        )
                        if existing_sgs['SecurityGroups']:
                            security_group_id = existing_sgs['SecurityGroups'][0]['GroupId']
                        else:
                            # Use default security group
                            default_sgs = ec2_client.describe_security_groups(
                                Filters=[
                                    {'Name': 'group-name', 'Values': ['default']},
                                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                                ]
                            )
                            security_group_id = default_sgs['SecurityGroups'][0]['GroupId']
                    except Exception:
                        return json.dumps({
                            "status": "failed",
                            "error_type": "aws_api_error",
                            "error_message": "Failed to configure security group for ECS service.",
                            "suggestion": "Verify EC2 permissions or provide a security group ID."
                        })

                # Create the Fargate service
                create_svc_response = ecs_client.create_service(
                    cluster=cluster_name,
                    serviceName=service_name,
                    taskDefinition=task_definition_arn,
                    desiredCount=1,
                    launchType='FARGATE',
                    networkConfiguration={
                        'awsvpcConfiguration': {
                            'subnets': subnet_ids,
                            'securityGroups': [security_group_id],
                            'assignPublicIp': 'ENABLED',
                        }
                    },
                    deploymentConfiguration={
                        'maximumPercent': 200,
                        'minimumHealthyPercent': 100,
                    },
                )

                service_arn = create_svc_response['service']['serviceArn']
                desired_count = create_svc_response['service'].get('desiredCount', 1)
                logger.info("ecs_service_created", service=service_name, arn=service_arn)

                details = {
                    "ecr_image_uri": ecr_image_uri,
                    "task_definition_arn": task_definition_arn,
                    "service_arn": service_arn,
                    "desired_count": desired_count,
                    "codebuild_build_id": build_id,
                    "action": "created",
                    "launch_type": "FARGATE",
                    "cpu": "0.25 vCPU",
                    "memory": "512 MB",
                    "cluster_name": cluster_name,
                }

                return _build_manifest(
                    target_type="ecs",
                    status="success",
                    resource_arn=service_arn,
                    region=region,
                    details=details,
                    rollback_info={
                        "service_name": service_name,
                        "cluster_name": cluster_name,
                        "task_definition_arn": task_definition_arn,
                        "note": "First deployment — no previous revision to rollback to.",
                    },
                )

            except Exception as create_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create ECS Fargate service '{service_name}': {str(create_err)}",
                    "codebuild_build_id": build_id,
                    "task_definition_arn": task_definition_arn,
                    "ecr_image_uri": ecr_image_uri,
                    "suggestion": "Verify IAM permissions include ecs:CreateService, ecs:CreateCluster, "
                                  "and that the ECS execution role has the necessary permissions."
                })

    except Exception as e:
        logger.error("deploy_ecs_service_error", error=str(e), service_name=service_name)
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during ECS deployment: {str(e)}",
            "target_type": "ecs",
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


@tool
def rollback_ecs_service(service_name: str, cluster_name: str = 'cloudops-cluster', region: str = 'us-east-1') -> str:
    """Rollback an ECS service to the previous task definition revision.

    Describes the current service to get its active task definition, extracts the
    task definition family, lists all revisions for that family, identifies the
    previous revision (current minus 1), and updates the service to use it.

    Args:
        service_name: The ECS service name to roll back.
        cluster_name: ECS cluster name where the service runs. Defaults to 'cloudops-cluster'.
        region: AWS region where the service is deployed.

    Returns:
        JSON string with deployment manifest confirming the rolled-back state,
        or error details if rollback cannot be performed.
    """
    try:
        logger.info("rollback_ecs_service", service_name=service_name, cluster_name=cluster_name, region=region)

        # Validate service name
        if not service_name or not service_name.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "service_name is required and cannot be empty.",
                "suggestion": "Provide the name of the ECS service you want to roll back."
            })

        ecs_client = boto3.client('ecs', region_name=region)

        # ---- Step 1: Describe the current service to get the active task definition ----
        try:
            describe_response = ecs_client.describe_services(
                cluster=cluster_name,
                services=[service_name],
            )
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to describe ECS service '{service_name}' in cluster '{cluster_name}': {str(e)}",
                "suggestion": "Verify the cluster name and service name are correct, and that you have ecs:DescribeServices permission."
            })

        services = describe_response.get('services', [])
        active_service = None
        for svc in services:
            if svc.get('status') == 'ACTIVE':
                active_service = svc
                break

        if not active_service:
            return json.dumps({
                "status": "failed",
                "error_type": "resource_not_found",
                "error_message": f"No active ECS service '{service_name}' found in cluster '{cluster_name}'.",
                "suggestion": "Verify the service name and cluster. Use the AWS console or CLI to list active services in the cluster."
            })

        current_task_def_arn = active_service.get('taskDefinition', '')
        service_arn = active_service.get('serviceArn', '')
        desired_count = active_service.get('desiredCount', 1)

        if not current_task_def_arn:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Could not determine the current task definition for service '{service_name}'.",
                "suggestion": "The service may be in an inconsistent state. Check the AWS ECS console."
            })

        # ---- Step 2: Extract the task definition family from the current task def ARN ----
        # Task def ARN format: arn:aws:ecs:region:account:task-definition/family:revision
        try:
            # Extract family:revision from the ARN
            task_def_part = current_task_def_arn.split('/')[-1]  # e.g., "my-service:3"
            family = task_def_part.rsplit(':', 1)[0]  # e.g., "my-service"
            current_revision = int(task_def_part.rsplit(':', 1)[1])  # e.g., 3
        except (IndexError, ValueError) as parse_err:
            return json.dumps({
                "status": "failed",
                "error_type": "parse_error",
                "error_message": f"Failed to parse task definition ARN '{current_task_def_arn}': {str(parse_err)}",
                "suggestion": "The task definition ARN format is unexpected. Check the ECS console for the service's task definition."
            })

        # ---- Step 3: List task definition revisions for that family ----
        try:
            revisions = []
            paginator = ecs_client.get_paginator('list_task_definitions')
            for page in paginator.paginate(familyPrefix=family, sort='DESC', status='ACTIVE'):
                revisions.extend(page.get('taskDefinitionArns', []))
        except Exception as e:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to list task definition revisions for family '{family}': {str(e)}",
                "suggestion": "Verify IAM permissions include ecs:ListTaskDefinitions."
            })

        # ---- Step 4: Find the previous revision (current minus 1) ----
        previous_task_def_arn = None
        for rev_arn in revisions:
            try:
                rev_part = rev_arn.split('/')[-1]
                rev_number = int(rev_part.rsplit(':', 1)[1])
                if rev_number == current_revision - 1:
                    previous_task_def_arn = rev_arn
                    break
            except (IndexError, ValueError):
                continue

        # ---- Step 5: If no previous revision exists, return informative error ----
        if not previous_task_def_arn:
            return json.dumps({
                "status": "failed",
                "error_type": "no_previous_revision",
                "error_message": (
                    f"Cannot rollback service '{service_name}': no previous task definition revision exists. "
                    f"Current revision is {current_revision} for family '{family}'."
                ),
                "suggestion": (
                    "A rollback requires at least two task definition revisions. Suggestions:\n"
                    "- Redeploy the service with corrected code using deploy_ecs_service.\n"
                    "- Manually register a new task definition with the desired image.\n"
                    "- If the previous revision was deregistered, re-register it from your container image history in ECR."
                ),
                "service_name": service_name,
                "cluster_name": cluster_name,
                "current_task_definition": current_task_def_arn,
                "current_revision": current_revision,
                "available_revisions": len(revisions),
            })

        # ---- Step 6: Update the ECS service to use the previous task definition revision ----
        try:
            update_response = ecs_client.update_service(
                cluster=cluster_name,
                service=service_name,
                taskDefinition=previous_task_def_arn,
                forceNewDeployment=True,
            )

            updated_service = update_response.get('service', {})
            service_arn = updated_service.get('serviceArn', service_arn)
            desired_count = updated_service.get('desiredCount', desired_count)

            logger.info("ecs_service_rolled_back",
                        service=service_name,
                        from_revision=current_revision,
                        to_revision=current_revision - 1,
                        task_def=previous_task_def_arn)

        except Exception as update_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to update ECS service '{service_name}' for rollback: {str(update_err)}",
                "target_type": "ecs",
                "service_name": service_name,
                "cluster_name": cluster_name,
                "previous_task_definition": previous_task_def_arn,
                "suggestion": "Verify IAM permissions include ecs:UpdateService and that the previous task definition is still active."
            })

        # ---- Step 7: Return manifest confirming rolled-back state ----
        details = {
            "task_definition_arn": previous_task_def_arn,
            "service_arn": service_arn,
            "desired_count": desired_count,
            "action": "rollback",
            "rolled_back_from_revision": current_revision,
            "rolled_back_to_revision": current_revision - 1,
            "previous_task_definition": current_task_def_arn,
        }

        return _build_manifest(
            target_type="ecs",
            status="success",
            resource_arn=service_arn,
            region=region,
            details=details,
            rollback_info={
                "service_name": service_name,
                "cluster_name": cluster_name,
                "restored_task_definition": previous_task_def_arn,
                "rolled_back_from": current_task_def_arn,
            },
        )

    except Exception as e:
        logger.error("rollback_ecs_service_error", service_name=service_name, error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during ECS service rollback: {str(e)}",
            "target_type": "ecs",
            "service_name": service_name,
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


# ============================================================================
# CloudFormation/SAM Deployment Tools
# ============================================================================


@tool
def validate_cfn_template(template_content: str, region: str = 'us-east-1') -> str:
    """Validate a CloudFormation or SAM template syntax before deployment.

    Calls the CloudFormation ValidateTemplate API to check template syntax and
    returns the validation result including description, parameters, and capabilities.

    Args:
        template_content: The CloudFormation or SAM template body (YAML or JSON).
        region: AWS region to use for the validation API call.

    Returns:
        JSON string with validation result on success, or error details on failure.
    """
    try:
        logger.info("validate_cfn_template", region=region)

        # Validate that template_content is non-empty
        if not template_content or not template_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Template content is empty. Cannot validate an empty template.",
                "suggestion": "Provide a valid CloudFormation or SAM template (YAML or JSON)."
            })

        # Call CloudFormation validate_template API
        cfn_client = boto3.client('cloudformation', region_name=region)

        try:
            response = cfn_client.validate_template(TemplateBody=template_content)

            # Extract validation details
            description = response.get('Description', '')
            parameters = response.get('Parameters', [])
            capabilities = response.get('Capabilities', [])
            capabilities_reason = response.get('CapabilitiesReason', '')

            # Format parameters for readability
            formatted_parameters = []
            for param in parameters:
                formatted_parameters.append({
                    "key": param.get('ParameterKey', ''),
                    "default_value": param.get('DefaultValue', ''),
                    "no_echo": param.get('NoEcho', False),
                    "description": param.get('Description', ''),
                })

            return json.dumps({
                "status": "success",
                "message": "Template validation passed.",
                "description": description,
                "parameters": formatted_parameters,
                "capabilities": capabilities,
                "capabilities_reason": capabilities_reason,
            })

        except cfn_client.exceptions.ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))

            return json.dumps({
                "status": "failed",
                "error_type": "validation_error",
                "error_code": error_code,
                "error_message": f"Template validation failed: {error_message}",
                "suggestion": (
                    "Review the template for syntax errors. Common issues include: "
                    "invalid resource types, missing required properties, malformed YAML/JSON, "
                    "or unsupported intrinsic functions. Check the error message for line references."
                )
            })

    except Exception as e:
        logger.error("validate_cfn_template_error", error=str(e))
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during template validation: {str(e)}",
            "suggestion": "Check the template content and AWS connectivity, then try again."
        })


@tool
def deploy_cfn_stack(
    template_content: str,
    stack_name: str,
    parameters: str = '{}',
    region: str = 'us-east-1',
) -> str:
    """Create or update a CloudFormation stack with the provided template and parameters.

    Validates the template before deployment. If the stack does not exist, creates it.
    If the stack already exists, creates a change set and returns the proposed changes
    for operator review. On successful creation, returns a deployment manifest with
    stack details.

    Args:
        template_content: The CloudFormation/SAM template content (YAML or JSON).
        stack_name: Name of the CloudFormation stack.
        parameters: JSON string of stack parameters (e.g. '{"KeyName": "my-key", "Env": "prod"}').
        region: AWS region for the stack deployment.

    Returns:
        JSON string with deployment manifest on success, change set details for
        existing stacks, or error details on failure.
    """
    try:
        logger.info("deploy_cfn_stack", stack_name=stack_name, region=region)

        # Validate inputs
        if not template_content or not template_content.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": "Template content is empty. Cannot deploy an empty template.",
                "suggestion": "Provide a valid CloudFormation or SAM template (YAML or JSON)."
            })

        if not stack_name or not stack_name.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "stack_name is required and cannot be empty.",
                "suggestion": "Provide a name for the CloudFormation stack."
            })

        stack_name = stack_name.strip()

        # Parse parameters JSON string
        try:
            params_dict = json.loads(parameters) if parameters and parameters.strip() else {}
        except json.JSONDecodeError as parse_err:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_payload",
                "error_message": f"Failed to parse parameters as JSON: {str(parse_err)}",
                "suggestion": "Provide parameters as a valid JSON string, e.g. '{\"KeyName\": \"value\"}'."
            })

        # Convert parameters dict to CloudFormation parameter format
        cfn_parameters = [
            {"ParameterKey": k, "ParameterValue": str(v)}
            for k, v in params_dict.items()
        ]

        cfn_client = boto3.client('cloudformation', region_name=region)

        # Step 1: Validate the template before deployment
        try:
            validate_response = cfn_client.validate_template(TemplateBody=template_content)
            capabilities = validate_response.get('Capabilities', [])
        except cfn_client.exceptions.ClientError as val_err:
            error_message = str(val_err)
            return json.dumps({
                "status": "failed",
                "error_type": "validation_error",
                "error_message": f"Template validation failed: {error_message}",
                "suggestion": "Fix the template syntax errors before attempting deployment. "
                              "Common issues: invalid resource types, missing required properties, "
                              "or malformed YAML/JSON."
            })
        except Exception as val_err:
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Error validating template: {str(val_err)}",
                "suggestion": "Check AWS connectivity and permissions (cloudformation:ValidateTemplate)."
            })

        # Step 2: Check if the stack already exists
        stack_exists = False
        stack_status = None
        try:
            describe_response = cfn_client.describe_stacks(StackName=stack_name)
            stacks = describe_response.get('Stacks', [])
            if stacks:
                stack_status = stacks[0].get('StackStatus', '')
                # Treat DELETE_COMPLETE and ROLLBACK_COMPLETE stacks as non-existent
                if stack_status not in ('DELETE_COMPLETE', 'ROLLBACK_COMPLETE'):
                    stack_exists = True
        except cfn_client.exceptions.ClientError as e:
            # Stack does not exist — this is expected
            if 'does not exist' in str(e):
                stack_exists = False
            else:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Error checking stack status: {str(e)}",
                    "suggestion": "Verify the stack name and AWS permissions (cloudformation:DescribeStacks)."
                })

        if not stack_exists:
            # Step 3a: Create new stack
            try:
                create_kwargs = {
                    "StackName": stack_name,
                    "TemplateBody": template_content,
                    "Parameters": cfn_parameters,
                    "OnFailure": "ROLLBACK",
                    "Tags": [
                        {"Key": "ManagedBy", "Value": "CloudOps"},
                        {"Key": "DeployedAt", "Value": datetime.now(timezone.utc).isoformat()},
                    ],
                }
                if capabilities:
                    create_kwargs["Capabilities"] = capabilities

                create_response = cfn_client.create_stack(**create_kwargs)
                stack_id = create_response.get('StackId', '')
                logger.info("cfn_stack_create_initiated", stack_name=stack_name, stack_id=stack_id)

            except Exception as create_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create stack '{stack_name}': {str(create_err)}",
                    "suggestion": "Verify IAM permissions include cloudformation:CreateStack, and that "
                                  "all required parameters are provided. Check resource quotas in the target region."
                })

            # Poll for stack creation completion
            max_wait_seconds = 600
            poll_interval = 10
            elapsed = 0
            final_status = "CREATE_IN_PROGRESS"

            while final_status == "CREATE_IN_PROGRESS" and elapsed < max_wait_seconds:
                time.sleep(poll_interval)
                elapsed += poll_interval
                try:
                    poll_response = cfn_client.describe_stacks(StackName=stack_name)
                    stacks = poll_response.get('Stacks', [])
                    if stacks:
                        final_status = stacks[0].get('StackStatus', 'UNKNOWN')
                except Exception:
                    pass

            # Handle timeout
            if final_status == "CREATE_IN_PROGRESS":
                return json.dumps({
                    "status": "pending",
                    "error_type": "timeout",
                    "stack_name": stack_name,
                    "stack_id": stack_id,
                    "error_message": f"Stack creation still in progress after {max_wait_seconds}s.",
                    "suggestion": "The stack is still being created. Check the CloudFormation console for status updates."
                })

            # Handle failure
            if final_status != "CREATE_COMPLETE":
                # Fetch failed stack events for error reporting
                failure_events = _get_failed_stack_events(cfn_client, stack_name)
                return json.dumps({
                    "status": "failed",
                    "error_type": "stack_creation_failed",
                    "stack_name": stack_name,
                    "stack_id": stack_id,
                    "final_status": final_status,
                    "error_message": f"Stack creation failed with status: {final_status}",
                    "failed_events": failure_events,
                    "suggestion": "Review the failed events above. Common causes: IAM permission issues, "
                                  "resource limit exceeded, or invalid property values. "
                                  "Consider rolling back with rollback_cfn_stack if the stack is in a failed state."
                })

            # Success — gather stack outputs and resources
            try:
                final_describe = cfn_client.describe_stacks(StackName=stack_name)
                stack_info = final_describe['Stacks'][0]
                stack_id = stack_info.get('StackId', stack_id)
                outputs_raw = stack_info.get('Outputs', [])
                outputs = {o['OutputKey']: o['OutputValue'] for o in outputs_raw}
            except Exception:
                outputs = {}

            # Count resources created
            resources_created = 0
            try:
                resources_response = cfn_client.list_stack_resources(StackName=stack_name)
                resources_created = len(resources_response.get('StackResourceSummaries', []))
            except Exception:
                pass

            details = {
                "stack_name": stack_name,
                "stack_id": stack_id,
                "outputs": outputs,
                "resources_created": resources_created,
                "action": "created",
                "final_status": final_status,
            }

            return _build_manifest(
                target_type="cloudformation",
                status="success",
                resource_arn=stack_id,
                region=region,
                details=details,
                rollback_info={
                    "stack_name": stack_name,
                    "action": "delete_stack",
                    "note": "First deployment — rollback would delete the stack entirely.",
                },
            )

        else:
            # Step 3b: Stack exists — create a change set for updates
            change_set_name = f"cloudops-changeset-{int(time.time())}"

            try:
                changeset_kwargs = {
                    "StackName": stack_name,
                    "ChangeSetName": change_set_name,
                    "TemplateBody": template_content,
                    "Parameters": cfn_parameters,
                    "Description": f"Change set created by CloudOps at {datetime.now(timezone.utc).isoformat()}",
                }
                if capabilities:
                    changeset_kwargs["Capabilities"] = capabilities

                changeset_response = cfn_client.create_change_set(**changeset_kwargs)
                change_set_id = changeset_response.get('Id', '')
                logger.info("cfn_changeset_created", stack_name=stack_name, change_set_name=change_set_name)

            except Exception as cs_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to create change set for stack '{stack_name}': {str(cs_err)}",
                    "suggestion": "Verify IAM permissions include cloudformation:CreateChangeSet. "
                                  "Ensure the stack is in a stable state (not currently updating)."
                })

            # Wait for change set to be ready
            cs_status = "CREATE_PENDING"
            max_cs_wait = 120
            cs_elapsed = 0

            while cs_status in ("CREATE_PENDING", "CREATE_IN_PROGRESS") and cs_elapsed < max_cs_wait:
                time.sleep(5)
                cs_elapsed += 5
                try:
                    cs_describe = cfn_client.describe_change_set(
                        ChangeSetName=change_set_name,
                        StackName=stack_name,
                    )
                    cs_status = cs_describe.get('Status', 'UNKNOWN')
                except Exception:
                    break

            # Describe the change set to get the changes
            try:
                cs_detail = cfn_client.describe_change_set(
                    ChangeSetName=change_set_name,
                    StackName=stack_name,
                )
            except Exception as desc_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to describe change set: {str(desc_err)}",
                    "change_set_name": change_set_name,
                    "suggestion": "The change set may still be in creation. Check the CloudFormation console."
                })

            cs_status = cs_detail.get('Status', 'UNKNOWN')
            cs_status_reason = cs_detail.get('StatusReason', '')

            # Handle FAILED change set (e.g., no changes detected)
            if cs_status == 'FAILED':
                # Clean up the failed change set
                try:
                    cfn_client.delete_change_set(
                        ChangeSetName=change_set_name,
                        StackName=stack_name,
                    )
                except Exception:
                    pass

                return json.dumps({
                    "status": "failed",
                    "error_type": "changeset_failed",
                    "stack_name": stack_name,
                    "change_set_name": change_set_name,
                    "error_message": f"Change set creation failed: {cs_status_reason}",
                    "suggestion": "If the reason is 'no changes', the template matches the current stack. "
                                  "Otherwise, fix the template and try again."
                })

            # Extract the changes for review
            changes = cs_detail.get('Changes', [])
            changes_summary = []
            for change in changes:
                resource_change = change.get('ResourceChange', {})
                changes_summary.append({
                    "action": resource_change.get('Action', ''),
                    "logical_id": resource_change.get('LogicalResourceId', ''),
                    "resource_type": resource_change.get('ResourceType', ''),
                    "replacement": resource_change.get('Replacement', 'False'),
                    "physical_id": resource_change.get('PhysicalResourceId', ''),
                })

            # Get the current stack ID
            try:
                stack_describe = cfn_client.describe_stacks(StackName=stack_name)
                stack_id = stack_describe['Stacks'][0].get('StackId', '')
            except Exception:
                stack_id = ''

            # Return the change set details for operator confirmation
            details = {
                "stack_name": stack_name,
                "stack_id": stack_id,
                "change_set_name": change_set_name,
                "change_set_id": change_set_id,
                "change_set_status": cs_status,
                "changes": changes_summary,
                "changes_count": len(changes_summary),
                "action": "update_pending",
                "message": (
                    f"Change set '{change_set_name}' created with {len(changes_summary)} change(s). "
                    "Review the changes above and confirm to execute the update."
                ),
            }

            return _build_manifest(
                target_type="cloudformation",
                status="pending_confirmation",
                resource_arn=stack_id,
                region=region,
                details=details,
                rollback_info={
                    "stack_name": stack_name,
                    "change_set_name": change_set_name,
                    "action": "delete_change_set",
                    "note": "To cancel, the change set can be deleted without affecting the stack.",
                },
            )

    except Exception as e:
        logger.error("deploy_cfn_stack_error", error=str(e), stack_name=stack_name)
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during CloudFormation deployment: {str(e)}",
            "target_type": "cloudformation",
            "stack_name": stack_name,
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })


def _get_failed_stack_events(cfn_client, stack_name: str) -> list:
    """Retrieve failed stack events for error diagnosis.

    Args:
        cfn_client: boto3 CloudFormation client.
        stack_name: The stack name to get events for.

    Returns:
        List of dicts with failed event details.
    """
    try:
        events_response = cfn_client.describe_stack_events(StackName=stack_name)
        events = events_response.get('StackEvents', [])

        failed_events = []
        for event in events:
            status = event.get('ResourceStatus', '')
            if 'FAILED' in status:
                failed_events.append({
                    "logical_id": event.get('LogicalResourceId', ''),
                    "resource_type": event.get('ResourceType', ''),
                    "status": status,
                    "reason": event.get('ResourceStatusReason', ''),
                    "timestamp": str(event.get('Timestamp', '')),
                })

        return failed_events[:10]  # Limit to 10 most relevant events
    except Exception:
        return []


@tool
def rollback_cfn_stack(stack_name: str, region: str = 'us-east-1') -> str:
    """Rollback a CloudFormation stack to its last successful state.

    Verifies the stack exists, checks its current status, and initiates an
    appropriate rollback action. For stacks in UPDATE_ROLLBACK_FAILED state,
    calls continue_update_rollback. For stacks in UPDATE_FAILED or similar
    states, calls rollback_stack. For CREATE_FAILED stacks, suggests deletion
    and recreation since there is no previous successful state.

    Args:
        stack_name: The name or ID of the CloudFormation stack to roll back.
        region: AWS region where the stack is deployed.

    Returns:
        JSON string with deployment manifest confirming the rollback status,
        or error details if rollback cannot be performed.
    """
    try:
        logger.info("rollback_cfn_stack", stack_name=stack_name, region=region)

        # Validate stack_name
        if not stack_name or not stack_name.strip():
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_parameter",
                "error_message": "stack_name is required and cannot be empty.",
                "suggestion": "Provide the name or ID of the CloudFormation stack you want to roll back."
            })

        cfn_client = boto3.client('cloudformation', region_name=region)

        # ---- Step 1: Verify the stack exists by calling describe_stacks ----
        try:
            describe_response = cfn_client.describe_stacks(StackName=stack_name)
        except Exception as e:
            error_str = str(e)
            if 'does not exist' in error_str.lower() or 'not found' in error_str.lower():
                return json.dumps({
                    "status": "failed",
                    "error_type": "resource_not_found",
                    "error_message": f"Stack '{stack_name}' does not exist in region '{region}'.",
                    "suggestion": (
                        "Verify the stack name is correct. Use the AWS console or CLI to list "
                        "existing stacks. The stack may have been deleted."
                    )
                })
            return json.dumps({
                "status": "failed",
                "error_type": "aws_api_error",
                "error_message": f"Failed to describe stack '{stack_name}': {error_str}",
                "suggestion": "Verify the stack name and that you have cloudformation:DescribeStacks permission."
            })

        stacks = describe_response.get('Stacks', [])
        if not stacks:
            return json.dumps({
                "status": "failed",
                "error_type": "resource_not_found",
                "error_message": f"Stack '{stack_name}' not found in region '{region}'.",
                "suggestion": "Verify the stack name is correct and the stack has not been deleted."
            })

        stack = stacks[0]
        stack_status = stack.get('StackStatus', '')
        stack_id = stack.get('StackId', '')

        # ---- Step 2: Check the stack status to determine rollback action ----

        # States where rollback is already complete or in progress
        already_rolled_back_states = [
            'ROLLBACK_COMPLETE',
            'UPDATE_ROLLBACK_COMPLETE',
        ]

        rollback_in_progress_states = [
            'ROLLBACK_IN_PROGRESS',
            'UPDATE_ROLLBACK_IN_PROGRESS',
            'UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS',
        ]

        # States where we can call continue_update_rollback
        continue_rollback_states = [
            'UPDATE_ROLLBACK_FAILED',
        ]

        # States where we can call rollback_stack
        rollback_eligible_states = [
            'UPDATE_FAILED',
            'UPDATE_COMPLETE',
            'UPDATE_ROLLBACK_COMPLETE',
        ]

        # CREATE_FAILED — no previous state to roll back to
        create_failed_states = [
            'CREATE_FAILED',
        ]

        # Stable states where rollback doesn't apply
        stable_states = [
            'CREATE_COMPLETE',
        ]

        # States where no action can be taken
        terminal_states = [
            'DELETE_COMPLETE',
            'DELETE_IN_PROGRESS',
            'DELETE_FAILED',
        ]

        # ---- Handle each state category ----

        if stack_status in terminal_states:
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_state",
                "error_message": f"Stack '{stack_name}' is in '{stack_status}' state. Cannot perform rollback on a deleted or deleting stack.",
                "stack_name": stack_name,
                "stack_id": stack_id,
                "current_status": stack_status,
                "suggestion": "If you need this stack, create a new one with deploy_cfn_stack using your template."
            })

        if stack_status in rollback_in_progress_states:
            return json.dumps({
                "status": "in_progress",
                "message": f"Stack '{stack_name}' is already rolling back (status: {stack_status}). No additional action needed.",
                "stack_name": stack_name,
                "stack_id": stack_id,
                "current_status": stack_status,
                "suggestion": "Wait for the rollback to complete. Monitor progress in the CloudFormation console."
            })

        if stack_status in already_rolled_back_states:
            # Already rolled back — return manifest confirming current state
            details = {
                "stack_name": stack_name,
                "stack_id": stack_id,
                "action": "rollback",
                "current_status": stack_status,
                "message": "Stack has already been rolled back to its last successful state.",
            }
            return _build_manifest(
                target_type="cloudformation",
                status="success",
                resource_arn=stack_id,
                region=region,
                details=details,
            )

        if stack_status in create_failed_states:
            return json.dumps({
                "status": "failed",
                "error_type": "no_previous_state",
                "error_message": (
                    f"Stack '{stack_name}' is in CREATE_FAILED state. There is no previous "
                    "successful state to roll back to because the stack was never successfully created."
                ),
                "stack_name": stack_name,
                "stack_id": stack_id,
                "current_status": stack_status,
                "suggestion": (
                    "For a CREATE_FAILED stack, consider these options:\n"
                    "1. Delete the stack and recreate it with a corrected template.\n"
                    "2. Review the failed stack events to identify and fix the issue.\n"
                    "3. Use validate_cfn_template to check the template before redeploying."
                ),
                "failed_events": _get_failed_stack_events(cfn_client, stack_name),
            })

        if stack_status == 'CREATE_IN_PROGRESS':
            return json.dumps({
                "status": "failed",
                "error_type": "invalid_state",
                "error_message": f"Stack '{stack_name}' is currently being created (CREATE_IN_PROGRESS). Cannot rollback during initial creation.",
                "stack_name": stack_name,
                "stack_id": stack_id,
                "current_status": stack_status,
                "suggestion": "Wait for the stack creation to complete. If it fails, you can delete and recreate it."
            })

        # ---- Step 3: Perform the appropriate rollback action ----

        if stack_status in continue_rollback_states:
            # Stack is stuck in UPDATE_ROLLBACK_FAILED — use continue_update_rollback
            try:
                cfn_client.continue_update_rollback(StackName=stack_name)
                logger.info("cfn_continue_update_rollback",
                            stack_name=stack_name,
                            previous_status=stack_status)

                details = {
                    "stack_name": stack_name,
                    "stack_id": stack_id,
                    "action": "rollback",
                    "rollback_method": "continue_update_rollback",
                    "previous_status": stack_status,
                    "message": (
                        f"Initiated continue_update_rollback for stack '{stack_name}'. "
                        "The stack will attempt to complete the rollback to the last successful state."
                    ),
                }

                return _build_manifest(
                    target_type="cloudformation",
                    status="success",
                    resource_arn=stack_id,
                    region=region,
                    details=details,
                    rollback_info={
                        "stack_name": stack_name,
                        "rollback_method": "continue_update_rollback",
                        "triggered_from_status": stack_status,
                    },
                )

            except Exception as rollback_err:
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to continue rollback for stack '{stack_name}': {str(rollback_err)}",
                    "stack_name": stack_name,
                    "stack_id": stack_id,
                    "current_status": stack_status,
                    "suggestion": (
                        "The continue_update_rollback failed. You may need to:\n"
                        "1. Skip resources that are blocking the rollback using the 'resources_to_skip' parameter.\n"
                        "2. Manually fix the resources that failed to rollback.\n"
                        "3. Delete and recreate the stack if recovery is not possible."
                    )
                })

        if stack_status in rollback_eligible_states:
            # Stack can be rolled back using rollback_stack
            try:
                cfn_client.rollback_stack(StackName=stack_name)
                logger.info("cfn_rollback_stack",
                            stack_name=stack_name,
                            previous_status=stack_status)

                details = {
                    "stack_name": stack_name,
                    "stack_id": stack_id,
                    "action": "rollback",
                    "rollback_method": "rollback_stack",
                    "previous_status": stack_status,
                    "message": (
                        f"Initiated rollback for stack '{stack_name}'. "
                        "The stack will revert to the last successful configuration."
                    ),
                }

                return _build_manifest(
                    target_type="cloudformation",
                    status="success",
                    resource_arn=stack_id,
                    region=region,
                    details=details,
                    rollback_info={
                        "stack_name": stack_name,
                        "rollback_method": "rollback_stack",
                        "triggered_from_status": stack_status,
                    },
                )

            except Exception as rollback_err:
                error_str = str(rollback_err)
                # If rollback_stack is not supported for this state, provide guidance
                return json.dumps({
                    "status": "failed",
                    "error_type": "aws_api_error",
                    "error_message": f"Failed to rollback stack '{stack_name}': {error_str}",
                    "stack_name": stack_name,
                    "stack_id": stack_id,
                    "current_status": stack_status,
                    "suggestion": (
                        "The rollback operation failed. Possible reasons:\n"
                        "1. The stack may not support rollback from its current state.\n"
                        "2. IAM permissions may be insufficient (requires cloudformation:RollbackStack).\n"
                        "3. Try deploying a corrected template using deploy_cfn_stack instead."
                    )
                })

        if stack_status in stable_states:
            # CREATE_COMPLETE — no updates have been made, nothing to roll back
            return json.dumps({
                "status": "failed",
                "error_type": "no_previous_state",
                "error_message": (
                    f"Stack '{stack_name}' is in CREATE_COMPLETE state with no prior updates. "
                    "There is no previous state to roll back to."
                ),
                "stack_name": stack_name,
                "stack_id": stack_id,
                "current_status": stack_status,
                "suggestion": (
                    "This stack has only been created, never updated. Rollback requires a previous "
                    "update to revert. If you want to remove this stack, use the AWS CLI or console "
                    "to delete it, then redeploy with deploy_cfn_stack."
                )
            })

        # ---- Fallback: Unhandled stack status ----
        return json.dumps({
            "status": "failed",
            "error_type": "unsupported_state",
            "error_message": (
                f"Stack '{stack_name}' is in '{stack_status}' state. "
                "Rollback cannot be determined for this state."
            ),
            "stack_name": stack_name,
            "stack_id": stack_id,
            "current_status": stack_status,
            "suggestion": (
                "Check the stack status in the CloudFormation console. If the stack is in a "
                "transitional state, wait for it to stabilize before attempting rollback. "
                "If the stack is broken, consider deleting and recreating it."
            )
        })

    except Exception as e:
        logger.error("rollback_cfn_stack_error", error=str(e), stack_name=stack_name)
        return json.dumps({
            "status": "failed",
            "error_type": "unexpected_error",
            "error_message": f"Unexpected error during CloudFormation stack rollback: {str(e)}",
            "target_type": "cloudformation",
            "stack_name": stack_name,
            "suggestion": "Check the input parameters and AWS connectivity, then try again."
        })
