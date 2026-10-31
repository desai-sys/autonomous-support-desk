"""
Tests for ActionAgent.
Mocks bedrock-agentcore client to test prompt construction,
ESCALATE detection, HITL detection, and error handling.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from src.agents.action_agent import ActionAgent
from src.models.query import Query


def make_query(text="SSH is down on i-0abc123", customer_id="demo-operator-00000000000000000000"):
    return Query(text=text, customer_id=customer_id)


def make_stream_response(text: str):
    """Simulate AgentCore StreamingBody response."""
    raw = json.dumps({"response": text}).encode("utf-8")
    stream = MagicMock()
    stream.read.return_value = raw
    return {"response": stream}


@pytest.fixture
def agent():
    with patch("boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        a = ActionAgent()
        a.client = mock_client
        return a


class TestActionAgentResolve:

    def test_successful_action_resolution(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response(
            "Port 22 opened on sg-abc123."
        )
        result = agent.resolve(make_query())
        assert result.resolution_type == "action"
        assert result.confidence == 1.0
        assert result.escalate is False
        assert "Port 22" in result.text

    def test_escalate_keyword_triggers_escalation(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("ESCALATE")
        result = agent.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.escalate is True
        assert result.confidence == 0.0

    def test_escalate_case_insensitive(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("escalate")
        result = agent.resolve(make_query())
        assert result.resolution_type == "escalated"

    def test_escalate_in_longer_response(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response(
            "I don't have a tool for this. ESCALATE to human SRE."
        )
        result = agent.resolve(make_query())
        assert result.resolution_type == "escalated"

    def test_hitl_response_still_returns_action(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response(
            "[HITL] Port 22 is missing. Should I open it?"
        )
        result = agent.resolve(make_query())
        assert result.resolution_type == "action"
        assert result.escalate is False
        assert "[HITL]" in result.text

    def test_empty_response_escalates(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("")
        result = agent.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.escalate is True
        assert "No response" in result.text

    def test_exception_returns_escalated(self, agent):
        agent.client.invoke_agent_runtime.side_effect = Exception("Connection timeout")
        result = agent.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.escalate is True
        assert "Connection timeout" in result.text

    def test_prompt_without_history(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("Fixed")
        query = make_query("Lambda throttling on payment-processor")
        agent.resolve(query, history=None)
        call_kwargs = agent.client.invoke_agent_runtime.call_args[1]
        payload = json.loads(call_kwargs["payload"].decode("utf-8"))
        assert payload["prompt"] == "Lambda throttling on payment-processor"

    def test_prompt_with_history_prepended(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("Fixed")
        query = make_query("Yes")
        history = ["User: SSH is down on i-0abc123", "Assistant: Port 22 missing. Open it?"]
        agent.resolve(query, history=history)
        call_kwargs = agent.client.invoke_agent_runtime.call_args[1]
        payload = json.loads(call_kwargs["payload"].decode("utf-8"))
        assert "Previous Conversation:" in payload["prompt"]
        assert "SSH is down" in payload["prompt"]
        assert "Current Request: Yes" in payload["prompt"]

    def test_runtime_session_id_is_customer_id(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("Fixed")
        query = make_query(customer_id="demo-operator-00000000000000000000")
        agent.resolve(query)
        call_kwargs = agent.client.invoke_agent_runtime.call_args[1]
        assert call_kwargs["runtimeSessionId"] == "demo-operator-00000000000000000000"

    def test_agent_arn_used_in_call(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("Fixed")
        agent.resolve(make_query())
        call_kwargs = agent.client.invoke_agent_runtime.call_args[1]
        assert "bedrock-agentcore" in call_kwargs["agentRuntimeArn"]

    def test_content_type_and_accept_headers(self, agent):
        agent.client.invoke_agent_runtime.return_value = make_stream_response("Fixed")
        agent.resolve(make_query())
        call_kwargs = agent.client.invoke_agent_runtime.call_args[1]
        assert call_kwargs["contentType"] == "application/json"
        assert call_kwargs["accept"] == "application/json"

    def test_env_var_overrides_default_arn(self):
        with patch.dict("os.environ", {"AGENTCORE_AGENT_ARN": "arn:aws:bedrock-agentcore:us-east-1:999:runtime/custom"}):
            with patch("boto3.client"):
                a = ActionAgent()
                assert a.agent_arn == "arn:aws:bedrock-agentcore:us-east-1:999:runtime/custom"
