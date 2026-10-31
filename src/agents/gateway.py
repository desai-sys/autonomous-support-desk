"""
AgentGateway — the console's client for the AgentCore runtime.

Sends chat turns and approval decisions to the agent (invoke_agent_runtime),
falls back to the knowledge base when the agent escalates, and handles
rejections and pending-action listings directly against DynamoDB.
"""

import json
import time
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

import boto3
import structlog
from botocore.config import Config

import config
from src.core.approvals import ActionStore, reject_action
from src.core.monitoring import metrics
from src.core.security import User, security_manager
from src.models.query import Query, Resolution, pad_session_id
from src.services.rag_service import RAGService

logger = structlog.get_logger()


@dataclass
class ChatResult:
    text: str
    resolution_type: str               # action | rag | escalated | error
    escalate: bool = False
    pending_actions: List[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class AgentGateway:
    def __init__(self):
        # Long read timeout: deployments can run for minutes. No retries, so a
        # timed-out request is never sent twice (execution is idempotent anyway).
        self.client = boto3.client(
            "bedrock-agentcore", region_name=config.AWS_REGION,
            config=Config(read_timeout=900, connect_timeout=10, retries={"max_attempts": 1}),
        )
        self.rag = RAGService()
        self.actions = ActionStore()

    # ── Agent calls ───────────────────────────────────────────────────────────

    def _invoke(self, payload: dict, user: User, session_id: str) -> dict:
        payload = {
            **payload,
            "session_id": session_id,
            "tenant_id": user.tenant_id,
            "user": {"id": user.user_id, "email": user.email, "role": user.role.value},
        }
        if config.AGENT_ENDPOINT_URL:
            return self._invoke_local(payload)
        response = self.client.invoke_agent_runtime(
            agentRuntimeArn=config.AGENT_ARN,
            runtimeSessionId=pad_session_id(session_id),
            payload=json.dumps(payload).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        body = response.get("response")
        raw = body.read() if hasattr(body, "read") else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    @staticmethod
    def _invoke_local(payload: dict) -> dict:
        request = urllib.request.Request(
            config.AGENT_ENDPOINT_URL.rstrip("/") + "/invocations",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=900) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")

    def chat(self, text: str, user: User, session_id: str, history: Optional[List[str]] = None,
             accounts: Optional[List[str]] = None, region: Optional[str] = None,
             artifact: Optional[dict] = None) -> ChatResult:
        start = time.time()
        prompt = text
        if history:
            prompt = "Conversation so far:\n" + "\n".join(history) + f"\n\nUser: {text}"
        payload = {"mode": "chat", "prompt": prompt, "user_message": text,
                   "history": history or []}
        if accounts is not None:
            payload["account_ids"] = list(accounts)
        if region:
            payload["region"] = region
        if artifact:
            payload["artifact"] = artifact

        security_manager.log_audit_event(user.user_id, user.tenant_id, "query_submitted",
                                         "agent", "SUCCESS",
                                         details={"length": len(text), "accounts": accounts or []})
        try:
            result = self._invoke(payload, user, session_id)
        except Exception as e:
            logger.error("agent_invocation_failed", error=str(e))
            metrics.request("console-chat", (time.time() - start) * 1000, False)
            return ChatResult(text=f"The agent is unavailable: {e}", resolution_type="error",
                              escalate=True)

        text_out = (result.get("response") or "").strip() or "No response from the agent."
        meta = {k: result.get(k) for k in ("domain", "accounts", "per_account",
                                          "tools_available", "execution_time_ms")}
        pending = result.get("pending_actions") or []
        escalate = bool(result.get("escalate")) or "error" in result

        if escalate and not artifact and not pending:
            rag = self.rag.resolve(Query(text=text[:2000], customer_id=user.tenant_id))
            if not rag.escalate:
                return ChatResult(text=rag.text, resolution_type="rag", metadata=meta)

        metrics.request("console-chat", (time.time() - start) * 1000, "error" not in result)
        return ChatResult(text=text_out, resolution_type="escalated" if escalate else "action",
                          escalate=escalate, pending_actions=pending, metadata=meta)

    def approve(self, action_ids: List[str], user: User, session_id: str) -> dict:
        try:
            return self._invoke({"mode": "execute", "action_ids": action_ids}, user, session_id)
        except Exception as e:
            logger.error("execute_invocation_failed", error=str(e))
            return {"response": f"The agent is unavailable: {e}", "results": [], "error": "Unavailable"}

    def check_account(self, account_id: str, user: User) -> dict:
        try:
            return self._invoke({"mode": "check_account", "account_id": account_id}, user,
                                f"account-check-{account_id}")
        except Exception as e:
            return {"ok": False, "response": f"The agent is unavailable: {e}"}

    def reject(self, action_id: str, user: User) -> bool:
        ok = reject_action(action_id, tenant_id=user.tenant_id, user=user.user_id, store=self.actions)
        if ok:
            security_manager.log_audit_event(user.user_id, user.tenant_id, "action_rejected",
                                             action_id, "SUCCESS")
        return ok

    def pending_actions(self, user: User, session_id: str) -> List[dict]:
        try:
            return self.actions.list_for_session(user.tenant_id, session_id)
        except Exception as e:
            logger.error("pending_actions_failed", error=str(e))
            return []

    # ── Knowledge base ────────────────────────────────────────────────────────

    def query_knowledge_base(self, text: str, user: User) -> Resolution:
        security_manager.log_audit_event(user.user_id, user.tenant_id, "kb_query_submitted",
                                         "knowledge_base", "SUCCESS")
        return self.rag.resolve(Query(text=text[:2000], customer_id=user.tenant_id))
