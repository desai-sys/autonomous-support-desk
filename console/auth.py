"""
Console sign-in.

AUTH_MODE=oidc (deployed): OpenID Connect authorization-code flow against the
Cognito user pool. The callback stores the user's claims in a signed, HTTP-only
session cookie. Cognito groups map to roles (highest wins) and the
``custom:tenant_id`` attribute sets the tenant.

AUTH_MODE=local (development): POST /auth/dev-login with any identity.
"""

from typing import Optional

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

import config
from src.core.security import User, UserRole, role_from_groups, security_manager

router = APIRouter()
oauth = OAuth()
if config.AUTH_MODE == "oidc":
    oauth.register(
        "cognito",
        client_id=config.OIDC_CLIENT_ID,
        client_secret=config.OIDC_CLIENT_SECRET,
        server_metadata_url=config.OIDC_METADATA_URL,
        client_kwargs={"scope": "openid email profile"},
    )


def session_user(request: Request) -> Optional[User]:
    data = request.session.get("user")
    if not data:
        return None
    try:
        return User.build(data["sub"], data["email"], UserRole(data["role"]), data["tenant"])
    except (KeyError, ValueError):
        return None


def require_user(request: Request) -> User:
    user = session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


def _remember(request: Request, sub: str, email: str, role: UserRole, tenant: str) -> None:
    request.session["user"] = {"sub": sub, "email": email, "role": role.value, "tenant": tenant}
    security_manager.log_audit_event(sub, tenant, "login", "console", "SUCCESS",
                                     details={"email": email, "role": role.value})


@router.get("/auth/login")
async def login(request: Request):
    if config.AUTH_MODE != "oidc":
        return RedirectResponse("/")
    return await oauth.cognito.authorize_redirect(request, config.OIDC_REDIRECT_URI)


@router.get("/oauth2callback")
async def callback(request: Request):
    token = await oauth.cognito.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    groups = claims.get("cognito:groups") or []
    if isinstance(groups, str):
        groups = [groups]
    email = claims.get("email") or claims.get("cognito:username") or "unknown"
    _remember(request, claims.get("sub") or email, email, role_from_groups(groups),
              claims.get("custom:tenant_id") or config.DEFAULT_TENANT_ID)
    return RedirectResponse("/")


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    if config.AUTH_MODE == "oidc" and config.OIDC_LOGOUT_URL:
        return RedirectResponse(config.OIDC_LOGOUT_URL)
    return RedirectResponse("/")


class DevLogin(BaseModel):
    email: str
    tenant: str = config.DEFAULT_TENANT_ID
    role: UserRole = UserRole.ADMIN


@router.post("/auth/dev-login")
async def dev_login(body: DevLogin, request: Request):
    if config.AUTH_MODE != "local":
        raise HTTPException(status_code=404)
    _remember(request, body.email.split("@")[0], body.email, body.role,
              body.tenant.strip() or config.DEFAULT_TENANT_ID)
    return {"ok": True}
