"""
Change approval — enforced human-in-the-loop for every mutating tool call.

The agent may *propose* any change, but it can never perform one. A Strands hook
(:class:`ApprovalGate`) intercepts each mutating tool call before it runs,
records the exact tool name, arguments, target account and region as a pending
action in DynamoDB, and returns "queued for approval" to the model instead of
executing. The console shows each pending action with an Approve / Reject
button. Approving calls :func:`execute_actions`, which re-loads the stored
action, atomically moves it to ``executing`` (so it can only run once), and
invokes the tool with the stored arguments under the stored account context —
no LLM is involved in the execution step, so what was approved is exactly what
runs.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional

import boto3
import structlog
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

import config
from src.core.aws_clients import use_account

logger = structlog.get_logger()

# Tool names starting with these prefixes only read state.
READ_ONLY_PREFIXES = (
    "list_", "analyze_", "get_", "describe_", "check_", "inspect_",
    "search_", "detect_", "validate_", "lookup_", "prepare_", "query_",
)
# Read-only tools whose names don't follow the prefix convention.
READ_ONLY_TOOLS = frozenset({"create_deployment_plan"})

# Roles allowed to approve changes. Viewers can only read.
APPROVER_ROLES = frozenset({"operator", "sre", "admin"})

STATUS_PENDING = "pending"
STATUS_EXECUTING = "executing"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"

_MAX_RESULT_CHARS = 30000


def is_mutating(tool_name: str) -> bool:
    return not (tool_name.startswith(READ_ONLY_PREFIXES) or tool_name in READ_ONLY_TOOLS)


def describe_call(tool_name: str, tool_input: dict) -> str:
    """Short human-readable form of a tool call, e.g. stop_ec2_instance(instance_id='i-1')."""
    args = ", ".join(f"{k}={v!r}" for k, v in (tool_input or {}).items())
    text = f"{tool_name}({args})"
    return text if len(text) <= 300 else text[:297] + "..."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_dynamo(value):
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


class ActionStore:
    """DynamoDB persistence for proposed actions (table key: action_id)."""

    def __init__(self, table_name: str = None, region: str = None):
        self.table = boto3.resource(
            "dynamodb", region_name=region or config.AWS_REGION
        ).Table(table_name or config.ACTIONS_TABLE)

    def create(self, *, tenant_id: str, session_id: str, requested_by: str,
               account_id: Optional[str], region: Optional[str],
               tool_name: str, tool_input: dict) -> dict:
        now = int(time.time())
        item = {
            "action_id": uuid.uuid4().hex[:16],
            "tenant_id": tenant_id,
            "session_id": session_id,
            "requested_by": requested_by,
            "account_id": account_id or "",
            "region": region or "",
            "tool_name": tool_name,
            "tool_input": json.dumps(tool_input or {}, default=str),
            "summary": describe_call(tool_name, tool_input),
            "status": STATUS_PENDING,
            "created_at": _now_iso(),
            "expires_at": now + config.ACTION_TTL_SECONDS,
        }
        self.table.put_item(Item=item)
        return self._public(item)

    def get(self, action_id: str) -> Optional[dict]:
        item = self.table.get_item(Key={"action_id": action_id}).get("Item")
        return self._public(item) if item else None

    def list_for_session(self, tenant_id: str, session_id: str, status: str = STATUS_PENDING) -> List[dict]:
        resp = self.table.query(
            IndexName="by-session",
            KeyConditionExpression=Key("session_id").eq(session_id),
            FilterExpression=Attr("tenant_id").eq(tenant_id) & Attr("status").eq(status),
        )
        now = int(time.time())
        items = [self._public(i) for i in resp.get("Items", [])]
        return [i for i in items if i["expires_at"] > now]

    def transition(self, action_id: str, from_status: str, to_status: str, **fields) -> bool:
        """Atomically move an action between states. Returns False if it was not in from_status."""
        names = {"#s": "status"}
        values = {":from": from_status, ":to": to_status}
        sets = ["#s = :to"]
        for i, (k, v) in enumerate(fields.items()):
            names[f"#f{i}"] = k
            values[f":v{i}"] = v
            sets.append(f"#f{i} = :v{i}")
        try:
            self.table.update_item(
                Key={"action_id": action_id},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression="#s = :from",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    @staticmethod
    def _public(item: dict) -> dict:
        item = _from_dynamo(dict(item))
        try:
            item["tool_input"] = json.loads(item.get("tool_input") or "{}")
        except (TypeError, ValueError):
            item["tool_input"] = {}
        return item


class ApprovalGate(HookProvider):
    """
    Strands hook that queues mutating tool calls instead of running them.

    One gate is created per agent run, bound to the request's tenant, user,
    session and the account/region the run targets.
    """

    def __init__(self, store: ActionStore, *, tenant_id: str, session_id: str,
                 requested_by: str, account_id: Optional[str], region: Optional[str]):
        self.store = store
        self.context = dict(tenant_id=tenant_id, session_id=session_id,
                            requested_by=requested_by, account_id=account_id, region=region)
        self.queued: List[dict] = []

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool_call)

    def _before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        if not is_mutating(name):
            return
        tool_input = event.tool_use.get("input") or {}
        try:
            action = self.store.create(tool_name=name, tool_input=tool_input, **self.context)
        except Exception as e:  # never fall through to executing the tool
            logger.error("action_queue_failed", tool=name, error=str(e))
            event.cancel_tool = (
                f"NOT EXECUTED: {name} changes resources and could not be queued for approval "
                f"({e}). Tell the operator the change was not made."
            )
            return
        self.queued.append(action)
        logger.info("action_queued", action_id=action["action_id"], tool=name)
        event.cancel_tool = (
            f"NOT EXECUTED YET: {name} changes resources, so it was queued as action "
            f"{action['action_id']} for operator approval. Explain exactly what it will do and "
            f"that the operator can approve it in the console. Do not call it again this turn, "
            f"and do not claim the change has been made."
        )


def _result_failed(result) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return False
    return isinstance(result, dict) and result.get("status") == "failed"


def execute_actions(action_ids: List[str], *, tenant_id: str, approver: str, approver_role: str,
                    tools: Dict[str, Callable], store: ActionStore = None,
                    audit: Callable[..., None] = None) -> List[dict]:
    """
    Run approved actions exactly as they were recorded.

    Returns one entry per id: {action_id, summary, account_id, region, status, result|error}.
    """
    store = store or ActionStore()
    results = []
    for action_id in action_ids:
        entry = {"action_id": action_id}
        results.append(entry)

        action = store.get(action_id)
        if not action or action["tenant_id"] != tenant_id:
            entry.update(status="error", error="Action not found.")
            continue
        entry.update(summary=action["summary"], account_id=action["account_id"] or None,
                     region=action["region"] or None, tool_name=action["tool_name"])
        if action["status"] != STATUS_PENDING:
            entry.update(status="error", error=f"Action is already {action['status']}.")
            continue
        if action["expires_at"] <= time.time():
            entry.update(status="error", error="Action expired; ask the agent to propose it again.")
            continue
        if approver_role not in APPROVER_ROLES:
            entry.update(status="error", error=f"Role '{approver_role}' cannot approve changes.")
            continue
        if config.REQUIRE_SEPARATE_APPROVER and approver == action["requested_by"]:
            entry.update(status="error", error="A different person must approve this change.")
            continue
        tool = tools.get(action["tool_name"])
        if tool is None:
            entry.update(status="error", error=f"Unknown tool {action['tool_name']}.")
            continue
        if not store.transition(action_id, STATUS_PENDING, STATUS_EXECUTING,
                                approved_by=approver, approved_at=_now_iso()):
            entry.update(status="error", error="Action was already approved or rejected.")
            continue

        try:
            with use_account(action["account_id"] or None, action["region"] or None):
                result = tool(**action["tool_input"])
            status = STATUS_FAILED if _result_failed(result) else STATUS_SUCCEEDED
            text = result if isinstance(result, str) else json.dumps(result, default=str)
            entry.update(status=status, result=text[:_MAX_RESULT_CHARS])
        except Exception as e:
            logger.error("action_execution_failed", action_id=action_id, error=str(e))
            status = STATUS_FAILED
            entry.update(status=status, error=str(e))

        store.transition(action_id, STATUS_EXECUTING, status, finished_at=_now_iso(),
                         result=(entry.get("result") or entry.get("error") or "")[:_MAX_RESULT_CHARS])
        if audit:
            audit(action="action_executed", resource=action["summary"], result=status.upper(),
                  details={"action_id": action_id, "account_id": action["account_id"],
                           "region": action["region"], "requested_by": action["requested_by"]})
    return results


def reject_action(action_id: str, *, tenant_id: str, user: str, store: ActionStore = None) -> bool:
    store = store or ActionStore()
    action = store.get(action_id)
    if not action or action["tenant_id"] != tenant_id:
        return False
    return store.transition(action_id, STATUS_PENDING, STATUS_REJECTED,
                            rejected_by=user, rejected_at=_now_iso())
