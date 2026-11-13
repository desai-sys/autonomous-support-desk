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


_secrets: dict = {}


def secret(name: str) -> str:
    """
    A secret setting: the environment variable ``name`` if set (ECS injects
    secrets this way), otherwise the Secrets Manager secret whose ARN is in
    ``<name>_SECRET_ARN`` (the agent runtimes, whose plain environment is
    visible through the AWS API). Fetched once per process; a failed fetch
    raises, so a request that needs the secret fails instead of running without it.
    """
    if name not in _secrets:
        value, arn = _env(name), _env(f"{name}_SECRET_ARN")
        if not value and arn:
            import boto3
            value = boto3.client("secretsmanager", region_name=AWS_REGION).get_secret_value(
                SecretId=arn)["SecretString"]
        _secrets[name] = value
    return _secrets[name]


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
JOBS_TABLE = _env("JOBS_TABLE", "CloudOpsJobs")
CONVERSATIONS_TABLE = _env("CONVERSATIONS_TABLE", "CloudOpsConversations")
# Identifiers of the platform's own resources (user pool id, runtime names, stack
# names, ...). The agent refuses to read or change anything that names them; IAM
# denies on the agent roles enforce the same thing.
PROTECTED_RESOURCE_NAMES = [n.strip() for n in _env("PROTECTED_RESOURCE_NAMES", "").split(",") if n.strip()]

# ── Knowledge base (RAG over resolved tickets) ────────────────────────────────
S3_BUCKET_NAME = _env("KB_BUCKET", "")
KNOWLEDGE_BASE_ID = _env("KNOWLEDGE_BASE_ID", "")
DATA_SOURCE_ID = _env("DATA_SOURCE_ID", "")
RAG_MIN_SCORE = float(_env("RAG_MIN_SCORE", "0.6"))

# ── Agent ─────────────────────────────────────────────────────────────────────
# The agent runs as two runtimes with different IAM roles. The planner talks to
# the model and holds read-only credentials; the executor runs approved actions,
# has no model, and holds the credentials that can change things.
PLANNER_RUNTIME_ARN = _env("PLANNER_RUNTIME_ARN", _env("AGENT_RUNTIME_ARN", ""))
EXECUTOR_RUNTIME_ARN = _env("EXECUTOR_RUNTIME_ARN", _env("AGENT_RUNTIME_ARN", ""))
# What this process may do: "planner" (chat only), "executor" (approved actions
# only) or "all" (local development, one process).
RUNTIME_MODE = _env("RUNTIME_MODE", "all")
# Local development only: POST to an agent started with `python agent_main.py`
# (e.g. http://localhost:8080) instead of calling AgentCore.
AGENT_ENDPOINT_URL = _env("AGENT_ENDPOINT_URL", "")
AGENT_MODEL_ID = _env("AGENT_MODEL_ID", "us.amazon.nova-pro-v1:0")
ROUTER_MODEL_ID = _env("ROUTER_MODEL_ID", "us.amazon.nova-micro-v1:0")
# Model the app builder writes code with. Empty = AGENT_MODEL_ID. The builder is designed
# to work on Amazon Nova Pro; point this at another model you have access to if you want
# to compare (the evaluation's build scenarios measure the difference).
BUILD_MODEL_ID = _env("BUILD_MODEL_ID", "")
AGENT_MAX_TOKENS = _env_int("AGENT_MAX_TOKENS", 8192)

# ── Change approval ───────────────────────────────────────────────────────────
# Every mutating tool call is queued as an action and only runs after a person
# approves it in the console. Unapproved actions expire after this many seconds.
ACTION_TTL_SECONDS = _env_int("ACTION_TTL_SECONDS", 3600)
# When true, the person who requested a change cannot approve it themselves.
REQUIRE_SEPARATE_APPROVER = _env_bool("REQUIRE_SEPARATE_APPROVER", True)
# An approved action still "executing" after this long is marked interrupted
# (the executor stopped mid-run); it is never retried automatically.
EXECUTION_TIMEOUT_SECONDS = _env_int("EXECUTION_TIMEOUT_SECONDS", 1200)

# ── Cross-account access ──────────────────────────────────────────────────────
# Role assumed in each managed account (deployment/cross_account_role.yaml).
CROSS_ACCOUNT_ROLE_NAME = _env("CROSS_ACCOUNT_ROLE_NAME", "CloudOpsCrossAccountRole")
# The target-account roles require an ExternalId (protects against the
# confused-deputy problem): secret("CROSS_ACCOUNT_EXTERNAL_ID"). Each tenant other
# than the default one gets its own, derived from it (see src/core/tenancy.py).
# Optional static allow-list {account_id: name}, merged with the accounts that
# admins register in the console (ACCOUNTS_TABLE).
MANAGED_ACCOUNTS = json.loads(_env("MANAGED_ACCOUNTS", "{}") or "{}")

# ── Code upload ───────────────────────────────────────────────────────────────
ARTIFACT_BUCKET = _env("ARTIFACT_BUCKET", "")
ARTIFACT_PREFIX = "uploads/"
MAX_ARTIFACT_MB = _env_int("MAX_ARTIFACT_MB", 200)

# ── Console authentication ────────────────────────────────────────────────────
# "oidc": sign in with Amazon Cognito (the default, so a missing setting never opens the console).
# "local": pick any identity on a form; must be set explicitly, for local development only.
AUTH_MODE = _env("AUTH_MODE", "oidc")
OIDC_CLIENT_ID = _env("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = _env("OIDC_CLIENT_SECRET", "")
OIDC_METADATA_URL = _env("OIDC_METADATA_URL", "")
OIDC_REDIRECT_URI = _env("OIDC_REDIRECT_URI", "")
OIDC_LOGOUT_URL = _env("OIDC_LOGOUT_URL", "")     # Cognito /logout URL incl. client_id + logout_uri
SESSION_SECRET = _env("OIDC_COOKIE_SECRET", "")
DEFAULT_TENANT_ID = _env("DEFAULT_TENANT_ID", "default")
# Tenants allowed to use the platform account itself; others only reach the
# accounts registered for them.
PLATFORM_TENANTS = frozenset(t.strip() for t in _env("PLATFORM_TENANTS", DEFAULT_TENANT_ID).split(",") if t.strip())
# The console signs every request to the agent with secret("IDENTITY_SIGNING_KEY");
# the agent rejects requests that are unsigned, altered or older than this.
IDENTITY_MAX_AGE_SECONDS = _env_int("IDENTITY_MAX_AGE_SECONDS", 300)

# ── Learning loop ─────────────────────────────────────────────────────────────
LEARNING_LOOP_TASK = _env("LEARNING_LOOP_TASK", "")  # set in ECS; enables "Run now"
