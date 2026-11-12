"""
Conversation memory.

Each chat turn is recorded with the tools it ran (and what they returned) and
the changes it proposed; when approved actions run, their results are recorded
too. The next turn is built from these records, so a follow-up such as "peer the
two VPCs you just created" works from the real ids rather than from whatever
text the browser happened to keep.

Records are per tenant and conversation and expire after 30 days. Memory is
best-effort: if the table can't be read or written the request still runs.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import boto3
import structlog
from boto3.dynamodb.conditions import Key
from strands.hooks import AfterToolCallEvent, HookProvider, HookRegistry

import config

logger = structlog.get_logger()

_TTL_SECONDS = 30 * 24 * 3600
_MAX_TOOLS_PER_TURN = 12
_RESULT_CHARS = 1200
_PROMPT_BUDGET = 12000


def _clip(text, limit: int) -> str:
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _redacted(text: str) -> str:
    """Tool output with secret-looking values removed before it is stored."""
    from src.tools.aws_api_tools import redact
    try:
        return json.dumps(redact(json.loads(text)), default=str)
    except (TypeError, ValueError):
        return text


class ToolRecorder(HookProvider):
    """Strands hook that keeps a short record of each tool call in a turn."""

    def __init__(self):
        self.calls: List[dict] = []

    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AfterToolCallEvent, self._after_tool_call)

    def _after_tool_call(self, event: AfterToolCallEvent) -> None:
        if len(self.calls) >= _MAX_TOOLS_PER_TURN:
            return
        try:
            result = event.result or {}
            text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
            if text.startswith(("NOT EXECUTED", "NOT QUEUED")):
                return                     # proposals are recorded separately; refusals aren't facts
            tool_input = dict(event.tool_use.get("input") or {})
            self.calls.append({"tool": event.tool_use.get("name", ""),
                               "account": str(tool_input.pop("account", "") or ""),
                               "input": _clip(tool_input, 300),
                               "result": _clip(_redacted(text), _RESULT_CHARS)})
        except Exception as e:             # recording must never break a tool call
            logger.warning("tool_record_failed", error=str(e))


class ConversationStore:
    def __init__(self, table_name: str = None):
        self._table_name = table_name or config.CONVERSATIONS_TABLE
        self._table = None

    @property
    def table(self):
        if self._table is None:
            self._table = boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(self._table_name)
        return self._table

    @staticmethod
    def _key(tenant_id: str, session_id: str) -> str:
        return f"{tenant_id}#{session_id}"

    def _put(self, tenant_id: str, session_id: str, record: dict) -> None:
        if not session_id:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.table.put_item(Item={"conversation": self._key(tenant_id, session_id),
                                      "sk": f"{now}#{uuid.uuid4().hex[:8]}", "at": now,
                                      "expires_at": int(time.time()) + _TTL_SECONDS,
                                      "record": json.dumps(record, default=str)})
        except Exception as e:
            logger.warning("conversation_write_failed", error=str(e))

    def record_turn(self, tenant_id: str, session_id: str, user_message: str, answer: str,
                    tools: List[dict], proposed: List[dict]) -> None:
        self._put(tenant_id, session_id, {
            "kind": "turn", "user": _clip(user_message, 2000), "answer": _clip(answer, 4000),
            "tools": tools[:_MAX_TOOLS_PER_TURN],
            "proposed": [{"action_id": a["action_id"], "summary": a["summary"],
                          "account": a.get("account_id") or "platform"} for a in proposed]})

    def record_execution(self, tenant_id: str, session_id: str, approver: str, results: List[dict]) -> None:
        entries = [{"action_id": r["action_id"], "summary": r.get("summary", ""), "status": r["status"],
                    "account": r.get("account_id") or "platform",
                    "detail": _clip(_redacted(r.get("result") or r.get("error") or ""), 1500)}
                   for r in results if r["status"] != "skipped"]
        if entries:
            self._put(tenant_id, session_id, {"kind": "execution", "by": approver, "results": entries})

    def recent(self, tenant_id: str, session_id: str, limit: int = 12) -> List[dict]:
        """The last ``limit`` records, oldest first ([] if memory is unavailable)."""
        if not session_id:
            return []
        try:
            items = self.table.query(KeyConditionExpression=Key("conversation").eq(self._key(tenant_id, session_id)),
                                     ScanIndexForward=False, Limit=limit).get("Items", [])
        except Exception as e:
            logger.warning("conversation_read_failed", error=str(e))
            return []
        return [json.loads(i["record"]) for i in reversed(items)]


def render(records: List[dict], budget: int = _PROMPT_BUDGET) -> Optional[str]:
    """Prompt text for earlier turns, newest kept when the budget runs out (None if nothing)."""
    blocks = []
    for record in records:
        if record.get("kind") == "turn":
            lines = [f"User: {record['user']}", f"Assistant: {record['answer']}"]
            for t in record.get("tools", []):
                where = f" in {t['account']}" if t.get("account") else ""
                lines.append(f"  [tool {t['tool']}({t['input']}){where} returned: {t['result']}]")
            for p in record.get("proposed", []):
                lines.append(f"  [proposed for approval, not yet run: {p['summary']} in {p['account']} "
                             f"(action {p['action_id']})]")
        else:
            lines = [f"[Approved actions were run by {record.get('by', 'an operator')}:"]
            lines += [f"  {r['status'].upper()}: {r['summary']} in {r['account']} -> {r['detail']}"
                      for r in record.get("results", [])]
            lines[-1] += "]"
        blocks.append("\n".join(lines))
    kept, used = [], 0
    for block in reversed(blocks):
        if used + len(block) > budget and kept:
            break
        kept.append(block)
        used += len(block)
    if not kept:
        return None
    return ("Conversation so far (from the platform's records; tool results and executed actions "
            "are facts, proposals are not done until run):\n" + "\n".join(reversed(kept)))


def history_lines(records: List[dict]) -> List[str]:
    """Short 'User: ...' / 'Assistant: ...' lines for the domain router."""
    lines = []
    for record in records:
        if record.get("kind") == "turn":
            lines += [f"User: {record['user'][:500]}", f"Assistant: {record['answer'][:500]}"]
    return lines
