"""
Per-tenant usage metering with an optional daily request limit.

Counters live in the usage table (partition key ``tenant_id``, sort key
``period`` = ``YYYY-MM-DD``) and expire after 400 days.
"""

import time
from datetime import datetime, timezone

import boto3
import structlog

import config

logger = structlog.get_logger()

# 0 disables the limit.
DAILY_REQUEST_LIMIT = config._env_int("DAILY_REQUEST_LIMIT_PER_TENANT", 0)
_RETENTION_SECONDS = 400 * 24 * 3600


class UsageMeter:
    def __init__(self):
        self._table = None

    @property
    def table(self):
        if self._table is None:
            self._table = boto3.resource(
                "dynamodb", region_name=config.AWS_REGION
            ).Table(config.USAGE_TABLE)
        return self._table

    @staticmethod
    def _period() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def record(self, tenant_id: str, counter: str, amount: int = 1) -> int:
        """Increment a counter for today and return its new value (0 on error)."""
        try:
            resp = self.table.update_item(
                Key={"tenant_id": tenant_id, "period": self._period()},
                UpdateExpression="ADD #c :n SET expires_at = :exp",
                ExpressionAttributeNames={"#c": counter},
                ExpressionAttributeValues={":n": amount, ":exp": int(time.time()) + _RETENTION_SECONDS},
                ReturnValues="UPDATED_NEW",
            )
            return int(resp["Attributes"].get(counter, 0))
        except Exception as e:
            logger.error("usage_record_failed", tenant_id=tenant_id, error=str(e))
            return 0

    def allow_request(self, tenant_id: str) -> bool:
        """Count an agent request; False once the tenant's daily limit is exceeded."""
        count = self.record(tenant_id, "agent_requests")
        return DAILY_REQUEST_LIMIT <= 0 or count <= DAILY_REQUEST_LIMIT


usage_meter = UsageMeter()
