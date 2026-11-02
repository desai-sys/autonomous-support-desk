"""Agent chat/execute flow (with the LLM stubbed) and the console's gateway."""
import io
import json
from unittest.mock import MagicMock

import pytest

from src.core.security import User, UserRole


@pytest.fixture
def ep(tables, monkeypatch):
    import src.agentcore_entrypoint as ep
    from src.core.accounts import AccountRegistry
    from src.core.approvals import ActionStore
    monkeypatch.setattr(ep, "action_store", ActionStore())
    monkeypatch.setattr(ep, "account_registry", AccountRegistry())
    monkeypatch.setattr(ep, "base_account_id", lambda: "999999999999")
    monkeypatch.setattr(ep.router, "route", lambda *a, **k: ["compute"])
    return ep


def payload(**kw):
    base = {"mode": "chat", "prompt": "stop i-1", "user_message": "stop i-1", "session_id": "s1",
            "tenant_id": "t1", "user": {"id": "alice", "role": "operator"}, "account_ids": []}
    return {**base, **kw}


def test_chat_returns_pending_actions_and_execute_runs_them(ep, monkeypatch):
    ran = []

    def fake_run(prompt, domains, notes, gate, read_only=False, directory=None):
        # What the ApprovalGate does when the model calls a mutating tool.
        gate.queued.append(gate.store.create(tool_name="stop_ec2_instance",
                                             tool_input={"instance_id": "i-1"}, **gate.context))
        return "I queued a stop for i-1.", domains, 10
    monkeypatch.setattr(ep, "_run_agent_with_domains", fake_run)
    monkeypatch.setitem(ep.TOOL_REGISTRY, "stop_ec2_instance",
                        lambda **kw: ran.append(kw) or json.dumps({"status": "success"}))

    out = ep.invoke(payload())
    assert out["response"] == "I queued a stop for i-1." and not out["escalate"]
    [action] = out["pending_actions"]
    assert action["tool_input"] == {"instance_id": "i-1"} and action["requested_by"] == "alice"

    viewer = ep.invoke({"mode": "execute", "action_ids": [action["action_id"]], "tenant_id": "t1",
                        "user": {"id": "v", "role": "viewer"}})
    assert viewer["results"][0]["status"] == "error" and ran == []

    done = ep.invoke({"mode": "execute", "action_ids": [action["action_id"]], "tenant_id": "t1",
                      "user": {"id": "bob", "role": "sre"}})
    assert done["results"][0]["status"] == "succeeded" and ran == [{"instance_id": "i-1"}]
    assert "✅" in done["response"]


def test_viewer_chat_is_read_only(ep, monkeypatch):
    seen = {}
    monkeypatch.setattr(ep, "_run_agent_with_domains",
                        lambda prompt, domains, notes, gate, read_only=False, directory=None:
                        seen.update(read_only=read_only, notes=notes) or ("ok", domains, 1))
    ep.invoke(payload(user={"id": "v", "role": "viewer"}))
    assert seen["read_only"] and any("READ-ONLY" in n for n in seen["notes"])


def test_single_run_sees_every_registered_account(ep, monkeypatch):
    ep.account_registry.add("111122223333", "prod", "admin")
    ep.account_registry.add("444455556666", "ta-aws-1", "admin")
    runs = []

    def fake_run(prompt, domains, notes, gate, read_only=False, directory=None):
        runs.append({"notes": notes, "directory": directory, "gate": gate})
        directory.resolve("platform")
        directory.resolve("ta-aws-1")
        return "Platform: 0 public. ta-aws-1: 1 public.", domains, 3
    monkeypatch.setattr(ep, "_run_agent_with_domains", fake_run)

    out = ep.invoke(payload(account_ids=[], user_message="public buckets in platform and ta-aws-1, and 777788889999"))
    [r] = runs                                               # one run, not one per account
    assert r["gate"].directory is r["directory"]
    assert set(r["directory"].registered) == {"111122223333", "444455556666"}
    joined = "\n".join(r["notes"])
    assert "ta-aws-1 — 444455556666" in joined and "777788889999 are not registered" in joined
    assert out["accounts"] == ["platform account (999999999999)", "ta-aws-1 (444455556666)"]
    assert out["refused_accounts"] == ["777788889999"]


def test_selected_accounts_become_default_or_each(ep, monkeypatch):
    ep.account_registry.add("111122223333", "prod", "admin")
    ep.account_registry.add("444455556666", "dev", "admin")
    runs = []
    monkeypatch.setattr(ep, "_run_agent_with_domains",
                        lambda prompt, domains, notes, gate, read_only=False, directory=None:
                        runs.append((notes, directory)) or ("ok", domains, 1))
    ep.invoke(payload(account_ids=["111122223333"]))
    assert runs[-1][1].default == "111122223333"
    ep.invoke(payload(account_ids=["111122223333", "444455556666"]))
    assert runs[-1][1].default is None and any("SELECTED ACCOUNTS" in n for n in runs[-1][0])


def test_escalation_flag(ep, monkeypatch):
    monkeypatch.setattr(ep, "_run_agent_with_domains",
                        lambda *a, **k: ("ESCALATE - no tool for that", ["ops"], 1))
    assert ep.invoke(payload())["escalate"]


def test_bad_requests(ep):
    assert ep.invoke({"mode": "nope"})["error"] == "BadRequest"
    assert ep.invoke(payload(prompt=" "))["error"] == "BadRequest"


# ── Gateway ───────────────────────────────────────────────────────────────────

@pytest.fixture
def gateway(tables, monkeypatch):
    from src.agents import gateway as gw
    monkeypatch.setattr(gw.config, "AGENT_ARN", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x")
    g = gw.AgentGateway()
    g.client = MagicMock()
    g.rag = MagicMock()
    return g


def respond(gateway, body: dict):
    gateway.client.invoke_agent_runtime.return_value = {"response": io.BytesIO(json.dumps(body).encode())}


def user(role=UserRole.OPERATOR):
    return User.build("alice", "alice@example.com", role, "t1")


def test_gateway_sends_identity_targets_and_history(gateway):
    respond(gateway, {"response": "hi", "pending_actions": [{"action_id": "a1"}]})
    res = gateway.chat("stop it", user(), "conv-1", history=["User: x"], accounts=["111122223333"],
                       region="eu-west-1", artifact={"uri": "s3://b/k.zip"})
    sent = json.loads(gateway.client.invoke_agent_runtime.call_args.kwargs["payload"])
    assert sent["user"] == {"id": "alice", "email": "alice@example.com", "role": "operator"}
    assert sent["account_ids"] == ["111122223333"] and sent["region"] == "eu-west-1"
    assert sent["user_message"] == "stop it" and sent["prompt"].startswith("Conversation so far")
    assert len(gateway.client.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"]) >= 33
    assert res.pending_actions == [{"action_id": "a1"}] and not res.escalate


def test_gateway_falls_back_to_knowledge_base(gateway):
    from src.models.query import Resolution
    respond(gateway, {"response": "ESCALATE - unknown", "escalate": True})
    gateway.rag.resolve.return_value = Resolution(text="past fix", resolution_type="rag", confidence=0.9)
    res = gateway.chat("weird error", user(), "conv-1")
    assert res.resolution_type == "rag" and res.text == "past fix"


def test_gateway_reports_agent_outage(gateway):
    gateway.client.invoke_agent_runtime.side_effect = RuntimeError("boom")
    res = gateway.chat("hi", user(), "conv-1")
    assert res.escalate and "unavailable" in res.text


def test_account_name_labels_results(tables, monkeypatch):
    import src.agentcore_entrypoint as ep
    from src.core.accounts import AccountRegistry
    reg = AccountRegistry()
    reg.add("111111111111", "ta-aws-1", "admin")
    monkeypatch.setattr(ep, "account_registry", reg)
    assert ep._account_name("111111111111") == "ta-aws-1 (111111111111)"
    assert ep._account_name(None) == "the platform account"
    assert ep._account_name("222222222222") == "222222222222"
