"""
Tests for AgentGateway (backward-compatible aliases: MasterOrchestrator, AdvancedOrchestrator).
Tests the fallback chain: AgentCore → RAG → human escalation.
"""
import json
import pytest
from unittest.mock import MagicMock, patch
from src.agents.orchestrator import AgentGateway, MasterOrchestrator, AdvancedOrchestrator
from src.models.query import Query, Resolution


def make_query(text="SSH is down on i-0abc123"):
    return Query(text=text, customer_id="demo-operator-00000000000000000000")


def make_resolution(resolution_type="action", confidence=1.0, escalate=False, text="Fixed"):
    return Resolution(
        text=text,
        resolution_type=resolution_type,
        confidence=confidence,
        escalate=escalate
    )


def _agent_stream(text: str, domain="compute", tools=52, time_ms=1234):
    stream = MagicMock()
    stream.read.return_value = json.dumps({
        'response': text,
        'domain': domain,
        'execution_time_ms': time_ms,
        'tools_available': tools,
    }).encode()
    return {'response': stream}


@pytest.fixture
def gateway():
    with patch("boto3.Session") as mock_session_cls, \
         patch("src.agents.orchestrator.RAGService") as mock_rag_cls, \
         patch("src.agents.orchestrator.tenant_manager") as mock_tenant, \
         patch("src.agents.orchestrator.security_manager") as mock_security, \
         patch("src.agents.orchestrator.monitoring_manager") as mock_monitoring:

        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_agentcore = MagicMock()
        mock_session.client.return_value = mock_agentcore

        mock_rag = MagicMock()
        mock_rag_cls.return_value = mock_rag

        mock_tenant.check_usage_limits.return_value = True
        mock_tenant.record_usage = MagicMock()

        gw = AgentGateway()
        gw.agentcore_client = mock_agentcore
        gw.rag_service = mock_rag

        return gw, mock_agentcore, mock_rag


class TestGatewayRouting:

    def test_successful_agent_response(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("Port 22 opened")

        result = gw.send(make_query("SSH is down"))

        agentcore.invoke_agent_runtime.assert_called_once()
        assert result.resolution_type == "action"
        assert "Port 22" in result.text

    def test_success_does_not_call_rag(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("Fixed")

        gw.send(make_query())
        rag.resolve.assert_not_called()

    def test_escalation_falls_back_to_rag(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("ESCALATE - no tool")
        rag.resolve.return_value = make_resolution("rag", 0.8, False, "Historical fix found")

        result = gw.send(make_query())

        agentcore.invoke_agent_runtime.assert_called_once()
        rag.resolve.assert_called_once()
        assert result.resolution_type == "rag"
        assert result.text == "Historical fix found"

    def test_both_escalate_returns_escalation(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("ESCALATE")
        rag.resolve.return_value = make_resolution("escalated", 0.0, True, "No KB match")

        result = gw.send(make_query())

        assert result.resolution_type == "escalated"
        assert result.escalate is True

    def test_agent_exception_escalates(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.side_effect = Exception("Connection timeout")
        rag.resolve.return_value = make_resolution("escalated", 0.0, True, "No match")

        result = gw.send(make_query())

        assert result.escalate is True
        assert result.confidence == 0.0

    def test_history_passed_in_payload(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("Fixed")

        history = ["User: SSH down", "Assistant: Port 22 missing. Open it?"]
        gw.send(make_query("Yes"), history=history)

        call_kwargs = agentcore.invoke_agent_runtime.call_args[1]
        payload = json.loads(call_kwargs['payload'].decode())
        assert "Conversation so far" in payload['prompt']
        assert "SSH down" in payload['prompt']

    def test_no_history_sends_plain_prompt(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("Done")

        gw.send(make_query("Check my EC2"), history=None)

        call_kwargs = agentcore.invoke_agent_runtime.call_args[1]
        payload = json.loads(call_kwargs['payload'].decode())
        assert payload['prompt'] == "Check my EC2"


class TestBackwardCompatibility:

    def test_master_orchestrator_alias(self):
        assert MasterOrchestrator is AgentGateway

    def test_advanced_orchestrator_alias(self):
        assert AdvancedOrchestrator is AgentGateway

    def test_route_query_alias(self, gateway):
        gw, agentcore, rag = gateway
        agentcore.invoke_agent_runtime.return_value = _agent_stream("Done")

        result = gw.route_query(make_query("Hello"))
        assert result.resolution_type == "action"
