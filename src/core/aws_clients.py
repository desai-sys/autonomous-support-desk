"""
Cross-account AWS client factory.

Every SRE tool creates its AWS clients through this module instead of calling
``boto3.client(...)`` directly. A context variable holds the *active target
account* for the current execution. When an account is set, the factory assumes
a well-known role in that account via STS and returns a client scoped to those
temporary credentials. When no account is set (the default), it behaves exactly
like plain boto3 and uses the ambient credentials of the runtime.

Tool modules opt in with a one-line import swap::

    from src.core.aws_clients import boto3        # instead of: import boto3

`boto3.client(...)` and `boto3.resource(...)` then transparently route through
the factory, so none of the hundreds of existing call sites need to change.

The entrypoint drives this by calling :func:`use_account` (a context manager)
around each agent run, once per target account the operator specified. This lets
a single prompt fan out and act on any number of accounts.
"""

import contextlib
import contextvars
import threading
import time
from typing import Optional

import boto3 as _boto3

try:
    # CROSS_ACCOUNT_ROLE_NAME is the role assumed in each target account.
    from config import CROSS_ACCOUNT_ROLE_NAME, AWS_REGION as _DEFAULT_REGION
except Exception:  # pragma: no cover - config always present in practice
    CROSS_ACCOUNT_ROLE_NAME = "CloudOpsCrossAccountRole"
    _DEFAULT_REGION = "us-east-1"


# ---------------------------------------------------------------------------
# Active-account context
# ---------------------------------------------------------------------------

# Holds the account id the current execution should act on. ``None`` means
# "use the runtime's own (base) account with ambient credentials".
_active_account: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "cloudops_active_account", default=None
)

# Optional per-context default region override.
_active_region: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar(
    "cloudops_active_region", default=None
)


def get_active_account() -> Optional[str]:
    """Return the account id the current context targets, or None for base."""
    return _active_account.get()


@contextlib.contextmanager
def use_account(account_id: Optional[str], region: Optional[str] = None):
    """
    Context manager that pins all AWS clients created inside it to ``account_id``.

    ``account_id`` of ``None`` (or the runtime's own account) uses ambient
    credentials with no assume-role hop.
    """
    tok_a = _active_account.set(account_id or None)
    tok_r = _active_region.set(region or None)
    try:
        yield
    finally:
        _active_account.reset(tok_a)
        _active_region.reset(tok_r)


# ---------------------------------------------------------------------------
# Assumed-role credential cache
# ---------------------------------------------------------------------------

_creds_lock = threading.Lock()
# key: account_id -> {"credentials": {...}, "expiry": epoch_seconds}
_creds_cache: dict = {}

# Refresh a little before the STS credentials actually expire.
_EXPIRY_SKEW_SECONDS = 300


def _base_account_id() -> Optional[str]:
    """Account id the runtime itself is running as (cached best-effort)."""
    global _base_account
    try:
        return _base_account
    except NameError:
        pass
    try:
        _base_account = _boto3.client(
            "sts", region_name=_DEFAULT_REGION
        ).get_caller_identity()["Account"]
    except Exception:
        _base_account = None
    return _base_account


def _assume_role_credentials(account_id: str) -> dict:
    """
    Return valid temporary credentials for ``account_id``, assuming
    ``CROSS_ACCOUNT_ROLE_NAME`` in it. Credentials are cached until near expiry.
    """
    now = time.time()
    with _creds_lock:
        entry = _creds_cache.get(account_id)
        if entry and entry["expiry"] - _EXPIRY_SKEW_SECONDS > now:
            return entry["credentials"]

    sts = _boto3.client("sts", region_name=_DEFAULT_REGION)
    role_arn = f"arn:aws:iam::{account_id}:role/{CROSS_ACCOUNT_ROLE_NAME}"
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"cloudops-agent-{account_id}",
        DurationSeconds=3600,
    )
    creds = resp["Credentials"]
    scoped = {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
    }
    expiry = creds["Expiration"].timestamp()
    with _creds_lock:
        _creds_cache[account_id] = {"credentials": scoped, "expiry": expiry}
    return scoped


def _resolve_region(kwargs: dict) -> dict:
    """Apply a context region default when the caller didn't pass one."""
    if "region_name" not in kwargs or kwargs.get("region_name") is None:
        ctx_region = _active_region.get()
        if ctx_region:
            kwargs["region_name"] = ctx_region
    return kwargs


def _needs_cross_account(account_id: Optional[str]) -> bool:
    if not account_id:
        return False
    base = _base_account_id()
    # If we can't determine the base account, still assume-role to be safe.
    return base is None or account_id != base


def get_client(service_name: str, **kwargs):
    """Drop-in replacement for ``boto3.client`` that is account-aware."""
    kwargs = _resolve_region(dict(kwargs))
    account_id = _active_account.get()
    if _needs_cross_account(account_id):
        creds = _assume_role_credentials(account_id)
        kwargs.setdefault("region_name", _DEFAULT_REGION)
        return _boto3.client(service_name, **{**creds, **kwargs})
    return _boto3.client(service_name, **kwargs)


def get_resource(service_name: str, **kwargs):
    """Drop-in replacement for ``boto3.resource`` that is account-aware."""
    kwargs = _resolve_region(dict(kwargs))
    account_id = _active_account.get()
    if _needs_cross_account(account_id):
        creds = _assume_role_credentials(account_id)
        kwargs.setdefault("region_name", _DEFAULT_REGION)
        return _boto3.resource(service_name, **{**creds, **kwargs})
    return _boto3.resource(service_name, **kwargs)


# ---------------------------------------------------------------------------
# boto3-compatible shim
# ---------------------------------------------------------------------------

class _Boto3Shim:
    """
    Object exposing the parts of the ``boto3`` API the tools use
    (``client``, ``resource``, ``Session``) while routing ``client``/``resource``
    through the account-aware factory. Any other attribute (e.g. exceptions)
    falls through to the real boto3 module.
    """

    client = staticmethod(get_client)
    resource = staticmethod(get_resource)

    def __getattr__(self, name):
        return getattr(_boto3, name)


# Import this as ``boto3`` in tool modules.
boto3 = _Boto3Shim()
