"""
Multi-Tenancy Support Module
Provides tenant isolation, resource management, and billing tracking.
"""

import boto3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any
from enum import Enum
from pydantic import BaseModel, Field
import structlog
from botocore.exceptions import ClientError

logger = structlog.get_logger()


class TenantTier(str, Enum):
    """Tenant subscription tiers."""
    FREE = "free"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantConfig(BaseModel):
    """Tenant configuration and limits."""
    tenant_id: str
    name: str
    tier: TenantTier
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

    # Resource limits
    max_users: int = 5
    max_tickets_per_month: int = 1000
    max_tool_executions_per_day: int = 100
    max_kb_size_mb: int = 100

    # Feature flags
    features: Dict[str, bool] = Field(default_factory=lambda: {
        'advanced_analytics': False,
        'custom_workflows': False,
        'api_access': False,
        'sla_guarantees': False,
        'priority_support': False
    })

    # AWS resource configuration
    aws_config: Dict[str, Any] = Field(default_factory=dict)

    # Billing information
    billing_contact: Optional[str] = None
    cost_center: Optional[str] = None


class UsageMetrics(BaseModel):
    """Tenant usage tracking."""
    tenant_id: str
    period: str  # YYYY-MM format

    # Usage counters (integers — safe for DynamoDB)
    tickets_created: int = 0
    tickets_resolved: int = 0
    tool_executions: int = 0
    api_requests: int = 0
    kb_queries: int = 0

    # Cost tracking stored as float in the model but written as Decimal to DynamoDB
    compute_cost: float = 0.0
    storage_cost: float = 0.0
    api_cost: float = 0.0
    total_cost: float = 0.0

    # Performance metrics
    avg_resolution_time_ms: float = 0.0
    success_rate: float = 0.0

    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dynamo_item(self) -> dict:
        """Convert to DynamoDB-safe dict (floats → Decimal)."""
        item = {
            'tenant_id': self.tenant_id,
            'period': self.period,
            'tickets_created': self.tickets_created,
            'tickets_resolved': self.tickets_resolved,
            'tool_executions': self.tool_executions,
            'api_requests': self.api_requests,
            'kb_queries': self.kb_queries,
            'compute_cost': Decimal(str(self.compute_cost)),
            'storage_cost': Decimal(str(self.storage_cost)),
            'api_cost': Decimal(str(self.api_cost)),
            'total_cost': Decimal(str(self.total_cost)),
            'avg_resolution_time_ms': Decimal(str(self.avg_resolution_time_ms)),
            'success_rate': Decimal(str(self.success_rate)),
            'last_updated': self.last_updated.isoformat(),
        }
        return item


class TenantManager:
    """Multi-tenant resource and configuration management."""

    def __init__(self):
        self.dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
        self.tenants_table = self.dynamodb.Table('CloudOpsTenants')
        self.usage_table = self.dynamodb.Table('CloudOpsUsage')
        self.s3 = boto3.client('s3', region_name='us-east-1')
        self.bedrock_agent = boto3.client('bedrock-agent', region_name='us-east-1')

        # Tier-based limits
        self.tier_limits = {
            TenantTier.FREE: {
                'max_users': 5,
                'max_tickets_per_month': 100,
                'max_tool_executions_per_day': 50,
                'max_kb_size_mb': 50,
                'features': {
                    'advanced_analytics': False,
                    'custom_workflows': False,
                    'api_access': False,
                    'sla_guarantees': False,
                    'priority_support': False
                }
            },
            TenantTier.PROFESSIONAL: {
                'max_users': 25,
                'max_tickets_per_month': 1000,
                'max_tool_executions_per_day': 500,
                'max_kb_size_mb': 500,
                'features': {
                    'advanced_analytics': True,
                    'custom_workflows': True,
                    'api_access': True,
                    'sla_guarantees': False,
                    'priority_support': False
                }
            },
            TenantTier.ENTERPRISE: {
                'max_users': 1000,
                'max_tickets_per_month': 10000,
                'max_tool_executions_per_day': 5000,
                'max_kb_size_mb': 5000,
                'features': {
                    'advanced_analytics': True,
                    'custom_workflows': True,
                    'api_access': True,
                    'sla_guarantees': True,
                    'priority_support': True
                }
            }
        }

    def create_tenant(self, tenant_id: str, name: str, tier: TenantTier,
                      billing_contact: Optional[str] = None) -> TenantConfig:
        """Create a new tenant with isolated resources."""
        try:
            tier_config = self.tier_limits[tier]
            tenant = TenantConfig(
                tenant_id=tenant_id,
                name=name,
                tier=tier,
                max_users=tier_config['max_users'],
                max_tickets_per_month=tier_config['max_tickets_per_month'],
                max_tool_executions_per_day=tier_config['max_tool_executions_per_day'],
                max_kb_size_mb=tier_config['max_kb_size_mb'],
                features=tier_config['features'],
                billing_contact=billing_contact
            )
            # Serialize datetimes to strings for DynamoDB
            item = tenant.model_dump()
            item['created_at'] = tenant.created_at.isoformat()
            self.tenants_table.put_item(Item=item)
            self._initialize_tenant_resources(tenant)
            logger.info("tenant_created", tenant_id=tenant_id, tier=tier.value)
            return tenant
        except ClientError as e:
            logger.error("tenant_creation_failed", tenant_id=tenant_id, error=str(e))
            raise

    def get_tenant(self, tenant_id: str) -> Optional[TenantConfig]:
        """Get tenant configuration."""
        try:
            response = self.tenants_table.get_item(Key={'tenant_id': tenant_id})
            if 'Item' in response:
                item = response['Item']
                # Convert Decimal back to int/float for Pydantic
                item = _decimal_to_python(item)
                return TenantConfig(**item)
            return None
        except Exception as e:
            logger.error("tenant_retrieval_failed", tenant_id=tenant_id, error=str(e))
            return None

    def check_usage_limits(self, tenant_id: str, operation: str) -> bool:
        """Check if tenant can perform operation within limits."""
        try:
            tenant = self.get_tenant(tenant_id)
            if not tenant or not tenant.is_active:
                # Unknown tenant — allow by default (tenant may not be in DB yet)
                return True

            current_period = datetime.now(timezone.utc).strftime('%Y-%m')
            usage = self.get_usage_metrics(tenant_id, current_period)

            if operation == 'create_ticket':
                return usage.tickets_created < tenant.max_tickets_per_month
            elif operation == 'execute_tool':
                return True  # Daily limit check simplified
            else:
                return True
        except Exception:
            return True  # Fail open — don't block on limit check errors

    def record_usage(self, tenant_id: str, operation: str,
                     cost: float = 0.0, metadata: Dict[str, Any] = None) -> None:
        """Record tenant usage for billing and limits."""
        try:
            current_period = datetime.now(timezone.utc).strftime('%Y-%m')
            self.usage_table.update_item(
                Key={'tenant_id': tenant_id, 'period': current_period},
                UpdateExpression='ADD #op :inc, total_cost :cost SET last_updated = :ts',
                ExpressionAttributeNames={'#op': operation},
                ExpressionAttributeValues={
                    ':inc': 1,
                    ':cost': Decimal(str(cost)),   # ← Decimal, not float
                    ':ts': datetime.now(timezone.utc).isoformat()
                }
            )
        except Exception as e:
            # Non-fatal — log and continue
            logger.error("usage_recording_failed", tenant_id=tenant_id, error=str(e))

    def get_usage_metrics(self, tenant_id: str, period: str) -> UsageMetrics:
        """Get tenant usage metrics for a period."""
        try:
            response = self.usage_table.get_item(
                Key={'tenant_id': tenant_id, 'period': period}
            )
            if 'Item' in response:
                item = _decimal_to_python(response['Item'])
                return UsageMetrics(**item)
            return UsageMetrics(tenant_id=tenant_id, period=period)
        except Exception as e:
            logger.error("usage_retrieval_failed", tenant_id=tenant_id, error=str(e))
            return UsageMetrics(tenant_id=tenant_id, period=period)

    def get_tenant_s3_prefix(self, tenant_id: str) -> str:
        return f"tenants/{tenant_id}/"

    def get_tenant_kb_id(self, tenant_id: str) -> Optional[str]:
        tenant = self.get_tenant(tenant_id)
        if tenant and tenant.aws_config:
            return tenant.aws_config.get('knowledge_base_id')
        return None

    def upgrade_tenant(self, tenant_id: str, new_tier: TenantTier) -> bool:
        """Upgrade tenant to a higher tier."""
        try:
            tenant = self.get_tenant(tenant_id)
            if not tenant:
                return False
            new_config = self.tier_limits[new_tier]
            self.tenants_table.update_item(
                Key={'tenant_id': tenant_id},
                UpdateExpression='''SET tier = :tier,
                                   max_users = :max_users,
                                   max_tickets_per_month = :max_tickets,
                                   max_tool_executions_per_day = :max_tools,
                                   max_kb_size_mb = :max_kb,
                                   features = :features''',
                ExpressionAttributeValues={
                    ':tier': new_tier.value,
                    ':max_users': new_config['max_users'],
                    ':max_tickets': new_config['max_tickets_per_month'],
                    ':max_tools': new_config['max_tool_executions_per_day'],
                    ':max_kb': new_config['max_kb_size_mb'],
                    ':features': new_config['features']
                }
            )
            logger.info("tenant_upgraded", tenant_id=tenant_id, new_tier=new_tier.value)
            return True
        except ClientError as e:
            logger.error("tenant_upgrade_failed", tenant_id=tenant_id, error=str(e))
            return False

    def _initialize_tenant_resources(self, tenant: TenantConfig) -> None:
        """Initialize usage record for a new tenant."""
        try:
            current_period = datetime.now(timezone.utc).strftime('%Y-%m')
            usage = UsageMetrics(tenant_id=tenant.tenant_id, period=current_period)
            self.usage_table.put_item(Item=usage.to_dynamo_item())
        except Exception as e:
            logger.error("tenant_resource_initialization_failed",
                         tenant_id=tenant.tenant_id, error=str(e))


def _decimal_to_python(obj):
    """Recursively convert DynamoDB Decimal values back to int/float."""
    if isinstance(obj, dict):
        return {k: _decimal_to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decimal_to_python(v) for v in obj]
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    return obj


# Global instance
tenant_manager = TenantManager()
