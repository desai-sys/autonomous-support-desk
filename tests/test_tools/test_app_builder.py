"""App builder: blueprints, the shipped runtime, static checks, the build pipeline, and deployment."""
import importlib
import io
import itertools
import json
import sys
import zipfile

import boto3
import pytest

import config
from src.appbuilder import blueprints, pipeline
from src.appbuilder.blueprints import BLUEPRINTS, render_template
from src.appbuilder.checks import check_file, javascript_issue
from src.core import request_context
from src.core.account_scope import AccountDirectory
from src.core.approvals import ActionStore, ApprovalGate, is_mutating
from src.services.app_bundles import AppBundleStore, BundleError, code_zip, validate
from src.tools import app_builder_tools as abt

PAGE = """<!doctype html>
<html><head><title>Game</title><style>body { font-family: sans-serif; }</style></head>
<body><h1>Snake and Ladder</h1><button id="roll">Roll</button><p id="out"></p>
<script>
const snakes = {16: 6, 47: 26}; let pos = 0;
document.getElementById('roll').onclick = () => {
  const d = Math.floor(Math.random() * 6) + 1; pos = snakes[pos + d] || pos + d;
  document.getElementById('out').textContent = `Rolled ${d}, now on ${pos}`;
};
</script></body></html>
"""
API = '''import uuid


def handle(method, path, query, body, store):
    if method == "GET" and path == "/api/todos":
        return 200, [dict(i["value"], id=i["key"]) for i in store.list("todos")]
    if method == "POST" and path == "/api/todos":
        if not isinstance(body, dict) or not body.get("title"):
            return 400, {"error": "title is required"}
        todo_id = uuid.uuid4().hex[:8]
        store.put("todos", todo_id, {"title": body["title"], "done": False})
        return 201, {"id": todo_id}
    if method == "GET" and path == "/api/boom":
        raise RuntimeError("kaboom")
    if method == "GET" and path == "/api/plain":
        return {"ok": True}
    return 404, {"error": "Not found"}
'''
APP_PAGE = PAGE.replace("</script>", "fetch('/api/todos').then(r => r.json());\n</script>")
JOB = "def run(store):\n    n = store.get('state', 'runs', 0) + 1\n    store.put('state', 'runs', n)\n    return f'run {n}'\n"


# ── Blueprints ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,with_store", list(itertools.product(BLUEPRINTS, (False, True))))
def test_blueprint_templates_are_valid_and_contain_no_iam(name, with_store):
    import cfnlint.api
    template = render_template(name, "snake-ladder", 'Snake & "Ladder"!', with_store, "rate(1 hour)")
    assert [f"{m.rule.id} {m.message}" for m in cfnlint.api.lint(template)] == []
    assert "AWS::IAM::" not in template and f"role/{blueprints.EXECUTION_ROLE_NAME}" in template
    assert ("AWS::DynamoDB::Table" in template) is with_store
    assert ("AWS::Lambda::Url" in template) is BLUEPRINTS[name].has_url
    files = {"src/handler.py": blueprints.RUNTIME_SOURCE, "README.md": "x"}
    assert validate("snake-ladder", files, template) == []          # passes the platform's own bundle rules


def test_bad_schedules_fall_back_to_the_default():
    assert 'ScheduleExpression: "rate(1 day)"' in render_template("scheduled-job", "job-app", "J", False, "whenever")
    assert 'ScheduleExpression: "cron(0 8 * * ? *)"' in render_template(
        "scheduled-job", "job-app", "J", False, "cron(0 8 * * ? *)")


# ── The runtime shipped inside every app ─────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    """A generated app laid out as it is inside the Lambda package."""
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text(PAGE)
    (tmp_path / "static" / "logo.png").write_bytes(b"\x89PNG\x00")
    (tmp_path / "handler.py").write_text(blueprints.RUNTIME_SOURCE)
    (tmp_path / "api.py").write_text(API)
    (tmp_path / "job.py").write_text(JOB)
    (tmp_path / "secret.txt").write_text("not served")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delenv("TABLE_NAME", raising=False)
    for name in ("handler", "api", "job"):
        sys.modules.pop(name, None)
    module = importlib.import_module("handler")
    yield module
    for name in ("handler", "api", "job"):
        sys.modules.pop(name, None)


def http(method, path, body=None, query=None):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path,
            "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}


def test_runtime_serves_the_page_and_nothing_outside_static(app):
    home = app.handler(http("GET", "/"), None)
    assert home["statusCode"] == 200 and "Snake and Ladder" in home["body"]
    assert home["headers"]["Content-Type"].startswith("text/html")
    image = app.handler(http("GET", "/logo.png"), None)
    assert image["isBase64Encoded"] and image["headers"]["Content-Type"] == "image/png"
    for path in ("/../secret.txt", "/../api.py", "/../../etc/passwd", "/missing.html"):
        assert app.handler(http("GET", path), None)["statusCode"] == 404
    assert app.handler(http("POST", "/"), None)["statusCode"] == 405


def test_runtime_dispatches_the_api_and_reports_its_errors(app):
    assert json.loads(app.handler(http("GET", "/api/todos"), None)["body"]) == []
    created = app.handler(http("POST", "/api/todos", {"title": "Buy milk"}), None)
    assert created["statusCode"] == 201
    assert app.handler(http("POST", "/api/todos", {}), None)["statusCode"] == 400
    todos = json.loads(app.handler(http("GET", "/api/todos"), None)["body"])
    assert todos == [{"title": "Buy milk", "done": False, "id": json.loads(created["body"])["id"]}]
    assert json.loads(app.handler(http("GET", "/api/plain"), None)["body"]) == {"ok": True}   # no status given
    boom = app.handler(http("GET", "/api/boom"), None)
    assert boom["statusCode"] == 500 and json.loads(boom["body"]) == {"error": "kaboom", "type": "RuntimeError"}
    assert app.handler(http("GET", "/api/nope"), None)["statusCode"] == 404


def test_runtime_runs_scheduled_jobs(app):
    assert app.handler({"source": "aws.events"}, None) == {"status": "ok", "result": "run 1"}
    assert app.handler({"source": "aws.events"}, None)["result"] == "run 2"


def test_store_persists_in_dynamodb(app, aws, monkeypatch):
    boto3.client("dynamodb", region_name="us-east-1").create_table(
        TableName="cloudops-gen-todo", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}])
    store = app.Store(boto3.resource("dynamodb", region_name="us-east-1").Table("cloudops-gen-todo"))
    store.put("scores", "b", {"points": 2.5, "tags": ["x"]})
    store.put("scores", "a", {"points": 1})
    store.put("other", "a", "text")
    assert store.get("scores", "b") == {"points": 2.5, "tags": ["x"]}           # floats survive
    assert [i["key"] for i in store.list("scores")] == ["a", "b"]
    store.delete("scores", "a")
    store.delete("scores", "never-there")
    assert store.get("scores", "a", "gone") == "gone" and store.get("other", "a") == "text"


# ── Static checks ────────────────────────────────────────────────────────────

def errors(path, content, blueprint="web-app"):
    return [m for severity, m in check_file(path, content, blueprint) if severity == "error"]


def test_good_files_pass():
    assert check_file("static/index.html", PAGE, "static-site") == []
    assert check_file("static/index.html", APP_PAGE, "web-app") == []
    assert check_file("api.py", API, "web-app") == []
    assert check_file("job.py", JOB, "scheduled-job") == []


@pytest.mark.parametrize("path,content,blueprint,expected", [
    ("api.py", "def handle(method, path, query, body, store)\n    return 200, {}\n", "web-app", "syntax error"),
    ("api.py", "import requests\n" + API, "web-app", "'requests', which is not installed"),
    ("api.py", "from flask import Flask\n" + API, "web-app", "'flask'"),
    ("api.py", "def handler(event, context):\n    return {}\n", "web-app", "must define a top-level function"),
    ("api.py", "def handle(event, context):\n    return 200, {}\n", "web-app", "exactly 5 parameters"),
    ("api.py", API + "\n# ... rest of the code\n", "web-app", "placeholder"),
    ("api.py", "from . import helpers\n" + API, "web-app", "relative import"),
    ("job.py", "def run():\n    return 1\n", "scheduled-job", "exactly 1 parameter"),
    ("static/index.html", "", "static-site", "is empty"),
    ("static/index.html", PAGE[:PAGE.index("</script>")], "static-site", "cut off"),
    ("static/index.html", PAGE.replace("<body>", "<body><script src=\"game.js\"></script>"), "static-site",
     "separate file"),
    ("static/index.html", PAGE.replace("<style>", "<link rel=\"stylesheet\" href=\"style.css\"><style>"),
     "static-site", "separate file"),
    ("static/index.html", APP_PAGE, "static-site", "no server-side code"),
    ("static/index.html", PAGE.replace("let pos = 0;", "let pos = 0; // TODO: implement here"), "static-site",
     "placeholder"),
    ("static/index.html", "<p>hello</p>", "static-site", "complete HTML document"),
])
def test_problems_are_caught(path, content, blueprint, expected):
    assert any(expected in m for m in errors(path, content, blueprint)), errors(path, content, blueprint)


def test_malformed_javascript_is_flagged_without_blocking():
    broken = PAGE.replace("pos = snakes[pos + d] || pos + d;", "pos = snakes[pos + d || pos + d;")
    problems = check_file("static/index.html", broken, "static-site")
    assert [severity for severity, _ in problems] == ["soft"] and "JavaScript" in problems[0][1]


def test_javascript_scanner_accepts_valid_code_and_finds_breaks():
    valid = r"""
    const t = 'a\'b(', s = "it's {fine}"; // ( [ {
    /* { [ ( */ const msg = `P ${players[turn].name} rolled ${roll() + (b ? 1 : 0)} \`q\` ${ `nested ${x}` }`;
    const re = /[/\]{(]+\/"'/g, half = total / 2 / count;
    if (/^\d{3}$/.test(v) && a/b > c/d) { arr.map(x => ({k: x})).filter(x => x.k !== '}'); }
    const o = {a: 1, 'b}': [1, 2, {c: `}`}]}; x = (y) / 2; w = arr[0] / 3;
    """
    assert javascript_issue(valid) == ""
    assert "never closed" in javascript_issue("function a(){ if (x) { return 1; }")
    assert "not closed before the end of the line" in javascript_issue("const s = 'abc;\nlet y = 1;")
    assert "unexpected ')'" in javascript_issue("f(1, 2));")
    assert "never closed" in javascript_issue("x = `abc ${y")


# ── The pipeline, with a scripted model ──────────────────────────────────────

class ScriptedModel(pipeline.TextModel):
    """Answers by the kind of request: 'plan', or the file being written/repaired."""

    def __init__(self, plan, files, truncate_first=False):
        self.plan, self.files, self.calls, self.truncate_first = plan, files, [], truncate_first
        self.model_id = "scripted"

    def converse(self, system, messages, max_tokens, temperature):
        prompt = messages[0]["content"][0]["text"]
        if "You plan small web applications" in system:
            self.calls.append(("plan", prompt + "\n" + system))
            return (self.plan if isinstance(self.plan, str) else json.dumps(self.plan)), "end_turn"
        path = next(p for p in ("api.py", "job.py", "static/index.html") if f"Write the file {p}" in prompt)
        kind = "continue" if len(messages) > 1 else "repair" if prompt.startswith("This file has problems") else "write"
        self.calls.append((kind, path, prompt))
        answers = self.files[path]
        answers = answers if isinstance(answers, list) else [answers]
        attempt = sum(1 for c in self.calls if c[0] in ("write", "repair") and c[1] == path) - 1
        text = answers[min(attempt, len(answers) - 1)]
        if self.truncate_first and kind == "write":
            return "```html\n" + text[:len(text) // 2], "max_tokens"
        if kind == "continue":
            return text[len(text) // 2:] + "\n```", "end_turn"
        return f"Here is the file:\n```python\n{text}```\nDone.", "end_turn"


@pytest.fixture
def env(tables, monkeypatch):
    monkeypatch.setattr(config, "ARTIFACT_BUCKET", "cloudops-artifacts-test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cloudops-artifacts-test")
    gate = ApprovalGate(ActionStore(), tenant_id="t1", session_id="s1", requested_by="alice",
                        account_id=None, region="us-east-1",
                        directory=AccountDirectory("999999999999", {"111122223333": "ta-aws-1"}))
    store = AppBundleStore(boto3.client("s3", region_name="us-east-1"))
    with request_context.bind("t1", "alice", "s1"):
        yield gate, store


def build(env, model, request="build me a snake and ladder game", conversation=""):
    gate, store = env
    return pipeline.run(request, conversation, gate, gate.directory, "us-east-1", model=model,
                        store_factory=lambda: store)


GAME_PLAN = {"action": "build", "app_id": "snake-ladder", "title": "Snake and Ladder", "blueprint": "static-site",
             "summary": "A two-player snake and ladder game.", "spec": "10x10 board, dice, snakes, ladders."}
TODO_PLAN = {"action": "build", "app_id": "todo-list", "title": "Todo List", "blueprint": "web-app",
             "summary": "A shared todo list.", "spec": "GET/POST /api/todos"}


def test_builds_a_page_without_any_tool_call_and_only_queues_the_deployment(env):
    gate, store = env
    reply = build(env, ScriptedModel(GAME_PLAN, {"static/index.html": PAGE}))
    [action] = gate.queued
    assert action["tool_name"] == "deploy_generated_app" and action["status"] == "pending"
    assert action["tool_input"] == {"app_id": "snake-ladder", "version": 1, "region": "us-east-1"}
    manifest, files, template = store.load("t1", "snake-ladder")
    assert manifest["blueprint"] == "static-site" and manifest["public_url"] and not manifest["uses_store"]
    assert files["src/static/index.html"] == PAGE and files["src/handler.py"] == blueprints.RUNTIME_SOURCE
    assert "AWS::DynamoDB::Table" not in template and "AWS::IAM::" not in template
    assert sorted(zipfile.ZipFile(io.BytesIO(store.load_code("t1", "snake-ladder", 1))).namelist()) == [
        "handler.py", "static/index.html"]
    assert "queued its deployment for approval" in reply and "public" in reply and "Checks:** passed." in reply
    # Nothing was deployed: no stack, no function.
    assert boto3.client("cloudformation", region_name="us-east-1").describe_stacks()["Stacks"] == []


def test_web_app_gets_a_table_because_its_api_stores_data(env):
    gate, store = env
    model = ScriptedModel(TODO_PLAN, {"api.py": API, "static/index.html": APP_PAGE})
    reply = build(env, model, "build a todo list that saves items")
    manifest, files, template = store.load("t1", "todo-list")
    assert manifest["uses_store"] and "TableName: cloudops-gen-todo-list" in template
    assert set(files) == {"README.md", "src/api.py", "src/handler.py", "src/static/index.html"}
    page_prompt = next(c[2] for c in model.calls if c[0] == "write" and c[1] == "static/index.html")
    assert "def handle(method, path, query, body, store)" in page_prompt       # the page is written against the API
    assert [c[1] for c in model.calls if c[0] == "write"] == ["api.py", "static/index.html"]
    assert "DynamoDB table" in reply


def test_problems_are_sent_back_and_fixed(env):
    gate, store = env
    broken = API.replace("def handle(method, path, query, body, store):", "def handle(method, path, query, body, store)")
    model = ScriptedModel(TODO_PLAN, {"api.py": [broken, API], "static/index.html": APP_PAGE})
    reply = build(env, model)
    repair = next(c[2] for c in model.calls if c[0] == "repair")
    assert "syntax error" in repair and "COMPLETE corrected file" in repair
    assert store.load("t1", "todo-list")[1]["src/api.py"] == API
    assert "after 1 automatic fix." in reply and len(gate.queued) == 1


def test_an_app_that_cannot_be_fixed_is_never_offered(env):
    gate, store = env
    cut_off = PAGE[:PAGE.index("</script>")]
    reply = build(env, ScriptedModel(GAME_PLAN, {"static/index.html": cut_off}))
    assert gate.queued == [] and "not** offered it for deployment" in reply and "cut off" in reply
    with pytest.raises(BundleError):
        store.load("t1", "snake-ladder")                       # nothing stored either


def test_a_soft_javascript_warning_does_not_block(env):
    gate, _ = env
    odd = PAGE.replace("pos = snakes[pos + d] || pos + d;", "pos = snakes[pos + d || pos + d;")
    reply = build(env, ScriptedModel(GAME_PLAN, {"static/index.html": odd}))
    assert len(gate.queued) == 1 and "Worth a look when you review" in reply


def test_output_cut_off_by_the_token_limit_is_continued(env):
    gate, store = env
    model = ScriptedModel(GAME_PLAN, {"static/index.html": PAGE}, truncate_first=True)
    build(env, model)
    assert [c[0] for c in model.calls] == ["plan", "write", "continue"]
    assert store.load("t1", "snake-ladder")[1]["src/static/index.html"].split() == PAGE.split()
    assert len(gate.queued) == 1


def test_changing_an_app_makes_a_new_version_from_the_current_files(env):
    gate, store = env
    build(env, ScriptedModel(GAME_PLAN, {"static/index.html": PAGE}))
    changed = PAGE.replace("Snake and Ladder", "Snake and Ladder for 3")
    model = ScriptedModel({"action": "modify", "app_id": "snake-ladder", "blueprint": "static-site",
                           "spec": "three players"}, {"static/index.html": changed})
    reply = build(env, model, "make it three players")
    prompt = next(c[2] for c in model.calls if c[0] == "write")
    assert "Current version of static/index.html" in prompt and "Change requested: make it three players" in prompt
    assert store.load("t1", "snake-ladder")[0]["version"] == 2
    assert store.load("t1", "snake-ladder", 1)[1]["src/static/index.html"] == PAGE     # v1 untouched
    assert gate.queued[-1]["tool_input"]["version"] == 2 and "I updated" in reply
    assert "snake-ladder (static-site)" in next(c[1] for c in model.calls if c[0] == "plan")


def test_a_new_app_never_overwrites_an_existing_one(env):
    gate, store = env
    build(env, ScriptedModel(GAME_PLAN, {"static/index.html": PAGE}))
    build(env, ScriptedModel(GAME_PLAN, {"static/index.html": PAGE}))
    assert [a["tool_input"]["app_id"] for a in gate.queued] == ["snake-ladder", "snake-ladder-2"]


def test_delete_list_question_and_other(env):
    gate, store = env
    assert "no generated apps" in build(env, ScriptedModel({"action": "list"}, {}))
    build(env, ScriptedModel(GAME_PLAN, {"static/index.html": PAGE}))
    assert "**snake-ladder** (version 1, static-site)" in build(env, ScriptedModel({"action": "list"}, {}))
    assert build(env, ScriptedModel({"action": "question", "question": "What should it do?"}, {})) == "What should it do?"
    assert build(env, ScriptedModel({"action": "other"}, {})) is None            # left to the normal agent
    reply = build(env, ScriptedModel({"action": "delete", "app_id": "snake-ladder"}, {}))
    assert gate.queued[-1]["tool_name"] == "delete_generated_app" and "cannot be undone" in reply
    assert "Which app" in build(env, ScriptedModel({"action": "delete", "app_id": "nope"}, {}))


def test_an_unusable_plan_still_builds_with_sensible_defaults(env):
    gate, store = env
    model = ScriptedModel("Sure! I'd love to help you build that.", {"api.py": API, "static/index.html": APP_PAGE})
    build(env, model, "Create a guestbook where visitors can save messages")
    [action] = gate.queued
    assert action["tool_input"]["app_id"] == "guestbook-where-visitors-save"
    assert store.load("t1", action["tool_input"]["app_id"])[0]["blueprint"] == "web-app"


def test_named_account_is_where_it_deploys_and_unknown_accounts_stop_it(env):
    gate, _ = env
    build(env, ScriptedModel({**GAME_PLAN, "account": "ta-aws-1"}, {"static/index.html": PAGE}))
    assert gate.queued[0]["account_id"] == "111122223333" and "account" not in gate.queued[0]["tool_input"]
    reply = build(env, ScriptedModel({**GAME_PLAN, "app_id": "other-game", "account": "nope"}, {"static/index.html": PAGE}))
    assert "Unknown account" in reply and len(gate.queued) == 1


def test_extract_code_handles_what_models_actually_send():
    assert pipeline.extract_code("```html\n<p>x</p>\n```") == "<p>x</p>\n"
    assert pipeline.extract_code("Here you go:\n```python\nx = 1\n```\nand a note ```y```") == "x = 1\n"
    assert pipeline.extract_code("```html\n<p>cut off") == "<p>cut off\n"           # fence never closed
    assert pipeline.extract_code("def run(store):\n    return 1") == "def run(store):\n    return 1\n"
    assert pipeline.extract_code("   ") == ""


# ── Storage rules, deployment and the entry point ────────────────────────────

FILES = {"src/handler.py": "def handler(event, context):\n    return {'statusCode': 200}\n", "README.md": "# Demo\n"}
TEMPLATE = render_template("scheduled-job", "demo-app", "Demo", True)


class TestBundles:
    @pytest.mark.parametrize("files,template,msg", [
        (FILES, TEMPLATE.replace("AWS::DynamoDB::Table", "AWS::IAM::User"), "not allowed"),
        (FILES, TEMPLATE + "\n# arn:aws:iam::aws:policy/AdministratorAccess\n", "administrator"),
        (FILES, TEMPLATE.replace("Action: lambda:InvokeFunction", 'Action: "*"'), 'Action "*"'),
        (FILES, TEMPLATE.replace("ArtifactKey: {Type: String}", ""), "ArtifactBucket and ArtifactKey"),
        ({"../evil.py": "x", **FILES}, TEMPLATE, "Unsafe file path"),
        (FILES, "not: [valid", "not valid YAML"),
    ])
    def test_rejections(self, files, template, msg):
        with pytest.raises(BundleError, match=msg):
            validate("demo-app", files, template)

    def test_bad_name(self):
        with pytest.raises(BundleError, match="app_id"):
            validate("Demo App", FILES, TEMPLATE)

    def test_code_zip_strips_src(self):
        assert zipfile.ZipFile(io.BytesIO(code_zip(FILES))).namelist() == ["handler.py"]

    def test_versions_are_immutable_and_tenants_are_separate(self, env):
        _, store = env
        assert store.save("t1", "demo-app", "v1", FILES, TEMPLATE, "alice", extra={"blueprint": "scheduled-job"})["version"] == 1
        assert store.save("t1", "demo-app", "v2", {**FILES, "README.md": "# v2\n"}, TEMPLATE, "alice")["version"] == 2
        assert store.load("t1", "demo-app", 1)[1] == FILES and store.load("t1", "demo-app")[0]["version"] == 2
        assert [a["app_id"] for a in store.list_apps("t1")] == ["demo-app"] and store.list_apps("t2") == []
        with pytest.raises(BundleError):
            store.load("t2", "demo-app")


def test_deploy_needs_the_shared_role_then_creates_and_deletes_the_stack(env, monkeypatch):
    gate, store = env
    monkeypatch.setattr(abt.time, "sleep", lambda s: None)
    build(env, ScriptedModel({"action": "build", "app_id": "nightly", "title": "Nightly", "blueprint": "scheduled-job",
                              "schedule": "rate(1 day)", "spec": "count runs"}, {"job.py": JOB}))
    missing = json.loads(abt.deploy_generated_app(app_id="nightly", version=1))
    assert missing["error_type"] == "missing_execution_role" and "Accounts page" in missing["error_message"]

    boto3.client("iam").create_role(RoleName=blueprints.EXECUTION_ROLE_NAME, AssumeRolePolicyDocument=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                "Action": "sts:AssumeRole"}]}))
    out = json.loads(abt.deploy_generated_app(app_id="nightly", version=1))
    assert out["status"] == "success", out
    assert out["details"]["stack_name"] == "cloudops-gen-nightly" and out["details"]["action"] == "created"
    function = boto3.client("lambda", region_name="us-east-1").get_function(FunctionName="cloudops-gen-nightly")
    assert function["Configuration"]["Handler"] == "handler.handler"
    assert function["Configuration"]["Role"].endswith(f"role/{blueprints.EXECUTION_ROLE_NAME}")
    assert boto3.client("dynamodb", region_name="us-east-1").describe_table(TableName="cloudops-gen-nightly")
    assert json.loads(abt.delete_generated_app(app_id="nightly"))["status"] == "success"


def test_smoke_test_reports_url_outputs(monkeypatch):
    class Resp:
        status = 200
        def read(self, n): return b"hello"
        def __enter__(self): return self
        def __exit__(self, *a): return False
    monkeypatch.setattr(abt.urllib.request, "urlopen", lambda req, timeout: Resp())
    res = abt._smoke_test({"AppUrl": "https://x.example.com/", "TableName": "t"})
    assert res == [{"output": "AppUrl", "url": "https://x.example.com/", "status": 200, "body": "hello"}]


def test_only_deploying_and_deleting_need_approval():
    import src.agentcore_entrypoint as ep
    assert is_mutating("deploy_generated_app") and is_mutating("delete_generated_app")
    assert not is_mutating("get_generated_app")
    names = {t.tool_name for t in ep.DOMAIN_TOOLS["build"]}
    assert {"deploy_generated_app", "delete_generated_app", "get_generated_app"} <= names
    assert not any(n.startswith("prepare_app") for n in ep.TOOL_REGISTRY)      # code never travels in tool calls


def test_build_requests_use_the_pipeline_and_viewers_cannot_build(tables, monkeypatch):
    import src.agentcore_entrypoint as ep
    monkeypatch.setattr(ep, "action_store", ActionStore())
    monkeypatch.setattr(ep, "base_account_id", lambda: "999999999999")
    monkeypatch.setattr(ep.router, "route", lambda *a, **k: ["build"])
    seen = []
    monkeypatch.setattr(ep.app_builder, "run",
                        lambda request, conversation, gate, directory, region: seen.append(request) or "Built it.")
    monkeypatch.setattr(ep, "_run_agent_with_domains", lambda *a, **k: ("agent answered", ["build"], 3))
    base = {"mode": "chat", "prompt": "build a game", "user_message": "build a game", "session_id": "s1",
            "tenant_id": "t1"}
    assert ep.invoke({**base, "user": {"id": "alice", "role": "operator"}})["response"] == "Built it."
    assert ep.invoke({**base, "user": {"id": "v", "role": "viewer"}})["response"] == "agent answered"
    assert seen == ["build a game"]
    # A request the builder doesn't handle falls through to the normal agent.
    monkeypatch.setattr(ep.app_builder, "run", lambda *a, **k: None)
    assert ep.invoke({**base, "user": {"id": "alice", "role": "operator"}})["response"] == "agent answered"
