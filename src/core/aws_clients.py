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
import jmespath
from botocore.utils import set_value_from_jmespath

import config
from config import AWS_REGION as _DEFAULT_REGION


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


def base_account_id() -> Optional[str]:
    """Account id the runtime itself runs in (None if it can't be determined)."""
    return _base_account_id()


_UNSET = object()
_base_account: object = _UNSET


def _base_account_id() -> Optional[str]:
    """Account id the runtime itself is running as (cached best-effort)."""
    global _base_account
    if _base_account is _UNSET:
        try:
            _base_account = _boto3.client(
                "sts", region_name=_DEFAULT_REGION
            ).get_caller_identity()["Account"]
        except Exception:
            _base_account = None
    return _base_account  # type: ignore[return-value]


def _assume_role_credentials(account_id: str) -> dict:
    """
    Return valid temporary credentials for ``account_id``, assuming
    ``CROSS_ACCOUNT_ROLE_NAME`` in it. Credentials are cached until near expiry.
    """
    from src.core import request_context, tenancy
    tenant = request_context.tenant_id()
    cache_key = (tenant, account_id, config.CROSS_ACCOUNT_ROLE_NAME)
    now = time.time()
    with _creds_lock:
        entry = _creds_cache.get(cache_key)
        if entry and entry["expiry"] - _EXPIRY_SKEW_SECONDS > now:
            return entry["credentials"]

    sts = _boto3.client("sts", region_name=_DEFAULT_REGION)
    role_arn = f"arn:aws:iam::{account_id}:role/{config.CROSS_ACCOUNT_ROLE_NAME}"
    params = {
        "RoleArn": role_arn,
        "RoleSessionName": f"cloudops-{config.RUNTIME_MODE}-{account_id}",
        "DurationSeconds": 3600,
    }
    external_id = tenancy.external_id_for(tenant)
    if external_id:
        params["ExternalId"] = external_id
    resp = sts.assume_role(**params)
    creds = resp["Credentials"]
    scoped = {
        "aws_access_key_id": creds["AccessKeyId"],
        "aws_secret_access_key": creds["SecretAccessKey"],
        "aws_session_token": creds["SessionToken"],
    }
    expiry = creds["Expiration"].timestamp()
    with _creds_lock:
        _creds_cache[cache_key] = {"credentials": scoped, "expiry": expiry}
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


# ---------------------------------------------------------------------------
# Complete results: follow pagination automatically
# ---------------------------------------------------------------------------
# AWS list/describe calls return one page (often 50-100 items). A tool that reads
# only that page reports a partial list as if it were complete. Clients from this
# module therefore follow pagination themselves, up to a cap, and record when the
# cap cut a result short so the tool result can say so.

MAX_AUTO_PAGES = 25
# Operations that page through stored data rather than resource listings; callers
# bound these explicitly (time windows, limits), so they keep single-page behaviour.
_SINGLE_PAGE_OPERATIONS = frozenset({
    "filter_log_events", "get_log_events", "lookup_events", "scan", "query", "list_objects",
    "list_objects_v2", "list_object_versions", "list_multipart_uploads", "list_parts",
    "get_query_results", "get_records", "get_metric_data", "get_cost_and_usage",
    "get_cost_and_usage_with_resources", "execute_statement", "search_resources",
})

_truncations: "contextvars.ContextVar[Optional[list]]" = contextvars.ContextVar(
    "cloudops_truncations", default=None)


@contextlib.contextmanager
def track_truncations():
    """Collect the operations whose results were cut short at the page cap inside the block."""
    found: list = []
    token = _truncations.set(found)
    try:
        yield found
    finally:
        _truncations.reset(token)


def _all_pages(client, name: str, method, kwargs: dict):
    paginator = client.get_paginator(name)
    cfg = paginator._pagination_cfg
    as_list = lambda v: [v] if isinstance(v, str) else list(v or [])   # noqa: E731
    input_tokens, limit_key = as_list(cfg.get("input_token")), cfg.get("limit_key")
    # A caller that pages or limits explicitly knows what it wants.
    if any(t in kwargs for t in input_tokens) or (limit_key and limit_key in kwargs):
        return method(**kwargs)

    merged, pages, more = None, 0, False
    for page in paginator.paginate(**kwargs):
        pages += 1
        if merged is None:
            merged = page
        else:
            for expression in paginator.result_keys:
                value, existing = expression.search(page), expression.search(merged)
                if isinstance(value, list) and isinstance(existing, list):
                    existing.extend(value)
                elif value is not None and existing is None:
                    set_value_from_jmespath(merged, expression.expression, value)
        if pages >= MAX_AUTO_PAGES:
            more = any(jmespath.search(t, page) for t in as_list(cfg.get("output_token")))
            break
    if merged is None:
        return method(**kwargs)
    if more:
        found = _truncations.get()
        if found is not None:
            found.append(f"{client.meta.service_model.service_name}:{name}")
    else:
        # The result is complete: drop continuation markers so callers don't page again.
        for token in as_list(cfg.get("output_token")) + ["NextToken", "nextToken", "NextMarker", "Marker"]:
            if token.isidentifier():
                merged.pop(token, None)
        if merged.get("IsTruncated") is True:
            merged["IsTruncated"] = False
    return merged


class _PagingClient:
    """A boto3 client whose pageable operations return every page (up to MAX_AUTO_PAGES)."""

    def __init__(self, client):
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name.startswith("_") or name in _SINGLE_PAGE_OPERATIONS or not callable(attr):
            return attr
        try:
            pageable = self._client.can_paginate(name)
        except Exception:
            pageable = False
        if not pageable:
            return attr

        def call(*args, **kwargs):
            return attr(*args, **kwargs) if args else _all_pages(self._client, name, attr, kwargs)
        call.__name__ = name
        return call

    def __setattr__(self, name, value):
        setattr(self._client, name, value)


def get_client(service_name: str, **kwargs):
    """Drop-in replacement for ``boto3.client``: account-aware, and returns complete lists."""
    kwargs = _resolve_region(dict(kwargs))
    account_id = _active_account.get()
    if account_id and _needs_cross_account(account_id):
        creds = _assume_role_credentials(account_id)
        kwargs.setdefault("region_name", _DEFAULT_REGION)
        return _PagingClient(_boto3.client(service_name, **{**creds, **kwargs}))
    return _PagingClient(_boto3.client(service_name, **kwargs))


def get_base_client(service_name: str, **kwargs):
    """
    Client that always uses the runtime's own (base) account credentials,
    regardless of the active target account. Used for platform-owned resources
    such as the uploaded-artifact bucket, which lives in the base account even
    when a deployment targets another account.
    """
    kwargs = _resolve_region(dict(kwargs))
    return _boto3.client(service_name, **kwargs)


def get_resource(service_name: str, **kwargs):
    """Drop-in replacement for ``boto3.resource`` that is account-aware."""
    kwargs = _resolve_region(dict(kwargs))
    account_id = _active_account.get()
    if account_id and _needs_cross_account(account_id):
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
