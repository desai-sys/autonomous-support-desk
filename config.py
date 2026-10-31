"""
CloudOps platform configuration.

Every value comes from an environment variable so the same code runs locally,
in the AgentCore runtime and in the ECS console service. The CloudFormation
stacks in infra/ set these variables; the defaults below are for local
development only.
"""

import json
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# ── General ───────────────────────────────────────────────────────────────────
AWS_REGION = _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-east-1"))
ENVIRONMENT = _env("CLOUDOPS_ENV", "dev")
LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# ── DynamoDB tables ───────────────────────────────────────────────────────────
TICKETS_TABLE = _env("TICKETS_TABLE", "CloudOpsTickets")
USERS_TABLE = _env("USERS_TABLE", "CloudOpsUsers")
TENANTS_TABLE = _env("TENANTS_TABLE", "CloudOpsTenants")
USAGE_TABLE = _env("USAGE_TABLE", "CloudOpsUsage")
AUDIT_TABLE = _env("AUDIT_TABLE", "CloudOpsAuditLog")
ACTIONS_TABLE = _env("ACTIONS_TABLE", "CloudOpsActions")
ACCOUNTS_TABLE = _env("ACCOUNTS_TABLE", "CloudOpsAccounts")

# ── Knowledge base (RAG over resolved tickets) ────────────────────────────────
S3_BUCKET_NAME = _env("KB_BUCKET", "")
KNOWLEDGE_BASE_ID = _env("KNOWLEDGE_BASE_ID", "")
DATA_SOURCE_ID = _env("DATA_SOURCE_ID", "")
RAG_MIN_SCORE = float(_env("RAG_MIN_SCORE", "0.6"))

# ── Agent ─────────────────────────────────────────────────────────────────────
AGENT_ARN = _env("AGENT_RUNTIME_ARN", "")
# Local development only: POST to an agent started with `python agent_main.py`
# (e.g. http://localhost:8080) instead of calling AgentCore.
AGENT_ENDPOINT_URL = _env("AGENT_ENDPOINT_URL", "")
AGENT_MODEL_ID = _env("AGENT_MODEL_ID", "us.amazon.nova-pro-v1:0")
ROUTER_MODEL_ID = _env("ROUTER_MODEL_ID", "us.amazon.nova-micro-v1:0")
# Model for the app builder (writing whole applications). Empty = AGENT_MODEL_ID.
# Code generation benefits from a stronger model, e.g. us.amazon.nova-premier-v1:0.
BUILD_MODEL_ID = _env("BUILD_MODEL_ID", "")
AGENT_MAX_TOKENS = _env_int("AGENT_MAX_TOKENS", 8192)

# ── Change approval ───────────────────────────────────────────────────────────
# Every mutating tool call is queued as an action and only runs after a person
# approves it in the console. Unapproved actions expire after this many seconds.
ACTION_TTL_SECONDS = _env_int("ACTION_TTL_SECONDS", 3600)
# When true, the person who requested a change cannot approve it themselves.
REQUIRE_SEPARATE_APPROVER = _env_bool("REQUIRE_SEPARATE_APPROVER", False)

# ── Cross-account access ──────────────────────────────────────────────────────
# Role assumed in each managed account (deployment/cross_account_role.yaml).
CROSS_ACCOUNT_ROLE_NAME = _env("CROSS_ACCOUNT_ROLE_NAME", "CloudOpsCrossAccountRole")
# Shared secret the target-account roles require (protects against the
# confused-deputy problem). Empty disables the ExternalId condition.
CROSS_ACCOUNT_EXTERNAL_ID = _env("CROSS_ACCOUNT_EXTERNAL_ID", "")
# Optional static allow-list {account_id: name}, merged with the accounts that
# admins register in the console (ACCOUNTS_TABLE).
MANAGED_ACCOUNTS = json.loads(_env("MANAGED_ACCOUNTS", "{}") or "{}")

# ── Code upload ───────────────────────────────────────────────────────────────
ARTIFACT_BUCKET = _env("ARTIFACT_BUCKET", "")
ARTIFACT_PREFIX = "uploads/"
MAX_ARTIFACT_MB = _env_int("MAX_ARTIFACT_MB", 200)

# ── Console authentication ────────────────────────────────────────────────────
# "oidc": sign in with Amazon Cognito (production).
# "local": pick any identity on a form; for local development only.
AUTH_MODE = _env("AUTH_MODE", "local")
DEFAULT_TENANT_ID = _env("DEFAULT_TENANT_ID", "default")

# ── Learning loop ─────────────────────────────────────────────────────────────
LEARNING_LOOP_TASK = _env("LEARNING_LOOP_TASK", "")  # set in ECS; enables "Run now"
