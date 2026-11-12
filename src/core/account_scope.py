"""
Per-call account targeting for a single agent run.

Instead of running one isolated agent per account, the agent gets every tool
with an extra optional ``account`` parameter (a registered account's name or id,
or "platform"). Each call runs under that account's assumed-role credentials, so
one turn can read from several accounts and queue changes that span them, e.g.
request a VPC peering in one account and accept it in another.

:class:`AccountDirectory` resolves what the model wrote to an account id and
records which accounts were actually used; :class:`AccountScopedTool` wraps a
Strands tool to add the parameter and apply the account around the call.
"""

import copy
import re
from typing import Any, Dict, Iterable, Optional

from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool

from src.core.aws_clients import use_account

PLATFORM_ALIASES = {"platform", "platform account", "base", "this account", "home"}
_ID_RE = re.compile(r"^\d{12}$")
_MESSAGE_ID_RE = re.compile(r"(?<![:\d])(\d{12})(?![\d:])")


class UnknownAccount(ValueError):
    pass


class AccountDirectory:
    """The accounts one request may use: the platform account plus registered ones."""

    def __init__(self, platform_id: Optional[str], registered: Dict[str, str],
                 default: Optional[str] = None, region: Optional[str] = None,
                 platform_allowed: bool = True):
        self.platform_id = platform_id
        self.registered = {a: (n or a) for a, n in registered.items() if a != platform_id}
        self.platform_allowed = platform_allowed
        self.default = default if default in self.registered else None
        # A tenant without the platform account and with exactly one account means that one.
        if self.default is None and not platform_allowed and len(self.registered) == 1:
            self.default = next(iter(self.registered))
        self.region = region
        self.used: Dict[Optional[str], str] = {}

    def resolve(self, ref: Any) -> Optional[str]:
        """Account id for a name/id/alias (None = platform account). Raises UnknownAccount."""
        text = str(ref or "").strip()
        if not text or text.lower() == "default":
            account = self.default
        elif text.lower() in PLATFORM_ALIASES or text == self.platform_id:
            account = None
        elif _ID_RE.match(text.replace("-", "")) and text.replace("-", "") in self.registered:
            account = text.replace("-", "")
        else:
            matches = [a for a, n in self.registered.items() if n.lower() == text.lower()]
            if len(matches) != 1:
                raise UnknownAccount(f"Unknown account {text!r}. Use one of: {self._choices()}. "
                                     f"Other accounts must be registered by an admin first.")
            account = matches[0]
        if account is None and not self.platform_allowed:
            raise UnknownAccount(
                "The platform account is not available to this organisation. "
                + (f"Pass `account` as one of: {self._choices()}." if self.registered
                   else "An admin must register an account on the Accounts page first."))
        self.used[account] = self.label(account)
        return account

    def _choices(self) -> str:
        names = ["platform"] if self.platform_allowed else []
        return ", ".join(names + [f"{n} ({a})" for a, n in self.registered.items()])

    def label(self, account: Optional[str]) -> str:
        if account is None:
            return f"platform account ({self.platform_id})" if self.platform_id else "platform account"
        return f"{self.registered.get(account, account)} ({account})"

    def unregistered_ids_in(self, text: str) -> list:
        return [a for a in dict.fromkeys(_MESSAGE_ID_RE.findall(text or ""))
                if a != self.platform_id and a not in self.registered]

    def prompt_note(self) -> str:
        lines = ["ACCOUNTS: every tool has an optional `account` parameter. Pass the account to run "
                 "each call in; one request may use several accounts (e.g. compare them, or peer VPCs "
                 "across them). Available accounts:"]
        if self.platform_allowed:
            lines.append(f"- platform — {self.label(None)}")
        lines += [f"- {n} — {a}" for a, n in self.registered.items()]
        if self.default is not None or self.platform_allowed:
            lines.append(f"If `account` is omitted the call runs in {self.label(self.default)}, the "
                         f"operator's selected default.")
        else:
            lines.append("There is no default account: always pass `account`.")
        lines.append("Only state facts about an account after calling a tool in THAT account during "
                     "this turn; never infer one account's state from another's. Label results by "
                     "account.")
        return "\n".join(lines)


class AccountScopedTool(AgentTool):
    """Adds an ``account`` parameter to a tool and runs each call in that account."""

    def __init__(self, inner: AgentTool, directory: AccountDirectory):
        super().__init__()
        self._inner = inner
        self._directory = directory
        spec = copy.deepcopy(inner.tool_spec)
        schema = spec.setdefault("inputSchema", {}).setdefault("json", {"type": "object", "properties": {}})
        schema.setdefault("properties", {})["account"] = {
            "type": "string",
            "description": "Account to run in: 'platform', or a registered account's name or 12-digit id. "
                           "Omit for the operator's default account.",
        }
        self._spec = spec

    @property
    def tool_name(self) -> str:
        return self._inner.tool_name

    @property
    def tool_spec(self):
        return self._spec

    @property
    def tool_type(self) -> str:
        return self._inner.tool_type

    async def stream(self, tool_use, invocation_state, **kwargs):
        tool_input = dict(tool_use.get("input") or {})
        ref = tool_input.pop("account", None)
        try:
            account = self._directory.resolve(ref)
        except UnknownAccount as e:
            yield ToolResultEvent({"toolUseId": tool_use.get("toolUseId", ""), "status": "error",
                                   "content": [{"text": str(e)}]})
            return
        inner_use = {**tool_use, "input": tool_input}
        # Collect inside the account context so nothing outside the call sees it.
        with use_account(account, self._directory.region):
            events = [event async for event in self._inner.stream(inner_use, invocation_state, **kwargs)]
        for event in events:
            yield event


def scope_tools(tools: Iterable[AgentTool], directory: AccountDirectory) -> list:
    return [AccountScopedTool(t, directory) for t in tools]


def resolve_account_ref(ref: Any) -> Optional[str]:
    """
    Resolve 'platform' / a registered account's name or id to an account id (None =
    platform) outside an agent run, e.g. for tools that act in two accounts at once.
    Uses the account registry, so it works both while proposing and when an approved
    action is executed later.
    """
    from src.core import request_context, tenancy
    from src.core.accounts import AccountRegistry
    from src.core.aws_clients import base_account_id
    tenant = request_context.tenant_id()
    registered = {a: info.get("name") or a for a, info in AccountRegistry().list(tenant).items()}
    return AccountDirectory(base_account_id(), registered,
                            platform_allowed=tenancy.platform_allowed(tenant)).resolve(ref or "platform")
