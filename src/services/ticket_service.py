"""
Escalation tickets.

Table key: ``ticket_id``. Global secondary indexes:
  * ``by-tenant`` (tenant_id, created_at) — a tenant's tickets, newest first.
  * ``pending-vectorization`` (pending_vectorization) — sparse index holding only
    closed tickets the learning loop has not yet added to the knowledge base.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

import boto3
from boto3.dynamodb.conditions import Key

import config

URGENCIES = ("critical", "high", "medium", "low")
OPEN_STATUSES = ("open", "escalated")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TicketService:
    def __init__(self, table_name: str = None):
        self.table = boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(
            table_name or config.TICKETS_TABLE
        )

    def create(self, *, tenant_id: str, query_text: str, category: str, urgency: str = "high",
               created_by: str = "", context: Optional[str] = None) -> dict:
        item = {
            "ticket_id": uuid.uuid4().hex[:10],
            "tenant_id": tenant_id,
            "query_text": query_text[:4000],
            "category": category,
            "urgency": urgency if urgency in URGENCIES else "medium",
            "status": "escalated",
            "created_by": created_by,
            "created_at": _now(),
        }
        if context:
            item["agent_context"] = context[:8000]
        self.table.put_item(Item=item)
        return item

    def list_open(self, tenant_id: str) -> List[dict]:
        resp = self.table.query(
            IndexName="by-tenant",
            KeyConditionExpression=Key("tenant_id").eq(tenant_id),
            ScanIndexForward=False,
        )
        items = [t for t in resp.get("Items", []) if t.get("status") in OPEN_STATUSES]
        order = {u: i for i, u in enumerate(URGENCIES)}
        return sorted(items, key=lambda t: (order.get(t.get("urgency"), 9), t.get("created_at", "")))

    def close(self, ticket_id: str, *, tenant_id: str, resolution: str, resolved_by: str) -> bool:
        if not resolution.strip():
            raise ValueError("Resolution text is required.")
        try:
            self.table.update_item(
                Key={"ticket_id": ticket_id},
                UpdateExpression=(
                    "SET #s = :closed, resolution = :res, resolved_by = :by, "
                    "updated_at = :ts, pending_vectorization = :p"
                ),
                ConditionExpression="tenant_id = :tenant",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":closed": "closed", ":res": resolution.strip()[:8000], ":by": resolved_by,
                    ":ts": _now(), ":p": "1", ":tenant": tenant_id,
                },
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def list_pending_vectorization(self) -> List[dict]:
        resp = self.table.query(
            IndexName="pending-vectorization",
            KeyConditionExpression=Key("pending_vectorization").eq("1"),
        )
        items = resp.get("Items", [])
        while resp.get("LastEvaluatedKey"):
            resp = self.table.query(
                IndexName="pending-vectorization",
                KeyConditionExpression=Key("pending_vectorization").eq("1"),
                ExclusiveStartKey=resp["LastEvaluatedKey"],
            )
            items += resp.get("Items", [])
        return items

    def mark_vectorized(self, ticket_id: str) -> None:
        self.table.update_item(
            Key={"ticket_id": ticket_id},
            UpdateExpression="REMOVE pending_vectorization SET vectorized_at = :ts",
            ExpressionAttributeValues={":ts": _now()},
        )
