"""
Enterprise Security & Authentication Module
Provides RBAC, audit logging, and security controls for CloudOps Command Center.
"""

import boto3
import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import Enum
from pydantic import BaseModel, Field
import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _to_dynamo(obj: Any) -> Any:
    """
    Recursively convert a dict/list/value to DynamoDB-safe types.
    - datetime  → ISO string
    - Enum      → .value
    - None      → omitted by caller (DynamoDB doesn't store None)
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _to_dynamo(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_to_dynamo(v) for v in obj]
    return obj


# ── Models ────────────────────────────────────────────────────────────────────

class UserRole(str, Enum):
    OPERATOR = "operator"
    SRE = "sre"
    ADMIN = "admin"
    VIEWER = "viewer"


class Permission(str, Enum):
    VIEW_TICKETS = "view_tickets"
    CREATE_TICKETS = "create_tickets"
    RESOLVE_TICKETS = "resolve_tickets"
    EXECUTE_TOOLS = "execute_tools"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT_LOGS = "view_audit_logs"
    TRIGGER_LEARNING = "trigger_learning"
    MANAGE_SYSTEM = "manage_system"


class User(BaseModel):
    user_id: str
    email: str
    role: UserRole
    tenant_id: str
    permissions: List[Permission]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: Optional[datetime] = None
    is_active: bool = True


class AuditEvent(BaseModel):
    event_id: str
    user_id: str
    tenant_id: str
    action: str
    resource: str
    resource_id: Optional[str] = None
    result: str  # SUCCESS, FAILURE, DENIED
    details: Dict[str, Any] = Field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dynamo_item(self) -> dict:
        """Return a DynamoDB-safe dict (no datetime or Enum objects)."""
        return _to_dynamo({
            'event_id': self.event_id,
            'user_id': self.user_id,
            'tenant_id': self.tenant_id,
            'action': self.action,
            'resource': self.resource,
            'resource_id': self.resource_id,
            'result': self.result,
            'details': self.details,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'timestamp': self.timestamp,
        })


# ── SecurityManager ───────────────────────────────────────────────────────────

class SecurityManager:
    """Centralized security and audit management."""

    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        self.audit_table = self.dynamodb.Table('CloudOpsAuditLog')
        self.users_table = self.dynamodb.Table('CloudOpsUsers')

        # Role-based permissions mapping
        self.role_permissions = {
            UserRole.VIEWER:   [Permission.VIEW_TICKETS],
            UserRole.OPERATOR: [Permission.VIEW_TICKETS, Permission.CREATE_TICKETS,
                                Permission.EXECUTE_TOOLS],
            UserRole.SRE:      [Permission.VIEW_TICKETS, Permission.CREATE_TICKETS,
                                Permission.RESOLVE_TICKETS, Permission.EXECUTE_TOOLS,
                                Permission.VIEW_AUDIT_LOGS],
            UserRole.ADMIN:    list(Permission),
        }

    def check_permission(self, user: User, permission: Permission,
                         resource_tenant_id: Optional[str] = None) -> bool:
        """Check if user has a specific permission."""
        if not user.is_active:
            return False
        if resource_tenant_id and resource_tenant_id != user.tenant_id:
            return False
        return permission in user.permissions

    def log_audit_event(self, user_id: str, tenant_id: str, action: str,
                        resource: str, result: str, **kwargs) -> None:
        """
        Log an audit event to DynamoDB.
        Non-fatal — exceptions are caught and logged; they never propagate.
        """
        try:
            event = AuditEvent(
                event_id=f"{user_id}_{int(time.time() * 1000)}",
                user_id=user_id,
                tenant_id=tenant_id,
                action=action,
                resource=resource,
                result=result,
                details=kwargs.get('details', {}),
                ip_address=kwargs.get('ip_address'),
                user_agent=kwargs.get('user_agent'),
            )
            self.audit_table.put_item(Item=event.to_dynamo_item())
        except Exception as e:
            # Audit logging must never crash the caller
            logger.error("audit_logging_failed", action=action, error=str(e))

    def get_audit_logs(self, tenant_id: str, start_date: datetime,
                       end_date: datetime, user_id: Optional[str] = None) -> List[AuditEvent]:
        """Retrieve audit logs for a tenant within a date range."""
        try:
            filter_expression = "tenant_id = :tenant_id AND #ts BETWEEN :start AND :end"
            expression_values = {
                ':tenant_id': tenant_id,
                ':start': start_date.isoformat(),
                ':end': end_date.isoformat(),
            }
            expression_names = {'#ts': 'timestamp'}

            if user_id:
                filter_expression += " AND user_id = :user_id"
                expression_values[':user_id'] = user_id

            response = self.audit_table.scan(
                FilterExpression=filter_expression,
                ExpressionAttributeValues=expression_values,
                ExpressionAttributeNames=expression_names,
            )
            return [AuditEvent(**item) for item in response.get('Items', [])]
        except Exception as e:
            logger.error("audit_retrieval_failed", error=str(e))
            return []

    def authenticate_user(self, token: str) -> Optional[User]:
        """Authenticate user via Cognito JWT token (production path)."""
        try:
            cognito = boto3.client('cognito-idp', region_name='us-east-1')
            response = cognito.get_user(AccessToken=token)
            attrs = {a['Name']: a['Value'] for a in response['UserAttributes']}

            user_id = response['Username']
            role = UserRole(attrs.get('custom:role', 'viewer'))
            tenant_id = attrs.get('custom:tenant_id', 'default')
            email = attrs.get('email', '')

            return self._get_or_create_user(user_id, email, role, tenant_id)
        except Exception as e:
            logger.error("authentication_failed", error=str(e))
            return None

    def validate_request_signature(self, payload: str, signature: str,
                                   secret_name: str) -> bool:
        """Validate HMAC-SHA256 signature for API requests."""
        try:
            secrets = boto3.client('secretsmanager', region_name='us-east-1')
            secret_key = secrets.get_secret_value(SecretId=secret_name)['SecretString']
            expected = hmac.new(
                key=secret_key.encode('utf-8'),
                msg=payload.encode('utf-8'),
                digestmod=hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(signature, expected)
        except Exception as e:
            logger.error("signature_validation_failed", error=str(e))
            return False

    def _get_or_create_user(self, user_id: str, email: str,
                            role: UserRole, tenant_id: str) -> User:
        """Get user from DynamoDB or create on first login."""
        try:
            response = self.users_table.get_item(Key={'user_id': user_id})
            if 'Item' in response:
                d = response['Item']
                return User(
                    user_id=d['user_id'],
                    email=d['email'],
                    role=UserRole(d['role']),
                    tenant_id=d['tenant_id'],
                    permissions=[Permission(p) for p in d['permissions']],
                    created_at=datetime.fromisoformat(d['created_at']),
                    last_login=datetime.fromisoformat(d['last_login']) if d.get('last_login') else None,
                    is_active=d.get('is_active', True),
                )
            else:
                permissions = self.role_permissions.get(role, [])
                user = User(user_id=user_id, email=email, role=role,
                            tenant_id=tenant_id, permissions=permissions)
                # Write with all datetimes serialized
                self.users_table.put_item(Item={
                    'user_id': user.user_id,
                    'email': user.email,
                    'role': user.role.value,
                    'tenant_id': user.tenant_id,
                    'permissions': [p.value for p in user.permissions],
                    'created_at': user.created_at.isoformat(),
                    'is_active': user.is_active,
                })
                return user
        except Exception as e:
            logger.error("user_retrieval_failed", error=str(e))
            raise

    def _update_last_login(self, user_id: str) -> None:
        try:
            self.users_table.update_item(
                Key={'user_id': user_id},
                UpdateExpression='SET last_login = :ts',
                ExpressionAttributeValues={':ts': _now()},
            )
        except Exception as e:
            logger.error("last_login_update_failed", error=str(e))


class RateLimiter:
    """In-memory rate limiter (Redis-upgradeable)."""

    def __init__(self):
        self.fallback_store: Dict[str, List[int]] = {}

    def is_rate_limited(self, key: str, limit: int, window: int) -> bool:
        current_time = int(time.time())
        window_start = current_time - window
        bucket = self.fallback_store.setdefault(key, [])
        self.fallback_store[key] = [t for t in bucket if t > window_start]
        if len(self.fallback_store[key]) >= limit:
            return True
        self.fallback_store[key].append(current_time)
        return False


# Global instances
security_manager = SecurityManager()
rate_limiter = RateLimiter()
