from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class Ticket(BaseModel):
    ticket_id: str
    customer_id: str
    query_text: str
    category: str
    urgency: str = Field(..., pattern=r'^(low|medium|high|critical)$')
    status: str = Field(..., pattern=r'^(open|in_progress|resolved|closed)$')
    resolution: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    embedded: bool = False


class TicketUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern=r'^(open|in_progress|resolved|closed)$')
    resolution: Optional[str] = None
    notes: Optional[str] = None

