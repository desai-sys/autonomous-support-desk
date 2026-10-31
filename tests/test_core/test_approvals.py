"""
The approval gate must make it impossible for the agent to change anything
without a person approving the exact recorded call.

These tests run a real Strands Agent with a scripted model that tries to call a
mutating tool, against a moto-backed DynamoDB actions table.
"""
import json

import pytest
from strands import Agent, tool
from strands.models.model import Model

from src.core import approvals
from src.core.approvals import ActionStore, ApprovalGate, execute_actions, is_mutating, reject_action

calls = []


@tool
def stop_ec2_instance(instance_id: str, region: str = "us-east-1") -> str:
    """Stop an EC2 instance."""
    calls.append(("stop", instance_id, region))
    return json.dumps({"status": "success", "instance_id": instance_id})


@tool
def list_ec2_instances(region: str = "us-east-1") -> str:
    """List EC2 instances."""
    calls.append(("list", region))
    return json.dumps({"instances": ["i-1"]})


class ScriptedModel(Model):
    """Emits the scripted tool calls on the first turn, then a final text reply."""

    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.tool_results = []

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        last = messages[-1]
        results = [c["toolResult"] for c in last["content"] if "toolResult" in c]
        yield {"messageStart": {"role": "assistant"}}
        if not results:
            for i, (name, args) in enumerate(self.tool_calls):
                yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{i}", "name": name}}}}
                yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
                yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        self.tool_results = results
        yield {"contentBlockDelta": {"delta": {"text": "done"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


@pytest.fixture
def store(tables):
    calls.clear()
    return ActionStore(region="us-east-1")


def run_agent(store, tool_calls, account_id=None):
    gate = ApprovalGate(store, tenant_id="t1", session_id="s1", requested_by="alice",
                        account_id=account_id, region="eu-west-1")
    model = ScriptedModel(tool_calls)
    agent = Agent(model=model, tools=[stop_ec2_instance, list_ec2_instances],
                  hooks=[gate], callback_handler=None)
    agent("stop i-123")
    return gate, model


def tools_by_name():
    return {"stop_ec2_instance": stop_ec2_instance, "list_ec2_instances": list_ec2_instances}


def test_classification():
    assert is_mutating("stop_ec2_instance")
    assert is_mutating("deploy_artifact_to_lambda")
    assert not is_mutating("list_ec2_instances")
    assert not is_mutating("create_deployment_plan")


def test_read_only_tool_runs_but_mutating_tool_is_only_queued(store):
    gate, model = run_agent(store, [("list_ec2_instances", {"region": "eu-west-1"}),
                                    ("stop_ec2_instance", {"instance_id": "i-123", "region": "eu-west-1"})])
    assert calls == [("list", "eu-west-1")]          # the stop never ran
    assert len(gate.queued) == 1
    action = gate.queued[0]
    assert action["tool_name"] == "stop_ec2_instance"
    assert action["tool_input"] == {"instance_id": "i-123", "region": "eu-west-1"}
    assert action["status"] == "pending"
    # The model was told the call was queued, not that it succeeded.
    texts = [c["text"] for r in model.tool_results for c in r["content"] if "text" in c]
    assert any("NOT EXECUTED YET" in t and action["action_id"] in t for t in texts)
    assert store.list_for_session("t1", "s1")[0]["action_id"] == action["action_id"]


def test_approved_action_runs_exactly_once_with_recorded_arguments(store):
    gate, _ = run_agent(store, [("stop_ec2_instance", {"instance_id": "i-123"})])
    action_id = gate.queued[0]["action_id"]

    first = execute_actions([action_id], tenant_id="t1", approver="bob", approver_role="sre",
                            tools=tools_by_name(), store=store)
    assert first[0]["status"] == "succeeded"
    assert calls == [("stop", "i-123", "us-east-1")]

    second = execute_actions([action_id], tenant_id="t1", approver="bob", approver_role="sre",
                             tools=tools_by_name(), store=store)
    assert second[0]["status"] == "error"
    assert "already succeeded" in second[0]["error"]
    assert len(calls) == 1


def test_execution_runs_in_the_recorded_account(store, monkeypatch):
    seen = []

    @tool
    def stop_ec2_instance(instance_id: str) -> str:
        """Stop."""
        from src.core.aws_clients import get_active_account
        seen.append(get_active_account())
        return "{}"

    gate, _ = run_agent(store, [("stop_ec2_instance", {"instance_id": "i-9"})], account_id="111122223333")
    execute_actions([gate.queued[0]["action_id"]], tenant_id="t1", approver="bob",
                    approver_role="operator", tools={"stop_ec2_instance": stop_ec2_instance}, store=store)
    assert seen == ["111122223333"]


@pytest.mark.parametrize("kwargs,error", [
    ({"tenant_id": "other"}, "not found"),
    ({"approver_role": "viewer"}, "cannot approve"),
])
def test_execution_is_refused(store, kwargs, error):
    gate, _ = run_agent(store, [("stop_ec2_instance", {"instance_id": "i-123"})])
    args = {"tenant_id": "t1", "approver": "bob", "approver_role": "sre", **kwargs}
    result = execute_actions([gate.queued[0]["action_id"]], tools=tools_by_name(), store=store, **args)
    assert error in result[0]["error"]
    assert calls == []


def test_separate_approver_required(store, monkeypatch):
    monkeypatch.setattr(approvals.config, "REQUIRE_SEPARATE_APPROVER", True)
    gate, _ = run_agent(store, [("stop_ec2_instance", {"instance_id": "i-123"})])
    result = execute_actions([gate.queued[0]["action_id"]], tenant_id="t1", approver="alice",
                             approver_role="admin", tools=tools_by_name(), store=store)
    assert "different person" in result[0]["error"]


def test_expired_and_rejected_actions_do_not_run(store, monkeypatch):
    gate, _ = run_agent(store, [("stop_ec2_instance", {"instance_id": "i-1"}),
                                ("stop_ec2_instance", {"instance_id": "i-2"})])
    expired, rejected = (a["action_id"] for a in gate.queued)
    store.table.update_item(Key={"action_id": expired}, UpdateExpression="SET expires_at = :e",
                            ExpressionAttributeValues={":e": 1})
    assert reject_action(rejected, tenant_id="t1", user="bob", store=store)
    results = execute_actions([expired, rejected], tenant_id="t1", approver="bob",
                              approver_role="sre", tools=tools_by_name(), store=store)
    assert "expired" in results[0]["error"]
    assert "already rejected" in results[1]["error"]
    assert calls == []


def test_queue_failure_never_executes(store, monkeypatch):
    monkeypatch.setattr(store, "create", lambda **kw: (_ for _ in ()).throw(RuntimeError("ddb down")))
    gate, model = run_agent(store, [("stop_ec2_instance", {"instance_id": "i-123"})])
    assert calls == [] and gate.queued == []
    texts = [c["text"] for r in model.tool_results for c in r["content"] if "text" in c]
    assert any("could not be queued" in t for t in texts)
