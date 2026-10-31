from pydantic import BaseModel, Field
from datetime import datetime


class Metrics(BaseModel):
    mttr_seconds: float = Field(..., ge=0)
    deflection_rate: float = Field(..., ge=0.0, le=1.0)
    active_tickets: int = Field(..., ge=0)
    resolved_today: int = Field(..., ge=0)
    avg_confidence: float = Field(..., ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=datetime.now)

