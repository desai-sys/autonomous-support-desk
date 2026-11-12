"""
The build pipeline: plan → write each file → check → repair → store → queue.

Why not let the agent write the app through tool calls? Tool calls carry their
arguments as JSON, and a mid-sized model asked to put several hundred lines of
code inside a JSON string often produces a malformed call. Here the model is
used for what it does well, one focused piece at a time:

1. Plan      one short JSON answer: which blueprint, a name, a spec.
2. Write     one call per file, answered as plain text in a code block.
3. Check     static checks (src/appbuilder/checks.py); nothing is executed.
4. Repair    problems go back to the model with the file, up to MAX_REPAIRS times.
5. Store     the files plus the platform's runtime and template become a new
             immutable version the operator can read.
6. Queue     deploy_generated_app is queued through the approval gate.

Nothing is created in any AWS account until a person approves the deployment.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import boto3 as _boto3
import structlog
from botocore.config import Config

import config
from src.appbuilder import blueprints
from src.appbuilder.blueprints import BLUEPRINTS
from src.appbuilder.checks import check_file
from src.core import request_context
from src.core.account_scope import AccountDirectory, UnknownAccount
from src.core.approvals import ApprovalGate, ProposalRefused
from src.core.aws_clients import boto3, get_base_client, use_account
from src.core.monitoring import metrics
from src.services.app_bundles import APP_ID_RE, AppBundleStore, BundleError

logger = structlog.get_logger()

MAX_REPAIRS = 2
MAX_CONTINUATIONS = 2
_FENCE = re.compile(r"```[^\n`]*\n(.*?)(?:\n```|\Z)", re.S)


# ── Model access ─────────────────────────────────────────────────────────────

class TextModel:
    """Plain text completions from Bedrock (Converse), with usage recorded for the request."""

    def __init__(self, model_id: str = None, region: str = None):
        self.model_id = model_id or config.BUILD_MODEL_ID or config.AGENT_MODEL_ID
        self._client = None
        self._region = region or config.AWS_REGION

    @property
    def client(self):
        if self._client is None:
            self._client = _boto3.client("bedrock-runtime", region_name=self._region,
                                         config=Config(read_timeout=300, retries={"max_attempts": 3}))
        return self._client

    def converse(self, system: str, messages: List[dict], max_tokens: int, temperature: float) -> Tuple[str, str]:
        response = self.client.converse(
            modelId=self.model_id, system=[{"text": system}], messages=messages,
            inferenceConfig={"maxTokens": max_tokens, "temperature": temperature})
        usage = response.get("usage") or {}
        request_context.record_usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0))
        try:
            metrics.put("ModelInputTokens", usage.get("inputTokens", 0), "Count", {"Model": self.model_id})
            metrics.put("ModelOutputTokens", usage.get("outputTokens", 0), "Count", {"Model": self.model_id})
        except Exception:  # noqa: S110 - telemetry is best-effort
            pass
        text = "".join(block.get("text", "") for block in response["output"]["message"]["content"])
        return text, response.get("stopReason", "")

    def complete(self, system: str, prompt: str, max_tokens: int = None, temperature: float = 0.2) -> str:
        """One answer, continued automatically if the model runs out of output tokens."""
        max_tokens = max_tokens or config.AGENT_MAX_TOKENS
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        text, stop = self.converse(system, messages, max_tokens, temperature)
        for _ in range(MAX_CONTINUATIONS):
            if stop != "max_tokens" or not text.strip():
                break
            messages = messages + [
                {"role": "assistant", "content": [{"text": text.rstrip()}]},
                {"role": "user", "content": [{"text": "You stopped mid-way. Continue from EXACTLY where you "
                                                      "stopped: do not repeat anything, do not restart the "
                                                      "file, do not add a new code fence or any comment."}]}]
            more, stop = self.converse(system, messages, max_tokens, temperature)
            text = text.rstrip() + _join(text, more)
        return text


def _join(before: str, after: str) -> str:
    """Continuation text, with a code fence the model re-opened removed."""
    after = re.sub(r"^\s*```[^\n`]*\n", "", after)
    # Continue on a new line unless the cut was clearly in the middle of a line.
    return after if after.startswith("\n") or not before.endswith("\n") else "\n" + after.lstrip("\n")


def extract_code(text: str) -> str:
    """The file inside the model's answer: the largest fenced block, else the whole answer."""
    blocks = [b for b in _FENCE.findall(text or "") if b.strip()]
    code = max(blocks, key=len) if blocks else (text or "")
    return code.strip("\n") + "\n" if code.strip() else ""


# ── Planning ─────────────────────────────────────────────────────────────────

PLAN_SYSTEM = """You plan small web applications that an AWS platform will build and deploy.
Reply with ONE JSON object and nothing else.

Blueprints (choose the simplest that fits):
{blueprints}

JSON fields:
  "action":    "build" (a new app), "modify" (change an existing app), "delete" (remove an existing app),
               "list" (the user asks which apps exist), "question" (you cannot tell at all what to build),
               or "other" (the request is not about building, changing or deleting an app).
  "app_id":    short lowercase name with hyphens, e.g. "snake-ladder". For modify/delete use the existing app's name.
  "title":     a short human title.
  "blueprint": one of the blueprint names above (for modify, keep the app's blueprint unless it now needs to save data).
  "schedule":  only for scheduled-job: "rate(1 hour)", "rate(1 day)" or a cron(...) expression.
  "summary":   two sentences for the user describing what will be built.
  "spec":      a precise specification for the programmer: features, rules, screens, and for web-app the API
               routes (method, path, request and response JSON). Be concrete and complete; keep the app small.
  "account":   the AWS account name the user asked to deploy to, or "" if they did not name one.
  "question":  only for action "question": the one thing you need to know.

Rules:
- Prefer "build" with sensible defaults over asking. Ask only if the request gives no idea what the app should do.
- Keep the scope small enough for one page of HTML and, for web-app, one short Python file.
- No user accounts, payments, email, file uploads or third-party services; leave those out and say so in the summary.

Existing apps:
{apps}"""


@dataclass
class Plan:
    action: str = "build"
    app_id: str = ""
    title: str = ""
    blueprint: str = "static-site"
    schedule: str = blueprints.DEFAULT_SCHEDULE
    summary: str = ""
    spec: str = ""
    account: str = ""
    question: str = ""


def _json_object(text: str) -> dict:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _slug(text: str) -> str:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    skip = {"a", "an", "the", "me", "my", "please", "build", "create", "make", "generate", "write", "simple",
            "app", "application", "for", "with", "that", "can", "you", "to", "of", "and", "website", "web"}
    kept = [w for w in words if w not in skip][:4] or words[:3] or ["app"]
    slug = "-".join(kept)[:40].strip("-")
    if not slug or not slug[0].isalpha():
        slug = "app-" + slug
    return (slug + "-app")[:41] if len(slug) < 3 else slug


def _guess_blueprint(request: str) -> str:
    low = request.lower()
    if re.search(r"\b(every (day|hour|week|minute|\d+)|daily|hourly|nightly|schedule|cron|periodically)\b", low):
        return "scheduled-job"
    if re.search(r"\b(save|store|database|persist|share|leaderboard|guestbook|todo|to-do|crud|api|endpoint|"
                 r"shorten|poll|vote|notes?|inventory|track)\b", low):
        return "web-app"
    return "static-site"


def make_plan(model: TextModel, request: str, conversation: str, apps: List[dict]) -> Plan:
    known = {a["app_id"]: a for a in apps}
    listing = "\n".join(f'- {a["app_id"]} ({a.get("blueprint", "custom")}): {a.get("description", "")[:160]}'
                        for a in apps) or "(none)"
    system = PLAN_SYSTEM.format(
        blueprints="\n".join(f'- "{b.name}": {b.summary}' for b in BLUEPRINTS.values()), apps=listing)
    prompt = (f"{conversation}\n\n" if conversation else "") + f"Request: {request}"
    try:
        data = _json_object(model.complete(system, prompt, max_tokens=1500, temperature=0.0))
    except Exception as e:
        logger.warning("build_plan_failed", error=str(e))
        data = {}

    plan = Plan()
    action = str(data.get("action", "")).lower()
    plan.action = action if action in ("build", "modify", "delete", "list", "question", "other") else "build"
    plan.title = str(data.get("title") or "").strip()[:80]
    plan.summary = str(data.get("summary") or "").strip()[:600]
    plan.spec = str(data.get("spec") or "").strip()[:6000] or request
    plan.account = str(data.get("account") or "").strip()
    plan.question = str(data.get("question") or "").strip()[:400]
    schedule = str(data.get("schedule") or "").strip()
    plan.schedule = schedule if blueprints.SCHEDULE_RE.match(schedule) else blueprints.DEFAULT_SCHEDULE
    blueprint = str(data.get("blueprint") or "")
    app_id = str(data.get("app_id") or "").strip().lower()

    if plan.action in ("modify", "delete"):
        if app_id not in known:
            # The model named an app that doesn't exist: one existing app is unambiguous, otherwise build new.
            if len(known) == 1 and plan.action == "modify":
                app_id = next(iter(known))
            elif plan.action == "delete":
                plan.action, plan.question = "question", (
                    "Which app should I delete? " + (", ".join(sorted(known)) or "There are no generated apps."))
            else:
                plan.action = "build"
        if plan.action == "modify":
            existing = str(known[app_id].get("blueprint") or "")
            if existing in BLUEPRINTS:
                # Keep the app's shape; the one allowed change is a page gaining a backend.
                grows = existing == "static-site" and blueprint == "web-app"
                blueprint = "web-app" if grows else existing
            plan.title = plan.title or str(known[app_id].get("title") or "")
    if plan.action == "build":
        if not APP_ID_RE.match(app_id):
            app_id = _slug(plan.title or request)
        base, n = app_id, 2
        while app_id in known:                 # a new app never overwrites an existing one
            app_id = f"{base[:38]}-{n}"
            n += 1
    plan.app_id = app_id
    plan.blueprint = blueprint if blueprint in BLUEPRINTS else _guess_blueprint(request)
    plan.title = plan.title or app_id.replace("-", " ").title()
    return plan


# ── Writing files ────────────────────────────────────────────────────────────

CODER_SYSTEM = """You are a careful programmer writing one file of a small application.
Reply with ONLY that file, complete, inside a single code block. No explanation before or after.
Write real, working code for every part. Never write placeholders, TODOs, "..." or "rest of the code".
Keep it as simple as the specification allows."""

_FILE_RULES = {
    "static/index.html": """Write the file static/index.html.
- One self-contained HTML5 document: all CSS inside <style>, all JavaScript inside <script>. No other files,
  no frameworks, no CDN links, no build step.
- It must work when opened in a modern browser, be responsive, and look clean and friendly.
- Handle the obvious edge cases (empty input, game over, restart).{backend}
Reply with the complete file in one ```html code block.""",
    "api.py": """Write the file api.py, the app's JSON API, in Python 3.12.
- Define exactly this top-level function, which the platform calls for every request whose path starts with /api/:
      def handle(method, path, query, body, store):
  method: "GET", "POST", "PUT" or "DELETE".  path: e.g. "/api/items/42".  query: dict of query-string values.
  body: the parsed JSON body (dict/list), or None.  Return (status_code, data); data is sent as JSON.
- Persist data ONLY with `store` (do not use files, globals or boto3 for storage):
      store.put(group, key, value)      # value is any JSON-serialisable dict/list/str/number
      store.get(group, key, default)    # -> value or default
      store.list(group)                 # -> [{"key": "...", "value": ...}, ...] ordered by key
      store.delete(group, key)
  group and key are strings, e.g. store.put("todos", item_id, {"title": "Buy milk", "done": False}).
- Only the Python standard library is available (uuid, time, json, re ...). No third-party packages.
- Validate input and return (400, {"error": "..."}) for bad requests and (404, {"error": "Not found"}) for
  unknown routes. Do not define a Lambda handler; the platform has one.
Example of the shape (not the content):
      import uuid
      def handle(method, path, query, body, store):
          if method == "GET" and path == "/api/items":
              return 200, [dict(i["value"], id=i["key"]) for i in store.list("items")]
          if method == "POST" and path == "/api/items":
              if not isinstance(body, dict) or not body.get("name"):
                  return 400, {"error": "name is required"}
              item_id = uuid.uuid4().hex[:8]
              store.put("items", item_id, {"name": body["name"]})
              return 201, {"id": item_id, "name": body["name"]}
          return 404, {"error": "Not found"}
Reply with the complete file in one ```python code block.""",
    "job.py": """Write the file job.py in Python 3.12. The platform runs it on a schedule.
- Define exactly this top-level function:
      def run(store):
  and return a short string or dict describing what it did (it is logged).
- Keep state between runs ONLY with `store`:
      store.put(group, key, value), store.get(group, key, default), store.list(group), store.delete(group, key)
- Only the Python standard library and boto3 are available. For HTTP calls use urllib.request with a timeout.
- Do not define a Lambda handler; the platform has one.
Reply with the complete file in one ```python code block.""",
}
_BACKEND_NOTE = {
    "static-site": "\n- There is NO server. All logic runs in the browser; use localStorage to remember things.",
    "web-app": "\n- Talk to the app's API with fetch() using relative URLs such as fetch('/api/items'). The API is "
               "in api.py, shown below; use exactly its routes and JSON shapes. Show a friendly message if a "
               "request fails.",
}


def _rules(path: str, blueprint: str) -> str:
    # Not str.format: the rules contain literal braces (JSON and code examples).
    return _FILE_RULES[path].replace("{backend}", _BACKEND_NOTE.get(blueprint, ""))


@dataclass
class Written:
    files: Dict[str, str] = field(default_factory=dict)
    repairs: int = 0
    unresolved: List[str] = field(default_factory=list)   # blocking problems that remain
    warnings: List[str] = field(default_factory=list)


def _file_prompt(path: str, plan: Plan, written: Dict[str, str], current: Optional[str], change: str) -> str:
    rules = _rules(path, plan.blueprint)
    parts = [f"Application: {plan.title}", f"Specification:\n{plan.spec}"]
    if path == "static/index.html" and "api.py" in written:
        parts.append("The app's API (api.py):\n```python\n" + written["api.py"] + "```")
    if current is not None:
        parts.append(f"Current version of {path}:\n```\n{current}```\n"
                     f"Change requested: {change}\n"
                     f"Apply the change and return the COMPLETE updated file. If this file needs no change, "
                     f"return it exactly as it is.")
    parts.append(rules)
    return "\n\n".join(parts)


def write_files(model: TextModel, plan: Plan, current: Optional[Dict[str, str]] = None, change: str = "") -> Written:
    """Write (or update) every file the blueprint needs, checking and repairing each one."""
    result = Written()
    for path in BLUEPRINTS[plan.blueprint].files:
        prompt = _file_prompt(path, plan, result.files, (current or {}).get(path), change)
        content = extract_code(model.complete(CODER_SYSTEM, prompt))
        problems = check_file(path, content, plan.blueprint)
        for _ in range(MAX_REPAIRS):
            if not problems:
                break
            result.repairs += 1
            logger.info("build_repair", file=path, problems=[m for _, m in problems])
            repair = ("This file has problems:\n" + "\n".join(f"- {m}" for _, m in problems)
                      + f"\n\nThe file ({path}):\n```\n{content}```\n\nSpecification:\n{plan.spec}\n\n"
                      + "Fix every problem and return the COMPLETE corrected file.\n\n"
                      + _rules(path, plan.blueprint))
            fixed = extract_code(model.complete(CODER_SYSTEM, repair))
            fixed_problems = check_file(path, fixed, plan.blueprint)
            # Keep the better attempt: fewer blocking problems wins.
            if _blocking(fixed_problems) <= _blocking(problems):
                content, problems = fixed, fixed_problems
        result.files[path] = content
        result.unresolved += [m for severity, m in problems if severity == "error"]
        result.warnings += [m for severity, m in problems if severity != "error"]
    return result


def _blocking(problems: list) -> int:
    return sum(1 for severity, _ in problems if severity == "error")


# ── Storing and queueing ─────────────────────────────────────────────────────

def _store() -> AppBundleStore:
    s3 = get_base_client("s3", region_name=config.AWS_REGION, config=Config(signature_version="s3v4"))
    return AppBundleStore(s3)


def _validate_in_account(template: str, account: Optional[str], region: str) -> str:
    """Ask CloudFormation in the target account whether the template is valid ('' = yes)."""
    try:
        with use_account(account, region):
            boto3.client("cloudformation", region_name=region).validate_template(TemplateBody=template)
        return ""
    except Exception as e:
        return str(e)


def uses_store(files: Dict[str, str]) -> bool:
    return any(re.search(r"\bstore\s*\.", files.get(p, "")) for p in ("api.py", "job.py"))


def _readme(plan: Plan, with_table: bool) -> str:
    bp = BLUEPRINTS[plan.blueprint]
    lines = [f"# {plan.title}", "", plan.summary or plan.spec[:400], "",
             f"Generated by CloudOps from the **{plan.blueprint}** blueprint.", "",
             "## What gets created", "",
             f"- {blueprints.what_it_creates(plan.blueprint, with_table).capitalize()}, named "
             f"`{blueprints.resource_name(plan.app_id)}`.",
             f"- It runs as the shared role `{blueprints.EXECUTION_ROLE_NAME}`, which can only write logs and "
             f"use DynamoDB tables named `{blueprints.RESOURCE_PREFIX}*`. No IAM resources are created."]
    if bp.has_url:
        lines.append("- The URL is **public**: anyone who has it can open the app. There is no sign-in.")
    else:
        lines.append(f"- Schedule: `{plan.schedule}`.")
    lines += ["", "## Files", "",
              "- `src/handler.py` is the platform's entry point (the same in every generated app)."]
    lines += [f"- `src/{p}` was written for this app." for p in bp.files]
    return "\n".join(lines) + "\n"


def _existing_sources(files: Dict[str, str]) -> Dict[str, str]:
    """{api.py: ..., static/index.html: ...} from a stored bundle's {src/...: ...}."""
    return {p[len("src/"):]: c for p, c in files.items() if p.startswith("src/") and p != "src/handler.py"}


# ── Entry point ──────────────────────────────────────────────────────────────

def run(request: str, conversation: str, gate: ApprovalGate, directory: Optional[AccountDirectory],
        region: Optional[str], model: TextModel = None,
        store_factory: Callable[[], AppBundleStore] = None) -> Optional[str]:
    """
    Handle a build request end to end and return the reply for the operator, or
    None if the request isn't one the builder handles (the caller then uses the
    normal agent).
    """
    model = model or TextModel()
    store = (store_factory or _store)()
    tenant, region = request_context.tenant_id(), region or config.AWS_REGION
    try:
        apps = store.list_apps(tenant)
    except Exception as e:
        logger.warning("build_list_apps_failed", error=str(e))
        apps = []

    plan = make_plan(model, request, conversation, apps)
    logger.info("build_plan", action=plan.action, app_id=plan.app_id, blueprint=plan.blueprint)
    if plan.action == "other":
        return None
    if plan.action == "question":
        return plan.question or "What should the app do? One or two sentences is enough."
    if plan.action == "list":
        if not apps:
            return "There are no generated apps yet. Describe one and I'll build it."
        return "Generated apps:\n" + "\n".join(
            f"- **{a['app_id']}** (version {a['version']}, {a.get('blueprint', 'custom')}): "
            f"{a.get('description', '')[:200]}" for a in apps)

    account_ref = plan.account or None
    if directory is not None:
        try:
            account_id = directory.resolve(account_ref)
        except UnknownAccount as e:
            return f"I can't build this yet: {e}"
        where = directory.label(account_id)
    else:
        account_id, where = None, "the platform account"

    if plan.action == "delete":
        try:
            action = gate.propose("delete_generated_app", {"app_id": plan.app_id, "region": region,
                                                           "account": account_ref})
        except ProposalRefused as e:
            return f"I couldn't queue the deletion: {e}"
        return (f"Deleting **{plan.app_id}** is queued for approval (action {action['action_id']}). Approving it "
                f"removes the app's function, its URL or schedule, and any table it created in {where}, "
                f"including the data in it. This cannot be undone.")

    # ── build or modify ──
    current, change = None, ""
    if plan.action == "modify":
        try:
            _, files, _ = store.load(tenant, plan.app_id)
            current, change = _existing_sources(files), request
        except Exception as e:
            return f"I couldn't read the current version of {plan.app_id}: {e}"

    try:
        written = write_files(model, plan, current, change)
    except Exception as e:
        logger.exception("build_write_failed")
        return (f"I couldn't write the app: {e}\n\nNothing was created. Try again, or describe a simpler "
                f"version.")
    if written.unresolved:
        return ("I wrote the app but it didn't pass my checks, so I have **not** offered it for deployment. "
                "Remaining problems:\n" + "\n".join(f"- {m}" for m in written.unresolved[:8])
                + "\n\nNothing was created. Try a simpler or more specific description (for example fewer "
                  "features), or ask again.")

    with_table = uses_store(written.files)
    template = blueprints.render_template(plan.blueprint, plan.app_id, plan.title, with_table, plan.schedule)
    bundle = {"src/handler.py": blueprints.RUNTIME_SOURCE, "README.md": _readme(plan, with_table),
              **{f"src/{p}": c for p, c in written.files.items()}}
    description = plan.summary or plan.spec[:400]
    try:
        manifest = store.save(tenant, plan.app_id, description, bundle, template, request_context.user_id(),
                              extra={"blueprint": plan.blueprint, "title": plan.title, "uses_store": with_table,
                                     "public_url": BLUEPRINTS[plan.blueprint].has_url})
    except BundleError as e:
        return f"The app didn't pass validation, so nothing was offered for deployment:\n{e}"
    except Exception as e:
        logger.exception("build_store_failed")
        return f"I wrote the app but couldn't save it for review: {e}. Nothing was created."

    template_problem = _validate_in_account(template, account_id, region)
    try:
        action = gate.propose("deploy_generated_app", {"app_id": plan.app_id, "version": manifest["version"],
                                                       "region": region, "account": account_ref})
    except ProposalRefused as e:
        return (f"I built **{plan.title}** (`{plan.app_id}`, version {manifest['version']}) but couldn't queue "
                f"its deployment: {e}")

    bp = BLUEPRINTS[plan.blueprint]
    verb = "updated" if plan.action == "modify" else "built"
    lines = [f"I {verb} **{plan.title}** (`{plan.app_id}`, version {manifest['version']}) and queued its "
             f"deployment for approval (action {action['action_id']}).", ""]
    if plan.summary:
        lines += [plan.summary, ""]
    lines += [f"**What approving it creates in {where} ({region}):** "
              f"{blueprints.what_it_creates(plan.blueprint, with_table)}. No IAM roles are created; it runs as "
              f"a shared role limited to logs and its own tables."]
    if bp.has_url:
        lines.append("**The URL will be public:** anyone who has it can open the app; there is no sign-in.")
    else:
        lines.append(f"**Schedule:** `{plan.schedule}`.")
    lines += ["**Cost:** pay-per-use, typically cents a month at low traffic.", "",
              "**Files** (all shown on the approval card): "
              + ", ".join(f"`src/{p}`" for p in bp.files) + ", plus the platform's `src/handler.py`.",
              "**Checks:** passed" + (f" after {written.repairs} automatic fix"
                                      f"{'es' if written.repairs != 1 else ''}" if written.repairs else "") + "."]
    if written.warnings:
        lines.append("**Worth a look when you review:** " + " ".join(written.warnings[:3]))
    if template_problem:
        lines.append(f"**Note:** CloudFormation in the target account could not pre-validate the template "
                     f"({template_problem[:200]}).")
    lines += ["", "After you approve, I deploy it" + (", open its URL to check it responds, and give you the link."
                                                       if bp.has_url else " and it starts running on its schedule.")
              + " To change it afterwards, just tell me what to change."]
    return "\n".join(lines)
