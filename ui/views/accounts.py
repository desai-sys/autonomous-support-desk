"""Accounts (admin): register the AWS accounts the agent may act on."""

import os

import streamlit as st

import config
from src.core.security import User, security_manager
from ui.common import account_registry, gateway, managed_accounts


def _setup_command() -> str:
    role_arn = os.environ.get("AGENT_RUNTIME_ROLE_ARN", "<agent-runtime-role-arn>")
    external_id = config.CROSS_ACCOUNT_EXTERNAL_ID or "<external-id>"
    return (
        "aws cloudformation deploy \\\n"
        "  --template-file infra/cross_account_role.yaml \\\n"
        "  --stack-name cloudops-cross-account \\\n"
        "  --capabilities CAPABILITY_NAMED_IAM \\\n"
        f"  --parameter-overrides AgentRuntimeRoleArn={role_arn} ExternalId={external_id}"
    )


def render(user: User) -> None:
    st.title("🌐 Accounts")
    st.caption("The agent only assumes roles in accounts registered here (plus the platform account).")

    with st.expander("How to connect an account", expanded=not managed_accounts()):
        st.markdown(
            "1. In the target account (or for many accounts at once, as a CloudFormation StackSet "
            "from your management account), deploy `infra/cross_account_role.yaml`:")
        st.code(_setup_command(), language="bash")
        st.markdown("2. Register the account below, then use **Test access**.")

    with st.form("add_account", clear_on_submit=True):
        c_id, c_name, c_btn = st.columns([2, 2, 1])
        account_id = c_id.text_input("Account ID", placeholder="123456789012")
        name = c_name.text_input("Name", placeholder="prod")
        if c_btn.form_submit_button("Add", type="primary"):
            try:
                account_registry().add(account_id, name, user.user_id)
                security_manager.log_audit_event(user.user_id, user.tenant_id, "account_added",
                                                 account_id, "SUCCESS", details={"name": name})
                managed_accounts.clear()
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    accounts = account_registry().list()
    if not accounts:
        st.info("No accounts registered yet.")
        return
    for account_id, info in sorted(accounts.items(), key=lambda kv: kv[1].get("name", "")):
        with st.container(border=True):
            c_info, c_test, c_del = st.columns([4, 1, 1])
            c_info.markdown(f"**{info.get('name') or account_id}** · `{account_id}`")
            c_info.caption(f"Added by {info.get('added_by', 'config')} {info.get('added_at', '')[:10]}"
                           if info.get("source") == "console" else "From MANAGED_ACCOUNTS (read-only)")
            if c_test.button("Test access", key=f"test-{account_id}"):
                with st.spinner("Assuming role..."):
                    result = gateway().check_account(account_id, user)
                (st.success if result.get("ok") else st.error)(result.get("response", ""))
            if info.get("source") == "console" and c_del.button("Remove", key=f"del-{account_id}"):
                account_registry().remove(account_id)
                security_manager.log_audit_event(user.user_id, user.tenant_id, "account_removed",
                                                 account_id, "SUCCESS")
                managed_accounts.clear()
                st.rerun()
