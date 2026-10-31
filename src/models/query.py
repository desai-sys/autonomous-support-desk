"""
Data models for CloudOps platform.
"""

from pydantic import BaseModel, Field
from typing import Optional, Dict
from datetime import datetime

# AgentCore requires runtimeSessionId to be >= 33 characters
_MIN_SESSION_ID_LEN = 33


def pad_session_id(session_id: str) -> str:
    """Ensure session ID meets AgentCore's 33-character minimum."""
    if len(session_id) >= _MIN_SESSION_ID_LEN:
        return session_id
    return session_id + "-" + "0" * (_MIN_SESSION_ID_LEN - len(session_id) - 1)


class Query(BaseModel):
    """Query model for user requests."""
    text: str = Field(..., min_length=1, max_length=2000)
    customer_id: str
    context: Optional[Dict] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Resolution(BaseModel):
    """Resolution model for agent responses."""
    text: str
    resolution_type: str = Field(..., pattern=r'^(rag|action|escalated|error|bedrock)$')
    confidence: float = Field(..., ge=0.0, le=1.0)
    ticket_id: Optional[str] = None
    escalate: bool = False
    metadata: Optional[Dict] = None
