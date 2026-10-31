"""
Tests for Query and Resolution Pydantic models.
Validates field constraints, type coercion, and validation errors.
"""
import pytest
from datetime import datetime
from pydantic import ValidationError
from src.models.query import Query, Resolution


# ---------------------------------------------------------------------------
# Query model
# ---------------------------------------------------------------------------

class TestQuery:

    def test_valid_minimal(self):
        q = Query(text="SSH is down", customer_id="cust-001")
        assert q.text == "SSH is down"
        assert q.customer_id == "cust-001"
        assert q.context is None
        assert isinstance(q.timestamp, datetime)

    def test_valid_with_context(self):
        q = Query(text="Check EC2", customer_id="cust-002", context={"region": "us-east-1"})
        assert q.context == {"region": "us-east-1"}

    def test_text_too_short(self):
        with pytest.raises(ValidationError) as exc:
            Query(text="", customer_id="cust-001")
        assert "min_length" in str(exc.value).lower() or "string_too_short" in str(exc.value).lower()

    def test_text_too_long(self):
        with pytest.raises(ValidationError):
            Query(text="x" * 2001, customer_id="cust-001")

    def test_text_max_boundary(self):
        q = Query(text="x" * 2000, customer_id="cust-001")
        assert len(q.text) == 2000

    def test_text_min_boundary(self):
        q = Query(text="x", customer_id="cust-001")
        assert q.text == "x"

    def test_timestamp_auto_set(self):
        before = datetime.now()
        q = Query(text="test", customer_id="cust-001")
        after = datetime.now()
        assert before <= q.timestamp <= after

    def test_missing_customer_id(self):
        with pytest.raises(ValidationError):
            Query(text="test")

    def test_missing_text(self):
        with pytest.raises(ValidationError):
            Query(customer_id="cust-001")


# ---------------------------------------------------------------------------
# Resolution model
# ---------------------------------------------------------------------------

class TestResolution:

    def test_valid_rag(self):
        r = Resolution(text="Restart nginx", resolution_type="rag", confidence=0.85)
        assert r.resolution_type == "rag"
        assert r.confidence == 0.85
        assert r.escalate is False

    def test_valid_action(self):
        r = Resolution(text="Port opened", resolution_type="action", confidence=1.0)
        assert r.resolution_type == "action"
        assert r.confidence == 1.0

    def test_valid_escalated(self):
        r = Resolution(
            text="Escalating to human",
            resolution_type="escalated",
            confidence=0.0,
            escalate=True
        )
        assert r.escalate is True

    def test_invalid_resolution_type(self):
        with pytest.raises(ValidationError):
            Resolution(text="test", resolution_type="unknown", confidence=0.5)

    def test_confidence_below_zero(self):
        with pytest.raises(ValidationError):
            Resolution(text="test", resolution_type="rag", confidence=-0.1)

    def test_confidence_above_one(self):
        with pytest.raises(ValidationError):
            Resolution(text="test", resolution_type="action", confidence=1.1)

    def test_confidence_boundary_zero(self):
        r = Resolution(text="test", resolution_type="escalated", confidence=0.0)
        assert r.confidence == 0.0

    def test_confidence_boundary_one(self):
        r = Resolution(text="test", resolution_type="action", confidence=1.0)
        assert r.confidence == 1.0

    def test_optional_ticket_id(self):
        r = Resolution(text="test", resolution_type="rag", confidence=0.7, ticket_id="abc123")
        assert r.ticket_id == "abc123"

    def test_optional_metadata(self):
        r = Resolution(
            text="test",
            resolution_type="action",
            confidence=1.0,
            metadata={"tool": "fix_ec2_sg", "port": 22}
        )
        assert r.metadata["tool"] == "fix_ec2_sg"

    def test_all_valid_resolution_types(self):
        for rtype in ("rag", "action", "escalated"):
            r = Resolution(text="test", resolution_type=rtype, confidence=0.5)
            assert r.resolution_type == rtype
