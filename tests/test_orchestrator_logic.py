"""
AgentGateway tests — all AWS calls mocked.
Tests the fallback chain: AgentCore → RAG → escalation.

Run with: python -m pytest tests/test_orchestrator_logic.py -v
"""
import json
import sys
import unittest.mock as mock
import pytest

# ── Mock all AWS / external deps before any project imports ──────────────────
for mod in ['boto3', 'botocore', 'botocore.exceptions', 'structlog']:
    sys.modules[mod] = mock.MagicMock()

import structlog
structlog.get_logger = mock.MagicMock(return_value=mock.MagicMock())
structlog.configure = mock.MagicMock()
structlog.stdlib = mock.MagicMock()
structlog.processors = mock.MagicMock()

import src.core.monitoring as mon
mon.monitor_performance = lambda name: (lambda f: f)
mon.monitoring_manager = mock.MagicMock()

import src.core.security as sec
sec.security_manager = mock.MagicMock()
sec.security_manager.check_permission.return_value = True
sec.security_manager.log_audit_event = mock.MagicMock()

import src.core.multi_tenant as mt
mt.tenant_manager = mock.MagicMock()
mt.tenant_manager.check_usage_limits.return_value = True
mt.tenant_manager.record_usage = mock.MagicMock()
mt.tenant_manager.get_tenant_kb_id.return_value = None

from src.models.query import Query, Resolution
from src.agents.orchestrator import AgentGateway


def _make_gateway():
    """Build a gateway with all AWS clients mocked."""
    gw = AgentGateway.__new__(AgentGateway)
    gw.rag_service = mock.MagicMock()
    gw.agentcore_client = mock.MagicMock()
    gw.agent_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/test"
    return gw


def _agent_stream(text: str):
    stream = mock.MagicMock()
    stream.read.return_value = json.dumps({'response': text}).encode()
    return {'response': stream}


# ─────────────────────────────────────────────────────────────────────────────

class TestAgentInvocation:
    def test_successful_agent_response(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream(
            'Rebooted instance i-abc123'
        )
        q = Query(text='EC2 instance is unreachable', customer_id='t1')
        result = gw.send(q)
        assert result.resolution_type == 'action'
        assert 'Rebooted' in result.text
        assert result.escalate is False
        gw.agentcore_client.invoke_agent_runtime.assert_called_once()

    def test_empty_response_escalates(self):
        gw = _make_gateway()
        stream = mock.MagicMock()
        stream.read.return_value = json.dumps({'response': ''}).encode()
        gw.agentcore_client.invoke_agent_runtime.return_value = {'response': stream}
        q = Query(text='Check something', customer_id='t1')
        result = gw.send(q)
        assert result.escalate is True

    def test_agent_exception_escalates(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.side_effect = Exception('Connection timeout')
        gw.rag_service.resolve.return_value = Resolution(
            text='No match', resolution_type='escalated', confidence=0.0, escalate=True
        )
        q = Query(text='EC2 down', customer_id='t1')
        result = gw.send(q)
        assert result.escalate is True


class TestEscalationFallback:
    def test_escalate_signal_triggers_rag_fallback(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream('ESCALATE - no tool')
        gw.rag_service.resolve.return_value = Resolution(
            text='Historical fix: restart the service',
            resolution_type='rag', confidence=0.85, escalate=False
        )
        q = Query(text='Unknown service issue', customer_id='t1')
        result = gw.send(q)
        assert result.resolution_type == 'rag'
        assert result.escalate is False
        gw.rag_service.resolve.assert_called_once()

    def test_both_escalate_returns_escalation(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream('ESCALATE')
        gw.rag_service.resolve.return_value = Resolution(
            text='No KB match', resolution_type='escalated', confidence=0.0, escalate=True
        )
        q = Query(text='Unknown issue', customer_id='t1')
        result = gw.send(q)
        assert result.escalate is True
        assert result.resolution_type == 'escalated'

    def test_successful_agent_does_not_call_rag(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream('Fixed the issue')
        q = Query(text='Fix my EC2', customer_id='t1')
        gw.send(q)
        gw.rag_service.resolve.assert_not_called()


class TestRateLimiting:
    def test_rate_limit_exceeded_returns_escalation(self):
        gw = _make_gateway()
        # Temporarily override the global mock
        original = mt.tenant_manager.check_usage_limits.return_value
        mt.tenant_manager.check_usage_limits.return_value = False
        try:
            q = Query(text='Any query', customer_id='t1')
            result = gw.send(q)
            assert result.escalate is True
            assert 'limit exceeded' in result.text.lower()
        finally:
            mt.tenant_manager.check_usage_limits.return_value = original


class TestConversationHistory:
    def test_history_prepended_to_prompt(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream('Done')

        q = Query(text='Now fix it', customer_id='t1')
        history = ['User: EC2 is down', 'Assistant: I see the issue']
        gw.send(q, history=history)

        call_payload = json.loads(
            gw.agentcore_client.invoke_agent_runtime.call_args[1]['payload'].decode()
        )
        assert 'Conversation so far' in call_payload['prompt']
        assert 'EC2 is down' in call_payload['prompt']
        assert 'Now fix it' in call_payload['prompt']

    def test_no_history_sends_plain_prompt(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream('Done')

        q = Query(text='Check my EC2', customer_id='t1')
        gw.send(q, history=None)

        call_payload = json.loads(
            gw.agentcore_client.invoke_agent_runtime.call_args[1]['payload'].decode()
        )
        assert call_payload['prompt'] == 'Check my EC2'


class TestMetadata:
    def test_successful_response_includes_metadata(self):
        gw = _make_gateway()
        stream = mock.MagicMock()
        stream.read.return_value = json.dumps({
            'response': 'Listed 3 instances',
            'domain': 'compute',
            'execution_time_ms': 1500,
            'tools_available': 52,
        }).encode()
        gw.agentcore_client.invoke_agent_runtime.return_value = {'response': stream}

        q = Query(text='List EC2', customer_id='t1')
        result = gw.send(q)

        assert result.metadata['domain'] == 'compute'
        assert result.metadata['execution_time_ms'] == 1500
        assert result.metadata['tools_available'] == 52


class TestBackwardCompatibility:
    def test_route_query_alias_works(self):
        gw = _make_gateway()
        gw.agentcore_client.invoke_agent_runtime.return_value = _agent_stream('OK')
        q = Query(text='Hello', customer_id='t1')
        result = gw.route_query(q)
        assert result.resolution_type == 'action'
