"""
Role-based access control and audit logging.

Identity comes from Amazon Cognito (see ui/auth.py): a user's role is the
Cognito group they belong to, and their tenant is the ``custom:tenant_id``
attribute. Every security-relevant event is written to the audit table
(partition key ``tenant_id``, sort key ``sk`` = ``<iso timestamp>#<id>``) so a
tenant's history can be queried newest-first without scans.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

import boto3
import structlog
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel, Field

import config

logger = structlog.get_logger()


class UserRole(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    SRE = "sre"
    ADMIN = "admin"


class Permission(str, Enum):
    USE_AGENT = "use_agent"              # chat with the agent (read-only tools)
    PROPOSE_CHANGES = "propose_changes"  # agent may queue mutating actions
    APPROVE_CHANGES = "approve_changes"  # approve / reject queued actions
    UPLOAD_CODE = "upload_code"
    VIEW_TICKETS = "view_tickets"
    RESOLVE_TICKETS = "resolve_tickets"
    MANAGE_ACCOUNTS = "manage_accounts"
    VIEW_AUDIT_LOGS = "view_audit_logs"
    TRIGGER_LEARNING = "trigger_learning"


ROLE_PERMISSIONS: Dict[UserRole, List[Permission]] = {
    UserRole.VIEWER: [Permission.USE_AGENT, Permission.VIEW_TICKETS],
    UserRole.OPERATOR: [
        Permission.USE_AGENT, Permission.PROPOSE_CHANGES, Permission.APPROVE_CHANGES,
        Permission.UPLOAD_CODE, Permission.VIEW_TICKETS,
    ],
    UserRole.SRE: [
        Permission.USE_AGENT, Permission.PROPOSE_CHANGES, Permission.APPROVE_CHANGES,
        Permission.UPLOAD_CODE, Permission.VIEW_TICKETS, Permission.RESOLVE_TICKETS,
        Permission.VIEW_AUDIT_LOGS, Permission.TRIGGER_LEARNING,
    ],
    UserRole.ADMIN: list(Permission),
}

# Highest role wins when a user is in several Cognito groups.
_ROLE_RANK = [UserRole.VIEWER, UserRole.OPERATOR, UserRole.SRE, UserRole.ADMIN]


def role_from_groups(groups: List[str]) -> UserRole:
    roles = [r for r in _ROLE_RANK if r.value in (groups or [])]
    return roles[-1] if roles else UserRole.VIEWER


class User(BaseModel):
    user_id: str
    email: str
    role: UserRole
    tenant_id: str
    permissions: List[Permission] = Field(default_factory=list)
    is_active: bool = True

    @classmethod
    def build(cls, user_id: str, email: str, role: UserRole, tenant_id: str) -> "User":
        return cls(user_id=user_id, email=email, role=role, tenant_id=tenant_id,
                   permissions=ROLE_PERMISSIONS[role])

    def can(self, permission: Permission) -> bool:
        return self.is_active and permission in self.permissions


def _to_dynamo(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [_to_dynamo(v) for v in obj]
    return obj


class SecurityManager:
    """Permission checks and the audit trail."""

    role_permissions = ROLE_PERMISSIONS

    def __init__(self):
        self._audit_table = None

    @property
    def audit_table(self):
        if self._audit_table is None:
            self._audit_table = boto3.resource(
                "dynamodb", region_name=config.AWS_REGION
            ).Table(config.AUDIT_TABLE)
        return self._audit_table

    def check_permission(self, user: User, permission: Permission,
                         resource_tenant_id: Optional[str] = None) -> bool:
        if resource_tenant_id and resource_tenant_id != user.tenant_id:
            return False
        return user.can(permission)

    def log_audit_event(self, user_id: str, tenant_id: str, action: str,
                        resource: str, result: str, details: Optional[dict] = None,
                        **_ignored) -> None:
        """Write an audit record. Never raises: auditing must not break the caller."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.audit_table.put_item(Item=_to_dynamo({
                "tenant_id": tenant_id,
                "sk": f"{now}#{uuid.uuid4().hex[:8]}",
                "timestamp": now,
                "user_id": user_id,
                "action": action,
                "resource": str(resource)[:1000],
                "result": result,
                "details": details or {},
            }))
        except Exception as e:
            logger.error("audit_logging_failed", action=action, error=str(e))
        logger.info("audit", tenant_id=tenant_id, user_id=user_id, action=action,
                    resource=str(resource)[:200], result=result)

    def get_audit_logs(self, tenant_id: str, limit: int = 200,
                       since: Optional[datetime] = None) -> List[dict]:
        """Newest-first audit records for a tenant."""
        condition = Key("tenant_id").eq(tenant_id)
        if since:
            condition = condition & Key("sk").gte(since.isoformat())
        try:
            resp = self.audit_table.query(
                KeyConditionExpression=condition, ScanIndexForward=False, Limit=limit
            )
            return resp.get("Items", [])
        except Exception as e:
            logger.error("audit_retrieval_failed", error=str(e))
            return []


security_manager = SecurityManager()
