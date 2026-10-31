"""
Console sign-in.

Production (AUTH_MODE=oidc): Streamlit's built-in OIDC login against the Amazon
Cognito user pool created by infra/foundation.yaml. Cognito groups map to roles
(admin, sre, operator, viewer; the highest wins) and the ``custom:tenant_id``
attribute sets the tenant. ui/serve.py writes the OIDC settings into
.streamlit/secrets.toml at container start.

Development (AUTH_MODE=local): a form that accepts any identity. Never enable
this on a deployed stack.
"""

from typing import Optional

import streamlit as st

import config
from src.core.security import User, UserRole, role_from_groups, security_manager


def _claim(name: str, default=None):
    try:
        return st.user.get(name, default)
    except Exception:
        return default


def current_user() -> Optional[User]:
    if config.AUTH_MODE == "oidc":
        if not st.user.is_logged_in:
            return None
        groups = _claim("cognito:groups") or []
        if isinstance(groups, str):
            groups = [groups]
        email = _claim("email") or _claim("cognito:username") or "unknown"
        user = User.build(
            user_id=_claim("sub") or email,
            email=email,
            role=role_from_groups(groups),
            tenant_id=_claim("custom:tenant_id") or config.DEFAULT_TENANT_ID,
        )
        if not st.session_state.get("_login_audited"):
            security_manager.log_audit_event(user.user_id, user.tenant_id, "login", "console", "SUCCESS",
                                             details={"email": email, "role": user.role.value})
            st.session_state._login_audited = True
        return user
    return st.session_state.get("local_user")


def sign_in_page() -> None:
    st.title("🛡️ CloudOps Command Center")
    st.caption("Autonomous SRE platform for your AWS accounts")
    if config.AUTH_MODE == "oidc":
        st.write("Sign in with your organisation account to continue.")
        if st.button("Sign in", type="primary"):
            st.login()
        return

    st.warning("Development sign-in: any identity is accepted. Do not use on a deployed stack.")
    with st.form("local_signin"):
        email = st.text_input("Email", value="dev@example.com")
        tenant = st.text_input("Tenant ID", value=config.DEFAULT_TENANT_ID)
        role = st.selectbox("Role", [r.value for r in UserRole], index=3)
        if st.form_submit_button("Sign in", type="primary"):
            st.session_state.local_user = User.build(
                user_id=email.split("@")[0], email=email, role=UserRole(role),
                tenant_id=tenant.strip() or config.DEFAULT_TENANT_ID,
            )
            st.rerun()


def sign_out() -> None:
    st.session_state.clear()
    if config.AUTH_MODE == "oidc":
        st.logout()
    else:
        st.rerun()
