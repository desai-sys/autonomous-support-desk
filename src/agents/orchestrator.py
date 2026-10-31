"""
AgentGateway — thin layer between the Streamlit UI and AgentCore.

Responsibilities:
  1. Invoke AgentCore via A2A (invoke_agent_runtime)
  2. Fallback to RAG if the agent escalates
  3. Record telemetry (usage, audit, metrics) — all non-fatal
  4. Escalate to human if both agent and RAG fail

This is NOT a router. All domain classification lives inside the AgentCore
container (DomainRouter in src/agents/router.py).
"""

import boto3
import json
import time
from datetime import datetime, timezone
from typing import List, Optional
from src.services.rag_service import RAGService
from src.models.query import Query, Resolution, pad_session_id
from src.core.monitoring import monitoring_manager
from src.core.security import security_manager, User, Permission
from src.core.multi_tenant import tenant_manager
import config
import structlog

logger = structlog.get_logger()


class AgentGateway:
    """
    Thin gateway that calls AgentCore and handles the fallback chain:
    AgentCore → RAG → human escalation.
    """

    def __init__(self):
        self.rag_service = RAGService()
        session = boto3.Session()
        self.agentcore_client = session.client("bedrock-agentcore", region_name="us-east-1")
        self.agent_arn = getattr(config, "AGENT_ARN", "")

    def send(self, query: Query, history: List[str] = None,
             user: User = None) -> Resolution:
        """
        Send query to AgentCore. Falls back to RAG on escalation,
        then to human escalation if RAG also fails.
        """
        start_time = time.time()

        # Non-fatal: record usage
        try:
            tenant_manager.record_usage(query.customer_id, "api_requests")
        except Exception:
            pass

        # Non-fatal: check limits (fail open)
        try:
            if not tenant_manager.check_usage_limits(query.customer_id, "api_request"):
                return Resolution(
                    text="API request limit exceeded for your tenant.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True,
                )
        except Exception:
            pass

        # Non-fatal: audit log
        try:
            security_manager.log_audit_event(
                user_id=user.user_id if user else "anonymous",
                tenant_id=query.customer_id,
                action="query_submitted",
                resource="agent_gateway",
                result="SUCCESS",
                details={"query_length": len(query.text)},
            )
        except Exception:
            pass

        try:
            result = self._invoke_agent(query, history)

            # If agent escalates, try RAG as fallback
            if result.escalate:
                rag = self.rag_service.resolve(query)
                if not rag.escalate:
                    return rag

            duration_ms = (time.time() - start_time) * 1000
            try:
                monitoring_manager.record_resolution_time(
                    duration_ms=duration_ms,
                    resolution_type=result.resolution_type,
                    tenant_id=query.customer_id,
                    success=not result.escalate,
                )
            except Exception:
                pass

            return result

        except Exception as e:
            logger.error("gateway_error", error=str(e))
            return Resolution(
                text=f"Gateway error: {str(e)}",
                resolution_type="escalated",
                confidence=0.0,
                escalate=True,
            )

    def _invoke_agent(self, query: Query, history: List[str] = None) -> Resolution:
        """
        Send query to AgentCore with domain="auto".
        The agent container handles routing internally via DomainRouter.
        """
        try:
            # Build prompt with conversation history for multi-turn context
            if history:
                prompt = (
                    "Conversation so far:\n"
                    + "\n".join(history)
                    + f"\n\nUser: {query.text}"
                )
            else:
                prompt = query.text

            payload = {
                "prompt": prompt,
                "domain": "auto",
                "session_id": query.customer_id,
                "tenant_id": query.customer_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context": query.context or {},
                "history": history or [],
            }

            session_id = pad_session_id(query.customer_id)

            response = self.agentcore_client.invoke_agent_runtime(
                agentRuntimeArn=self.agent_arn,
                runtimeSessionId=session_id,
                payload=json.dumps(payload).encode("utf-8"),
                contentType="application/json",
                accept="application/json",
            )

            stream = response.get("response")
            raw = stream.read() if hasattr(stream, "read") else b""
            result = json.loads(raw.decode("utf-8")) if raw else {}
            response_text = result.get("response", "").strip()

            if not response_text:
                return Resolution(
                    text="No response from agent.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True,
                )

            if "ESCALATE" in response_text.upper():
                return Resolution(
                    text="This request requires human review.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True,
                )

            return Resolution(
                text=response_text,
                resolution_type="action",
                confidence=1.0,
                escalate=False,
                metadata={
                    "domain": result.get("domain", "unknown"),
                    "execution_time_ms": result.get("execution_time_ms", 0),
                    "tools_available": result.get("tools_available", 0),
                },
            )

        except Exception as e:
            logger.error("agent_invocation_error", error=str(e))
            return Resolution(
                text=f"Agent error: {str(e)}",
                resolution_type="escalated",
                confidence=0.0,
                escalate=True,
            )

    def query_knowledge_base(self, query: Query, user: User = None) -> Resolution:
        """
        Knowledge Base mode: query the Bedrock KB directly for historical
        resolutions / how-to lookups. Does NOT invoke the Action Agent, so no
        AWS actions are performed. Returns the RAG result as-is (which escalates
        on low confidence or no match).
        """
        try:
            tenant_manager.record_usage(query.customer_id, "kb_queries")
        except Exception:
            pass

        try:
            security_manager.log_audit_event(
                user_id=user.user_id if user else "anonymous",
                tenant_id=query.customer_id,
                action="kb_query_submitted",
                resource="agent_gateway",
                result="SUCCESS",
                details={"query_length": len(query.text)},
            )
        except Exception:
            pass

        try:
            return self.rag_service.resolve(query)
        except Exception as e:
            logger.error("kb_query_error", error=str(e))
            return Resolution(
                text=f"Knowledge Base error: {str(e)}",
                resolution_type="error",
                confidence=0.0,
                escalate=True,
            )

    # Backward-compatible method alias
    def route_query(self, query: Query, history: List[str] = None,
                    user: User = None) -> Resolution:
        return self.send(query, history, user)


# Backward-compatible aliases so existing imports don't break
AdvancedOrchestrator = AgentGateway
MasterOrchestrator = AgentGateway
