"""Audit log: who asked for, approved and ran what."""

import streamlit as st

from src.core.security import User, security_manager


def render(user: User) -> None:
    st.title("📜 Audit log")
    st.caption(f"Most recent events for tenant `{user.tenant_id}`.")
    events = security_manager.get_audit_logs(user.tenant_id, limit=300)
    if not events:
        st.info("No events yet.")
        return
    actions = sorted({e.get("action", "") for e in events})
    chosen = st.multiselect("Event types", actions, default=[])
    rows = [
        {"time (UTC)": e.get("timestamp", "")[:19].replace("T", " "), "user": e.get("user_id"),
         "event": e.get("action"), "resource": e.get("resource"), "result": e.get("result"),
         "details": str(e.get("details") or "")}
        for e in events if not chosen or e.get("action") in chosen
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
