"""
CloudOps Action Agent — DEPRECATED in favor of A2A communication.

DEPRECATION NOTICE:
This wrapper class is no longer used in the main application flow.
The AgentGateway now uses Agent-to-Agent (A2A) communication to directly
invoke the Action Agent via bedrock-agentcore.invoke_agent_runtime().

The agent logic and all 275+ SRE tools live in src/agentcore_entrypoint.py
(imported from src/tools/). This file is kept for backward compatibility and
testing purposes only (see tests/test_agents/test_action_agent.py).

For the active A2A implementation, see:
- src/agents/orchestrator.py: AgentGateway._invoke_agent()
"""

import boto3
import json
import os
from src.models.query import Query, Resolution, pad_session_id


class ActionAgent:
    """
    Calls the deployed AgentCore runtime to resolve infrastructure alerts.
    Session memory is managed by AgentCore using customer_id as session_id.
    """

    def __init__(self):
        self.agent_arn = os.environ.get(
            "AGENTCORE_AGENT_ARN",
            "arn:aws:bedrock-agentcore:us-east-1:741448928336:runtime/src_agentcore_entrypoint-eWsFZM3S6Y"
        )
        self.client = boto3.client("bedrock-agentcore", region_name="us-east-1")

    def resolve(self, query: Query, history: list = None) -> Resolution:
        """
        Invoke the AgentCore agent with the user's query.

        Args:
            query: Operator query describing the infrastructure issue
            history: Optional prior conversation turns (prepended to prompt)

        Returns:
            Resolution with the agent's response or escalation
        """
        if history:
            prompt = "Previous Conversation:\n" + "\n".join(history) + f"\n\nCurrent Request: {query.text}"
        else:
            prompt = query.text

            # runtimeSessionId must be >= 33 chars (AgentCore requirement)
            session_id = pad_session_id(query.customer_id)

        try:
            response = self.client.invoke_agent_runtime(
                agentRuntimeArn=self.agent_arn,
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": prompt, "session_id": query.customer_id}).encode('utf-8'),
                contentType="application/json",
                accept="application/json"
            )

            # response is a StreamingBody under the 'response' key
            stream = response.get("response")
            raw = stream.read() if hasattr(stream, "read") else b""
            result = json.loads(raw.decode('utf-8')) if raw else {}
            response_text = result.get("response", "").strip()

            if not response_text:
                return Resolution(
                    text="No response received from AgentCore.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True
                )

            if "ESCALATE" in response_text.upper():
                return Resolution(
                    text="No appropriate tool found for this service. Escalating to fallback routing.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True
                )

            is_hitl = "[HITL]" in response_text
            return Resolution(
                text=response_text,
                resolution_type="action",
                confidence=1.0,
                escalate=False
            )

        except Exception as e:
            return Resolution(
                text=f"Action agent error: {str(e)}",
                resolution_type="escalated",
                confidence=0.0,
                escalate=True
            )
