"""Console API (FastAPI) with development sign-in, moto tables and a stubbed agent."""
import io
import time
import zipfile

import boto3
import pytest
from fastapi.testclient import TestClient

import config
from src.agents.gateway import ChatResult

H = {"X-CloudOps": "1"}


@pytest.fixture
def client(tables, monkeypatch):
    from console import api, server
    for cached in (api.gateway, api.jobs, api.tickets, api.artifacts, api.registry, api.bundles):
        cached.cache_clear()
    monkeypatch.setattr(config, "AUTH_MODE", "local")
    calls = {"chat": [], "approve": []}

    def chat(self, text, user, session_id, **kw):
        calls["chat"].append({"text": text, "user": user.user_id, "session": session_id, **kw})
        escalate = "unknown" in text
        return ChatResult(text="ESCALATE - no tool" if escalate else "Done.", resolution_type="action",
                          escalate=escalate, pending_actions=[], metadata={"domain": "compute"})

    def approve(self, action_ids, user, session_id):
        calls["approve"].append(action_ids)
        return {"response": "✅ ok", "results": [{"action_id": action_ids[0], "status": "succeeded"}]}

    monkeypatch.setattr("src.agents.gateway.AgentGateway.chat", chat)
    monkeypatch.setattr("src.agents.gateway.AgentGateway.approve", approve)
    c = TestClient(server.app)
    c.calls = calls
    return c


def login(client, role="admin"):
    assert client.post("/auth/dev-login", json={"email": f"{role}@example.com", "tenant": "t1", "role": role},
                       headers=H).status_code == 200


def wait(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_requires_sign_in_and_csrf_header(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/me").status_code == 401
    assert client.post("/auth/dev-login", json={"email": "a@b.c"}).status_code == 403   # no header
    login(client)
    assert client.post("/api/chat", json={}).status_code == 403                       # no header
    me = client.get("/api/me").json()
    assert me["role"] == "admin" and me["tenant"] == "t1" and "approve_changes" in me["permissions"]


def test_dev_login_is_disabled_in_oidc_mode(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    assert client.post("/auth/dev-login", json={"email": "a@b.c"}, headers=H).status_code == 404


def test_chat_runs_as_a_job_and_escalations_open_tickets(client):
    login(client, "operator")
    body = {"conversation_id": "conv-12345678", "message": "stop i-1", "accounts": ["111122223333"],
            "region": "eu-west-1", "history": ["User: hi"]}
    job = wait(client, client.post("/api/chat", json=body, headers=H).json()["job_id"])
    assert job["status"] == "succeeded" and job["result"]["text"] == "Done."
    sent = client.calls["chat"][0]
    assert sent["session"] == "conv-12345678" and sent["accounts"] == ["111122223333"] and sent["region"] == "eu-west-1"

    job = wait(client, client.post("/api/chat", json={**body, "message": "something unknown"}, headers=H).json()["job_id"])
    ticket_id = job["result"]["ticket_id"]
    assert [t["ticket_id"] for t in client.get("/api/tickets").json()] == [ticket_id]


def test_jobs_are_private_to_their_owner(client):
    login(client, "operator")
    job_id = client.post("/api/chat", json={"conversation_id": "conv-12345678", "message": "hi"}, headers=H).json()["job_id"]
    wait(client, job_id)
    login(client, "admin")
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def _queue(session="conv-12345678", requested_by="someone"):
    from src.core.approvals import ActionStore
    return ActionStore().create(tenant_id="t1", session_id=session, requested_by=requested_by, account_id="",
                                region="us-east-1", tool_name="stop_ec2_instance", tool_input={"instance_id": "i-1"})


def test_pending_approve_and_reject(client):
    login(client, "sre")
    a, b = _queue(), _queue()
    pending = client.get("/api/conversations/conv-12345678/actions").json()
    assert {p["action_id"] for p in pending} == {a["action_id"], b["action_id"]}
    job = wait(client, client.post(f"/api/actions/{a['action_id']}/approve",
                                   json={"conversation_id": "conv-12345678"}, headers=H).json()["job_id"])
    assert job["result"]["results"][0]["status"] == "succeeded" and client.calls["approve"] == [[a["action_id"]]]
    assert client.post(f"/api/actions/{b['action_id']}/reject", headers=H).json() == {"ok": True}
    assert client.post(f"/api/actions/{b['action_id']}/reject", headers=H).status_code == 409


def test_viewer_permissions(client):
    login(client, "viewer")
    action = _queue()
    assert client.post(f"/api/actions/{action['action_id']}/approve", json={"conversation_id": "x"}, headers=H).status_code == 403
    assert client.post(f"/api/actions/{action['action_id']}/reject", headers=H).status_code == 403
    assert client.post("/api/uploads", files={"file": ("a.zip", b"x")}, headers=H).status_code == 403
    assert client.post("/api/accounts", json={"account_id": "111122223333"}, headers=H).status_code == 403
    assert client.get("/api/audit").status_code == 403
    assert client.post("/api/chat", json={"conversation_id": "conv-12345678", "message": "hi"}, headers=H).status_code == 200


def test_accounts_admin(client):
    login(client, "operator")
    assert client.post("/api/accounts", json={"account_id": "111122223333"}, headers=H).status_code == 403
    login(client, "admin")
    assert client.post("/api/accounts", json={"account_id": "1111-2222-3333", "name": "prod"}, headers=H).json()["account_id"] == "111122223333"
    assert client.post("/api/accounts", json={"account_id": "123"}, headers=H).status_code == 400
    assert [a["name"] for a in client.get("/api/accounts").json()] == ["prod"]
    assert "ExternalId=" in client.get("/api/accounts/setup").json()["command"]
    client.delete("/api/accounts/111122223333", headers=H)
    assert client.get("/api/accounts").json() == []


def test_upload_zip(client, monkeypatch):
    monkeypatch.setattr(config, "ARTIFACT_BUCKET", "cloudops-artifacts-test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cloudops-artifacts-test")
    login(client, "operator")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("app.py", "def lambda_handler(e, c): return 1")
    res = client.post("/api/uploads", files={"file": ("app.zip", buf.getvalue(), "application/zip")},
                      data={"target": "lambda", "options": '{"function_name": "f", "handler": ""}'}, headers=H)
    assert res.status_code == 200, res.text
    ref = res.json()
    assert ref["uri"].startswith("s3://cloudops-artifacts-test/uploads/t1/") and ref["options"] == {"function_name": "f"}
    bad = client.post("/api/uploads", files={"file": ("x.zip", b"nope")}, data={"target": "lambda"}, headers=H)
    assert bad.status_code == 400


def test_tickets_audit_and_app_bundles(client, monkeypatch):
    from src.services.app_bundles import AppBundleStore
    from src.services.ticket_service import TicketService
    monkeypatch.setattr(config, "ARTIFACT_BUCKET", "cloudops-artifacts-test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cloudops-artifacts-test")
    login(client, "sre")
    ticket = TicketService().create(tenant_id="t1", query_text="disk full", category="X")
    assert client.post(f"/api/tickets/{ticket['ticket_id']}/close", json={"resolution": "grew it"}, headers=H).json() == {"ok": True}
    assert client.get("/api/tickets").json() == []
    assert any(e["action"] == "ticket_closed" for e in client.get("/api/audit").json())

    template = "Parameters: {ArtifactBucket: {Type: String}, ArtifactKey: {Type: String}}\nResources: {T: {Type: AWS::SNS::Topic}}\n"
    AppBundleStore(boto3.client("s3"), "cloudops-artifacts-test").save("t1", "hello-app", "hi", {"src/app.py": "x"}, template, "sre")
    bundle = client.get("/api/apps/hello-app/versions/1").json()
    assert bundle["files"] == {"src/app.py": "x"} and bundle["manifest"]["version"] == 1
    assert client.get("/api/apps/nope-app/versions/1").status_code == 404


def test_single_page_app_routes_fall_back_to_index(client, tmp_path, monkeypatch):
    from console import server
    if server.STATIC is None:
        pytest.skip("web/dist not built")
    for path in ("/", "/tickets", "/accounts"):
        res = client.get(path)
        assert res.status_code == 200 and "<div id=\"root\">" in res.text
    assert client.head("/tickets").status_code == 200
