"""
Tests for the Metrics Pydantic model.
Validates numeric bounds and timestamp auto-generation.
"""
import pytest
from datetime import datetime
from pydantic import ValidationError
from src.models.metrics import Metrics


class TestMetrics:

    def test_valid_metrics(self):
        m = Metrics(
            mttr_seconds=120.5,
            deflection_rate=0.75,
            active_tickets=3,
            resolved_today=12,
            avg_confidence=0.82
        )
        assert m.mttr_seconds == 120.5
        assert m.deflection_rate == 0.75
        assert m.active_tickets == 3
        assert m.resolved_today == 12
        assert m.avg_confidence == 0.82
        assert isinstance(m.timestamp, datetime)

    def test_zero_mttr(self):
        m = Metrics(mttr_seconds=0, deflection_rate=0.0, active_tickets=0,
                    resolved_today=0, avg_confidence=0.0)
        assert m.mttr_seconds == 0

    def test_negative_mttr(self):
        with pytest.raises(ValidationError):
            Metrics(mttr_seconds=-1, deflection_rate=0.5, active_tickets=0,
                    resolved_today=0, avg_confidence=0.5)

    def test_deflection_rate_above_one(self):
        with pytest.raises(ValidationError):
            Metrics(mttr_seconds=60, deflection_rate=1.1, active_tickets=0,
                    resolved_today=0, avg_confidence=0.5)

    def test_deflection_rate_below_zero(self):
        with pytest.raises(ValidationError):
            Metrics(mttr_seconds=60, deflection_rate=-0.1, active_tickets=0,
                    resolved_today=0, avg_confidence=0.5)

    def test_avg_confidence_boundaries(self):
        m_low = Metrics(mttr_seconds=60, deflection_rate=0.5, active_tickets=0,
                        resolved_today=0, avg_confidence=0.0)
        m_high = Metrics(mttr_seconds=60, deflection_rate=0.5, active_tickets=0,
                         resolved_today=0, avg_confidence=1.0)
        assert m_low.avg_confidence == 0.0
        assert m_high.avg_confidence == 1.0

    def test_negative_active_tickets(self):
        with pytest.raises(ValidationError):
            Metrics(mttr_seconds=60, deflection_rate=0.5, active_tickets=-1,
                    resolved_today=0, avg_confidence=0.5)

    def test_negative_resolved_today(self):
        with pytest.raises(ValidationError):
            Metrics(mttr_seconds=60, deflection_rate=0.5, active_tickets=0,
                    resolved_today=-1, avg_confidence=0.5)

    def test_timestamp_auto_generated(self):
        before = datetime.now()
        m = Metrics(mttr_seconds=60, deflection_rate=0.5, active_tickets=1,
                    resolved_today=5, avg_confidence=0.8)
        after = datetime.now()
        assert before <= m.timestamp <= after
