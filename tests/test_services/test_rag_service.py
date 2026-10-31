"""
Tests for RAGService.
Mocks bedrock-agent-runtime to test confidence thresholds and escalation logic.
"""
import pytest
from unittest.mock import MagicMock, patch
from src.services.rag_service import RAGService
from src.models.query import Query


def make_query(text="SSH is down on i-0abc123"):
    return Query(text=text, customer_id="demo-operator-00000000000000000000")


def make_retrieval_result(score, text="Restart the service"):
    return {
        "score": score,
        "content": {"text": text}
    }


@pytest.fixture
def rag_service():
    with patch("boto3.client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client_factory.return_value = mock_client
        svc = RAGService()
        svc.client = mock_client
        # config.KNOWLEDGE_BASE_ID is "PLACEHOLDER" by default, which makes
        # resolve() short-circuit before calling retrieve(). Set a real-looking
        # id so the retrieval path (and its confidence logic) is exercised.
        svc.knowledge_base_id = "TESTKB123"
        return svc


class TestRAGServiceResolve:

    def test_high_confidence_returns_rag_resolution(self, rag_service):
        rag_service.client.retrieve.return_value = {
            "retrievalResults": [make_retrieval_result(0.9, "Open port 22 on the security group")]
        }
        result = rag_service.resolve(make_query())
        assert result.resolution_type == "rag"
        assert result.confidence == 0.9
        assert result.escalate is False
        assert "port 22" in result.text.lower()

    def test_confidence_exactly_at_threshold_passes(self, rag_service):
        rag_service.client.retrieve.return_value = {
            "retrievalResults": [make_retrieval_result(0.6, "Known fix")]
        }
        result = rag_service.resolve(make_query())
        assert result.resolution_type == "rag"
        assert result.confidence == 0.6
        assert result.escalate is False

    def test_confidence_just_below_threshold_escalates(self, rag_service):
        rag_service.client.retrieve.return_value = {
            "retrievalResults": [make_retrieval_result(0.59, "Weak match")]
        }
        result = rag_service.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.confidence == 0.0
        assert result.escalate is True

    def test_zero_confidence_escalates(self, rag_service):
        rag_service.client.retrieve.return_value = {
            "retrievalResults": [make_retrieval_result(0.0, "No match")]
        }
        result = rag_service.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.escalate is True

    def test_empty_results_escalates(self, rag_service):
        rag_service.client.retrieve.return_value = {"retrievalResults": []}
        result = rag_service.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.confidence == 0.0
        assert result.escalate is True
        assert "escalating" in result.text.lower()

    def test_missing_retrieval_results_key_escalates(self, rag_service):
        rag_service.client.retrieve.return_value = {}
        result = rag_service.resolve(make_query())
        assert result.resolution_type == "escalated"
        assert result.escalate is True

    def test_uses_top_result_only(self, rag_service):
        rag_service.client.retrieve.return_value = {
            "retrievalResults": [
                make_retrieval_result(0.95, "Best match"),
                make_retrieval_result(0.80, "Second match"),
            ]
        }
        result = rag_service.resolve(make_query())
        assert "Best match" in result.text
        assert "Second match" not in result.text
        assert result.confidence == 0.95

    def test_query_text_passed_to_retrieve(self, rag_service):
        rag_service.client.retrieve.return_value = {"retrievalResults": []}
        query = make_query("Lambda throttling on payment-processor")
        rag_service.resolve(query)
        call_kwargs = rag_service.client.retrieve.call_args[1]
        assert call_kwargs["retrievalQuery"]["text"] == "Lambda throttling on payment-processor"

    def test_knowledge_base_id_used(self, rag_service):
        rag_service.client.retrieve.return_value = {"retrievalResults": []}
        rag_service.resolve(make_query())
        call_kwargs = rag_service.client.retrieve.call_args[1]
        assert call_kwargs["knowledgeBaseId"] == rag_service.knowledge_base_id
