"""
Shared pytest fixtures and configuration.
Patches boto3 globally so no real AWS calls are made during any test.
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=False)
def no_aws_calls():
    """
    Opt-in fixture: patches boto3.client and boto3.resource globally.
    Use in tests that don't set up their own mocks.
    """
    with patch("boto3.client", return_value=MagicMock()), \
         patch("boto3.resource", return_value=MagicMock()):
        yield


@pytest.fixture
def sample_query():
    from src.models.query import Query
    return Query(text="SSH is down on i-0abc123", customer_id="demo-operator-00000000000000000000")


@pytest.fixture
def sample_resolution_action():
    from src.models.query import Resolution
    return Resolution(text="Port 22 opened on sg-abc123", resolution_type="action", confidence=1.0)


@pytest.fixture
def sample_resolution_rag():
    from src.models.query import Resolution
    return Resolution(text="Restart nginx service", resolution_type="rag", confidence=0.85)


@pytest.fixture
def sample_resolution_escalated():
    from src.models.query import Resolution
    return Resolution(
        text="No known resolution. Escalating to human SRE.",
        resolution_type="escalated",
        confidence=0.0,
        escalate=True
    )
