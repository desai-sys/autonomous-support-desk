"""Follow-ups are answered from recorded facts: tool results and what approved actions returned."""
import json

import pytest
from strands import Agent, tool

from src.core import conversations
from src.core.conversations import ConversationStore, ToolRecorder
from tests.test_core.test_approvals import ScriptedModel


@pytest.fixture
def store(tables):
    return ConversationStore()


def test_turns_and_executions_are_remembered_per_tenant_and_conversation(store):
    store.record_turn("t1", "conv-1", "create a vpc in ta-aws-1", "Queued it.",
                      [{"tool": "describe_vpcs", "account": "ta-aws-1", "input": "{}", "result": "[]"}],
                      [{"action_id": "a1", "summary": "call_aws_api(CreateVpc)", "account_id": "111111111111"}])
    store.record_execution("t1", "conv-1", "bob", [
        {"action_id": "a1", "summary": "call_aws_api(CreateVpc)", "status": "succeeded",
         "account_id": "111111111111", "result": json.dumps({"Vpc": {"VpcId": "vpc-0e66a2b99d1747eda"}})},
        {"action_id": "a2", "summary": "never-ran-step", "status": "skipped"}])
    records = store.recent("t1", "conv-1")
    assert [r["kind"] for r in records] == ["turn", "execution"]
    text = conversations.render(records)
    assert "User: create a vpc in ta-aws-1" in text and "tool describe_vpcs" in text
    assert "not yet run" in text and "vpc-0e66a2b99d1747eda" in text and "SUCCEEDED" in text
    assert "never-ran-step" not in text                             # skipped steps didn't happen
    assert store.recent("t2", "conv-1") == [] and store.recent("t1", "conv-2") == []
    assert conversations.history_lines(records) == ["User: create a vpc in ta-aws-1", "Assistant: Queued it."]


def test_secrets_are_not_kept_in_memory(store):
    store.record_execution("t1", "c", "bob", [{
        "action_id": "a", "summary": "create key", "status": "succeeded",
        "result": json.dumps({"AccessKey": {"AccessKeyId": "AKIA1", "SecretAccessKey": "s3cr3t"}})}])
    assert "s3cr3t" not in json.dumps(store.recent("t1", "c"))


def test_newest_history_is_kept_when_it_does_not_all_fit():
    records = [{"kind": "turn", "user": f"question {i}", "answer": "x" * 500, "tools": [], "proposed": []}
               for i in range(40)]
    text = conversations.render(records, budget=3000)
    assert "question 39" in text and "question 0" not in text
    assert conversations.render([]) is None


def test_memory_failures_never_break_a_request(aws):
    store = ConversationStore("table-that-does-not-exist")
    store.record_turn("t1", "c", "hi", "hello", [], [])
    assert store.recent("t1", "c") == []


@tool
def describe_vpcs() -> str:
    """Describe VPCs."""
    return json.dumps({"Vpcs": [{"VpcId": "vpc-123", "password": "hunter2"}]})


@tool
def delete_vpc(vpc_id: str) -> str:
    """Delete a VPC."""
    raise AssertionError("must not run")


def test_recorder_keeps_tool_results_but_not_queued_calls(tables):
    from src.core.approvals import ActionStore, ApprovalGate
    gate = ApprovalGate(ActionStore(region="us-east-1"), tenant_id="t1", session_id="s1",
                        requested_by="alice", account_id=None, region="us-east-1")
    recorder = ToolRecorder()
    model = ScriptedModel([("describe_vpcs", {}), ("delete_vpc", {"vpc_id": "vpc-123"})])
    Agent(model=model, tools=[describe_vpcs, delete_vpc], hooks=[gate, recorder], callback_handler=None)("x")
    assert [c["tool"] for c in recorder.calls] == ["describe_vpcs"]
    assert "vpc-123" in recorder.calls[0]["result"] and "hunter2" not in recorder.calls[0]["result"]
    assert len(gate.queued) == 1


def test_next_turn_is_built_from_the_record(tables, monkeypatch):
    import src.agentcore_entrypoint as ep
    from src.core.approvals import ActionStore
    monkeypatch.setattr(ep, "action_store", ActionStore())
    monkeypatch.setattr(ep, "conversation_store", ConversationStore())
    monkeypatch.setattr(ep, "base_account_id", lambda: "999999999999")
    routed, prompts = [], []
    monkeypatch.setattr(ep.router, "route", lambda text, history=None: routed.append((text, history)) or ["network"])
    monkeypatch.setattr(ep, "_run_agent_with_domains",
                        lambda prompt, domains, notes, gate, read_only=False, directory=None:
                        prompts.append(prompt) or ("Created it.", domains, 1))
    base = {"mode": "chat", "session_id": "conv-9", "tenant_id": "t1", "user": {"id": "alice", "role": "operator"}}

    ep.invoke({**base, "prompt": "create a vpc", "user_message": "create a vpc"})
    ep.conversation_store.record_execution("t1", "conv-9", "bob", [
        {"action_id": "a1", "summary": "CreateVpc", "status": "succeeded", "result": '{"VpcId": "vpc-0abc"}'}])
    # The browser sends a stale, misleading history; the platform's record wins.
    ep.invoke({**base, "prompt": "Conversation so far:\nAssistant: I made vpc-WRONG\n\nUser: peer it",
               "user_message": "peer it", "history": ["Assistant: I made vpc-WRONG"]})
    assert "vpc-0abc" in prompts[1] and "vpc-WRONG" not in prompts[1] and prompts[1].endswith("User: peer it")
    assert routed[1] == ("peer it", ["User: create a vpc", "Assistant: Created it."])
