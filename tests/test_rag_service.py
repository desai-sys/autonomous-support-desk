"""
RAG service tests — Bedrock calls mocked.
Run with: python -m pytest tests/test_rag_service.py -v
"""
import sys
import unittest.mock as mock
import pytest

sys.modules['boto3'] = mock.MagicMock()
sys.modules['botocore'] = mock.MagicMock()
sys.modules['botocore.exceptions'] = mock.MagicMock()

from src.models.query import Query, Resolution
from src.services.rag_service import RAGService


@pytest.fixture
def svc():
    s = RAGService.__new__(RAGService)
    s.knowledge_base_id = 'KB-TEST-123'
    s.client = mock.MagicMock()
    return s


@pytest.fixture
def query():
    return Query(text='SSH timeout on EC2', customer_id='tenant-001')


class TestHighConfidenceResult:
    def test_returns_rag_resolution(self, svc, query):
        svc.client.retrieve.return_value = {
            'retrievalResults': [
                {'content': {'text': 'Reboot the instance.'}, 'score': 0.85}
            ]
        }
        result = svc.resolve(query)
        assert result.resolution_type == 'rag'
        assert result.confidence == 0.85
        assert result.escalate is False
        assert 'Reboot' in result.text

    def test_confidence_boundary_exactly_06(self, svc, query):
        svc.client.retrieve.return_value = {
            'retrievalResults': [
                {'content': {'text': 'Some resolution.'}, 'score': 0.6}
            ]
        }
        result = svc.resolve(query)
        assert result.resolution_type == 'rag'
        assert result.escalate is False


class TestLowConfidenceResult:
    def test_escalates_below_threshold(self, svc, query):
        svc.client.retrieve.return_value = {
            'retrievalResults': [
                {'content': {'text': 'Vague answer'}, 'score': 0.59}
            ]
        }
        result = svc.resolve(query)
        assert result.resolution_type == 'escalated'
        assert result.escalate is True

    def test_escalates_on_empty_results(self, svc, query):
        svc.client.retrieve.return_value = {'retrievalResults': []}
        result = svc.resolve(query)
        assert result.resolution_type == 'escalated'
        assert result.escalate is True


class TestErrorHandling:
    def test_placeholder_kb_returns_error(self, svc, query):
        svc.knowledge_base_id = 'PLACEHOLDER'
        result = svc.resolve(query)
        assert result.resolution_type == 'error'
        assert result.escalate is True

    def test_bedrock_exception_returns_error(self, svc, query):
        svc.client.retrieve.side_effect = Exception('Connection refused')
        result = svc.resolve(query)
        assert result.resolution_type == 'error'
        assert result.escalate is True
        assert 'Connection refused' in result.text

    def test_missing_score_field_treated_as_zero(self, svc, query):
        # score key absent → defaults to 0 → escalates
        svc.client.retrieve.return_value = {
            'retrievalResults': [
                {'content': {'text': 'Some text'}}  # no 'score' key
            ]
        }
        result = svc.resolve(query)
        assert result.escalate is True
