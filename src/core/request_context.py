"""
Who the current agent request is for.

The entrypoint binds the caller's tenant, user and conversation around each
chat or execute request so tools (e.g. the app builder, which stores drafts per
tenant) can read them without extra parameters the model could get wrong.
"""

import contextlib
import contextvars
from typing import Optional

_tenant: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("cloudops_tenant", default=None)
_user: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("cloudops_user", default=None)
_session: "contextvars.ContextVar[Optional[str]]" = contextvars.ContextVar("cloudops_session", default=None)


@contextlib.contextmanager
def bind(tenant_id: str, user_id: str, session_id: str = ""):
    tokens = (_tenant.set(tenant_id), _user.set(user_id), _session.set(session_id))
    try:
        yield
    finally:
        _tenant.reset(tokens[0])
        _user.reset(tokens[1])
        _session.reset(tokens[2])


def tenant_id() -> str:
    import config
    return _tenant.get() or config.DEFAULT_TENANT_ID


def user_id() -> str:
    return _user.get() or "unknown"


def session_id() -> str:
    return _session.get() or ""
