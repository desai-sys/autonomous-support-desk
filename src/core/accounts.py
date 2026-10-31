"""
Managed-account registry.

The agent only assumes roles in accounts listed here (plus its own account), so
a typo or a prompt-injected account id can't make it reach an unrelated
account. Admins add accounts on the console's Accounts page; a static
allow-list can also be supplied via the MANAGED_ACCOUNTS environment variable.
"""

import re
from datetime import datetime, timezone
from typing import Dict, Optional

import boto3
import structlog

import config

logger = structlog.get_logger()

ACCOUNT_ID_RE = re.compile(r"^\d{12}$")


class AccountRegistry:
    def __init__(self, table_name: str = None, region: str = None):
        self.table = boto3.resource(
            "dynamodb", region_name=region or config.AWS_REGION
        ).Table(table_name or config.ACCOUNTS_TABLE)

    def list(self) -> Dict[str, dict]:
        """All managed accounts as {account_id: {name, added_by, added_at, source}}."""
        accounts = {
            acc: {"account_id": acc, "name": name, "source": "config"}
            for acc, name in config.MANAGED_ACCOUNTS.items()
        }
        try:
            resp = self.table.scan()
            items = resp.get("Items", [])
            while resp.get("LastEvaluatedKey"):
                resp = self.table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
                items += resp.get("Items", [])
            for item in items:
                accounts[item["account_id"]] = {**item, "source": "console"}
        except Exception as e:
            logger.error("account_registry_read_failed", error=str(e))
        return accounts

    def is_allowed(self, account_id: str) -> bool:
        return account_id in self.list()

    def add(self, account_id: str, name: str, added_by: str) -> dict:
        account_id = account_id.strip().replace("-", "")
        if not ACCOUNT_ID_RE.match(account_id):
            raise ValueError("Account IDs are 12 digits.")
        item = {
            "account_id": account_id,
            "name": (name or account_id).strip()[:64],
            "added_by": added_by,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        self.table.put_item(Item=item)
        return item

    def remove(self, account_id: str) -> None:
        self.table.delete_item(Key={"account_id": account_id})

    def name_of(self, account_id: Optional[str]) -> Optional[str]:
        return self.list().get(account_id or "", {}).get("name")
