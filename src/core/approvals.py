"""
Change approval — enforced human-in-the-loop for every mutating tool call.

The agent may *propose* any change, but it can never perform one. A Strands hook
(:class:`ApprovalGate`) intercepts each mutating tool call before it runs,
records the exact tool name, arguments, target account and region as a pending
action in DynamoDB, and returns "queued for approval" to the model instead of
executing. The console shows each pending action with an Approve / Reject
button. Approving calls :func:`execute_actions`, which re-loads the stored
action, atomically moves it to ``executing`` (so it runs at most once), and
invokes the tool with the stored arguments under the stored account context —
no LLM is involved in the execution step, so what was approved is exactly what
runs. If the executor stops mid-run the action is marked ``interrupted``; it is
never retried automatically, because the change may or may not have happened.
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
STATUS_INTERRUPTED = "interrupted"
FINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED, STATUS_REJECTED, STATUS_INTERRUPTED})
INTERRUPTED_MESSAGE = ("The executor stopped before reporting a result, so it is unknown whether this "
                       "change was made. Check the resource in AWS before proposing it again; it was "
                       "not retried.")

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
        return self._reconcile(self._public(item)) if item else None

    def _reconcile(self, action: dict) -> dict:
        """Mark an action that has been 'executing' for too long as interrupted."""
        if action.get("status") != STATUS_EXECUTING:
            return action
        try:
            started = datetime.fromisoformat(action.get("approved_at", "")).timestamp()
        except ValueError:
            return action
        if time.time() - started > config.EXECUTION_TIMEOUT_SECONDS and self.transition(
                action["action_id"], STATUS_EXECUTING, STATUS_INTERRUPTED,
                finished_at=_now_iso(), result=INTERRUPTED_MESSAGE):
            logger.warning("action_interrupted", action_id=action["action_id"])
            action.update(status=STATUS_INTERRUPTED, result=INTERRUPTED_MESSAGE)
        return action

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
                 requested_by: str, account_id: Optional[str], region: Optional[str],
                 directory=None, checks=None):
        from src.core.proposal_checks import ProposalChecks
        self.store = store
        self.checks = checks if checks is not None else ProposalChecks()
        self.context = dict(tenant_id=tenant_id, session_id=session_id,
                            requested_by=requested_by, account_id=account_id, region=region)
        # With an AccountDirectory, each call's own `account` argument decides where
        # the queued action will run (see src/core/account_scope.py).
        self.directory = directory
        self.queued: List[dict] = []
        self.companions: list = []          # other hooks to run in the same turn (e.g. memory)

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool_call)

    def _before_tool_call(self, event: BeforeToolCallEvent) -> None:
        name = event.tool_use.get("name", "")
        if not is_mutating(name):
            return
        tool_input = dict(event.tool_use.get("input") or {})
        context = dict(self.context)
        if self.directory is not None:
            try:
                context["account_id"] = self.directory.resolve(tool_input.pop("account", None))
            except ValueError as e:
                event.cancel_tool = f"NOT EXECUTED: {e}"
                return
        problem = self.checks.problem(name, tool_input)
        if problem:
            logger.info("action_refused", tool=name, reason=problem)
            event.cancel_tool = f"NOT QUEUED: {problem}"
            return
        try:
            action = self.store.create(tool_name=name, tool_input=tool_input, **context)
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
                    audit: Callable[..., None] = None, stop_on_failure: bool = False,
                    account_allowed: Callable[[Optional[str]], bool] = None) -> List[dict]:
    """
    Run approved actions exactly as they were recorded, in the order given.

    With ``stop_on_failure`` (dependent steps such as create-then-accept a VPC
    peering), the remaining actions are skipped and stay pending after a failure.

    Returns one entry per id: {action_id, summary, account_id, region, status, result|error}.
    """
    store = store or ActionStore()
    results = []
    for action_id in action_ids:
        entry = {"action_id": action_id}
        results.append(entry)
        if stop_on_failure and any(r["status"] in (STATUS_FAILED, "error") for r in results[:-1]):
            entry.update(status="skipped", error="Skipped because an earlier step failed; still pending.")
            continue

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
        if account_allowed is not None and not account_allowed(action["account_id"] or None):
            entry.update(status="error", error="The target account is no longer registered for "
                                               "your organisation.")
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


def recorded_results(action_ids: List[str], *, tenant_id: str, store: ActionStore = None) -> Optional[List[dict]]:
    """
    What the record says happened to these actions, in the shape execute_actions
    returns. None while any of them is still executing. Used to answer for an
    approval whose console request was lost while the executor carried on.
    """
    store = store or ActionStore()
    results = []
    for action_id in action_ids:
        action = store.get(action_id)
        if not action or action["tenant_id"] != tenant_id:
            results.append({"action_id": action_id, "status": "error", "error": "Action not found."})
            continue
        if action["status"] == STATUS_EXECUTING:
            return None
        entry = {"action_id": action_id, "summary": action["summary"], "tool_name": action["tool_name"],
                 "account_id": action["account_id"] or None, "region": action["region"] or None,
                 "status": action["status"] if action["status"] in FINAL_STATUSES else "skipped"}
        text = action.get("result") or ""
        if action["status"] == STATUS_SUCCEEDED:
            entry["result"] = text
        elif action["status"] in FINAL_STATUSES:
            entry["error"] = text or action["status"]
        else:
            entry["error"] = "Not run; still pending."
        results.append(entry)
    return results


def result_headline(result) -> str:
    """One-line summary of a tool result (deployment manifests and error JSON)."""
    if not result:
        return ""
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return str(result)[:300]
    if not isinstance(data, dict):
        return str(result)[:300]
    if data.get("error_message"):
        return data["error_message"][:300]
    if data.get("resource_arn"):
        return data["resource_arn"]
    return str(data.get("message") or "")[:300]


_ICONS = {STATUS_SUCCEEDED: "✅", STATUS_FAILED: "❌", "skipped": "⏭️"}


def describe_results(results: List[dict], account_label: Callable[[Optional[str]], str]) -> str:
    """The lines shown to the operator after an approval, one per action."""
    lines = []
    for r in results:
        detail = r.get("error") or result_headline(r.get("result"))
        lines.append(f"{_ICONS.get(r['status'], '⚠️')} {r.get('summary', r['action_id'])} in "
                     f"{account_label(r.get('account_id'))}: {r['status']}" + (f" — {detail}" if detail else ""))
    return "\n".join(lines)


def reject_action(action_id: str, *, tenant_id: str, user: str, store: ActionStore = None) -> bool:
    store = store or ActionStore()
    action = store.get(action_id)
    if not action or action["tenant_id"] != tenant_id:
        return False
    return store.transition(action_id, STATUS_PENDING, STATUS_REJECTED,
                            rejected_by=user, rejected_at=_now_iso())
