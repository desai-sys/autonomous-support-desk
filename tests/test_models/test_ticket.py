"""
Tests for Ticket and TicketUpdate Pydantic models.
Validates urgency/status enums, optional fields, and update model.
"""
import pytest
from datetime import datetime
from pydantic import ValidationError
from src.models.ticket import Ticket, TicketUpdate


def make_ticket(**overrides):
    defaults = dict(
        ticket_id="abc12345",
        customer_id="cust-001",
        query_text="SSH is down on i-0abc123",
        category="EC2",
        urgency="high",
        status="open",
        created_at=datetime.utcnow(),
    )
    defaults.update(overrides)
    return Ticket(**defaults)


class TestTicket:

    def test_valid_ticket(self):
        t = make_ticket()
        assert t.ticket_id == "abc12345"
        assert t.urgency == "high"
        assert t.status == "open"
        assert t.resolution is None
        assert t.embedded is False

    def test_all_urgency_levels(self):
        for urgency in ("low", "medium", "high", "critical"):
            t = make_ticket(urgency=urgency)
            assert t.urgency == urgency

    def test_invalid_urgency(self):
        with pytest.raises(ValidationError):
            make_ticket(urgency="urgent")

    def test_all_status_values(self):
        for status in ("open", "in_progress", "resolved", "closed"):
            t = make_ticket(status=status)
            assert t.status == status

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            make_ticket(status="pending")

    def test_with_resolution(self):
        t = make_ticket(status="closed", resolution="Opened port 22 on sg-abc123")
        assert t.resolution == "Opened port 22 on sg-abc123"

    def test_with_updated_at(self):
        now = datetime.utcnow()
        t = make_ticket(updated_at=now)
        assert t.updated_at == now

    def test_embedded_flag(self):
        t = make_ticket(embedded=True)
        assert t.embedded is True

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            Ticket(ticket_id="abc", urgency="high", status="open", created_at=datetime.utcnow())


class TestTicketUpdate:

    def test_all_none(self):
        u = TicketUpdate()
        assert u.status is None
        assert u.resolution is None
        assert u.notes is None

    def test_valid_status_update(self):
        u = TicketUpdate(status="closed")
        assert u.status == "closed"

    def test_invalid_status_update(self):
        with pytest.raises(ValidationError):
            TicketUpdate(status="deleted")

    def test_resolution_only(self):
        u = TicketUpdate(resolution="Restarted nginx service")
        assert u.resolution == "Restarted nginx service"
        assert u.status is None

    def test_full_update(self):
        u = TicketUpdate(status="resolved", resolution="Fixed", notes="Escalated to L2 first")
        assert u.status == "resolved"
        assert u.resolution == "Fixed"
        assert u.notes == "Escalated to L2 first"
