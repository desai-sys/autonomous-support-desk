"""Shared console resources and helpers."""

import streamlit as st

from src.agents.gateway import AgentGateway
from src.core.accounts import AccountRegistry
from src.services.artifact_service import ArtifactService
from src.services.ticket_service import TicketService


@st.cache_resource
def gateway() -> AgentGateway:
    return AgentGateway()


@st.cache_resource
def tickets() -> TicketService:
    return TicketService()


@st.cache_resource
def artifacts() -> ArtifactService:
    return ArtifactService()


@st.cache_resource
def account_registry() -> AccountRegistry:
    return AccountRegistry()


@st.cache_data(ttl=60, show_spinner=False)
def managed_accounts() -> dict:
    """{account_id: name} of registered accounts (cached for a minute)."""
    return {a: info.get("name") or a for a, info in account_registry().list().items()}


def account_label(account_id: str) -> str:
    if not account_id:
        return "Platform account"
    name = managed_accounts().get(account_id)
    return f"{name} ({account_id})" if name and name != account_id else account_id
