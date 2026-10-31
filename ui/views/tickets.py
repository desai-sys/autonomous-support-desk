"""Tickets: escalations the agent could not resolve. Resolutions train the knowledge base."""

import streamlit as st

from src.core.security import Permission, User
from ui.common import tickets

URGENCY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}


def render(user: User) -> None:
    st.title("🎫 Tickets")
    st.caption("Requests the agent escalated. Closing a ticket with a resolution adds it to the "
               "knowledge base at the next learning-loop run (hourly).")

    if user.can(Permission.TRIGGER_LEARNING):
        with st.sidebar:
            if st.button("🔄 Sync knowledge base now", use_container_width=True):
                from src.jobs.learning_loop import sync_closed_tickets
                with st.spinner("Adding resolved tickets to the knowledge base..."):
                    try:
                        result = sync_closed_tickets()
                        st.success(f"Added {result['uploaded']} resolution(s).")
                    except Exception as e:
                        st.error(f"Sync failed: {e}")

    try:
        items = tickets().list_open(user.tenant_id)
    except Exception as e:
        st.error(f"Could not load tickets: {e}")
        return

    cols = st.columns(4)
    cols[0].metric("Open", len(items))
    for col, level in zip(cols[1:], ("critical", "high", "medium")):
        col.metric(f"{URGENCY_ICON[level]} {level.title()}", sum(t.get("urgency") == level for t in items))

    if not items:
        st.success("No open tickets. ✅")
        return

    for t in items:
        icon = URGENCY_ICON.get(t.get("urgency"), "⚪")
        label = f"{icon} {t['ticket_id']} · {t.get('category', 'General')} · {t.get('created_at', '')[:16]}"
        with st.expander(label, expanded=t.get("urgency") == "critical"):
            st.markdown("**Request**")
            st.text(t.get("query_text", ""))
            if t.get("agent_context"):
                st.markdown("**Agent response**")
                st.text(t["agent_context"][:3000])
            st.caption(f"Opened by {t.get('created_by') or 'unknown'} · status {t.get('status')}")
            if user.can(Permission.RESOLVE_TICKETS):
                with st.form(f"resolve-{t['ticket_id']}"):
                    resolution = st.text_area("Resolution (what fixed it — this trains the AI)",
                                              height=120)
                    if st.form_submit_button("Close ticket", type="primary"):
                        try:
                            if tickets().close(t["ticket_id"], tenant_id=user.tenant_id,
                                               resolution=resolution, resolved_by=user.user_id):
                                st.success("Closed.")
                                st.rerun()
                            else:
                                st.error("Ticket not found.")
                        except ValueError as e:
                            st.error(str(e))
