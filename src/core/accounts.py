"""
Managed-account registry.

The agent only assumes roles in accounts listed here (plus its own account), so
a typo or a prompt-injected account id can't make it reach an unrelated
account. Each account belongs to one tenant, and a tenant only ever sees and
reaches its own. Admins add accounts on the console's Accounts page; a static
allow-list for the default tenant can also be supplied via MANAGED_ACCOUNTS.
"""

import re
from datetime import datetime, timezone
from typing import Dict, Optional

import boto3
import structlog
from botocore.exceptions import ClientError

import config

logger = structlog.get_logger()

ACCOUNT_ID_RE = re.compile(r"^\d{12}$")


class AccountRegistry:
    def __init__(self, table_name: str = None, region: str = None):
        self.table = boto3.resource(
            "dynamodb", region_name=region or config.AWS_REGION
        ).Table(table_name or config.ACCOUNTS_TABLE)

    def list(self, tenant_id: Optional[str] = None) -> Dict[str, dict]:
        """A tenant's managed accounts as {account_id: {name, tenant_id, added_by, added_at, source}}."""
        tenant_id = tenant_id or config.DEFAULT_TENANT_ID
        accounts = {
            acc: {"account_id": acc, "name": name, "source": "config", "tenant_id": config.DEFAULT_TENANT_ID}
            for acc, name in config.MANAGED_ACCOUNTS.items()
        } if tenant_id == config.DEFAULT_TENANT_ID else {}
        try:
            resp = self.table.scan()
            items = resp.get("Items", [])
            while resp.get("LastEvaluatedKey"):
                resp = self.table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
                items += resp.get("Items", [])
            for item in items:
                # Accounts registered before tenants were recorded belong to the default tenant.
                owner = item.get("tenant_id") or config.DEFAULT_TENANT_ID
                if owner == tenant_id:
                    accounts[item["account_id"]] = {**item, "tenant_id": owner, "source": "console"}
        except Exception as e:
            logger.error("account_registry_read_failed", error=str(e))
        return accounts

    def is_allowed(self, account_id: str, tenant_id: Optional[str] = None) -> bool:
        return account_id in self.list(tenant_id)

    def add(self, account_id: str, name: str, added_by: str, tenant_id: Optional[str] = None) -> dict:
        account_id = account_id.strip().replace("-", "")
        if not ACCOUNT_ID_RE.match(account_id):
            raise ValueError("Account IDs are 12 digits.")
        tenant_id = tenant_id or config.DEFAULT_TENANT_ID
        item = {
            "account_id": account_id,
            "tenant_id": tenant_id,
            "name": (name or account_id).strip()[:64],
            "added_by": added_by,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            # An account belongs to one tenant: only its owner may re-register it.
            self.table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(account_id) OR tenant_id = :t"
                                    + (" OR attribute_not_exists(tenant_id)"
                                       if tenant_id == config.DEFAULT_TENANT_ID else ""),
                ExpressionAttributeValues={":t": tenant_id})
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError("That account is already registered to another organisation.") from e
            raise
        return item

    def remove(self, account_id: str, tenant_id: Optional[str] = None) -> bool:
        """Remove one of the tenant's accounts. False if it isn't theirs."""
        if account_id not in self.list(tenant_id):
            return False
        self.table.delete_item(Key={"account_id": account_id})
        return True

    def name_of(self, account_id: Optional[str], tenant_id: Optional[str] = None) -> Optional[str]:
        return self.list(tenant_id).get(account_id or "", {}).get("name")
