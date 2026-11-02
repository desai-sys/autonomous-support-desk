"""One agent run working across accounts via each tool's `account` argument."""
import json

import pytest
from strands import Agent, tool

from src.core.account_scope import AccountDirectory, AccountScopedTool, UnknownAccount
from src.core.approvals import ActionStore, ApprovalGate, execute_actions
from src.core.aws_clients import get_active_account
from tests.test_core.test_approvals import ScriptedModel

seen = []


@tool
def describe_vpcs(region: str = "us-east-1") -> str:
    """Describe VPCs."""
    seen.append(("describe", get_active_account()))
    return json.dumps({"Vpcs": [{"VpcId": f"vpc-{get_active_account() or 'platform'}"}]})


@tool
def call_aws_api(service: str, operation: str, parameters: str = "{}") -> str:
    """Call any AWS API."""
    seen.append((operation, get_active_account()))
    return json.dumps({"status": "failed" if "Fail" in operation else "success"})


def directory(default=None):
    return AccountDirectory("999999999999", {"111122223333": "prod", "444455556666": "ta-aws-1"}, default=default)


class TestDirectory:
    @pytest.mark.parametrize("ref,expected", [
        ("platform", None), ("Platform Account", None), ("999999999999", None),
        ("prod", "111122223333"), ("TA-AWS-1", "444455556666"), ("4444-5555-6666", "444455556666"),
        (None, None), ("", None),
    ])
    def test_resolve(self, ref, expected):
        assert directory().resolve(ref) == expected

    def test_default_applies_when_account_is_omitted(self):
        assert directory(default="111122223333").resolve(None) == "111122223333"

    def test_unknown_accounts_are_refused_with_the_valid_choices(self):
        with pytest.raises(UnknownAccount, match="prod \\(111122223333\\)"):
            directory().resolve("staging")
        with pytest.raises(UnknownAccount):
            directory().resolve("777788889999")

    def test_unregistered_ids_in_message_and_prompt_note(self):
        d = directory()
        assert d.unregistered_ids_in("peer 111122223333 with 777788889999 arn:aws:iam::555555555555:role/x") == ["777788889999"]
        note = d.prompt_note()
        assert "ta-aws-1 — 444455556666" in note and "Only state facts about an account" in note

    def test_wrapped_spec_gains_an_account_parameter(self):
        spec = AccountScopedTool(describe_vpcs, directory()).tool_spec
        assert "account" in spec["inputSchema"]["json"]["properties"]
        assert "account" not in describe_vpcs.tool_spec["inputSchema"]["json"]["properties"]


@pytest.fixture
def store(tables):
    seen.clear()
    return ActionStore(region="us-east-1")


def run(store, calls, default=None):
    d = directory(default)
    gate = ApprovalGate(store, tenant_id="t1", session_id="s1", requested_by="alice", account_id=None,
                        region="us-east-1", directory=d)
    tools = [AccountScopedTool(t, d) for t in (describe_vpcs, call_aws_api)]
    model = ScriptedModel(calls)
    Agent(model=model, tools=tools, hooks=[gate], callback_handler=None)("peer them")
    return gate, d, model


def test_cross_account_vpc_peering_in_one_turn(store):
    peer = json.dumps({"VpcId": "vpc-platform", "PeerVpcId": "vpc-444455556666", "PeerOwnerId": "444455556666"})
    gate, d, _ = run(store, [
        ("describe_vpcs", {"account": "platform"}),
        ("describe_vpcs", {"account": "ta-aws-1"}),
        ("call_aws_api", {"service": "ec2", "operation": "CreateVpcPeeringConnection", "parameters": peer,
                          "account": "platform"}),
        ("call_aws_api", {"service": "ec2", "operation": "AcceptVpcPeeringConnection",
                          "parameters": '{"VpcPeeringConnectionId": "pcx-1"}', "account": "ta-aws-1"}),
    ])
    # Reads ran immediately, each in its own account; the changes only queued.
    assert seen == [("describe", None), ("describe", "444455556666")]
    assert [(a["tool_input"]["operation"], a["account_id"]) for a in gate.queued] == [
        ("CreateVpcPeeringConnection", ""), ("AcceptVpcPeeringConnection", "444455556666")]
    assert all("account" not in a["tool_input"] for a in gate.queued)
    assert set(d.used) == {None, "444455556666"}

    results = execute_actions([a["action_id"] for a in gate.queued], tenant_id="t1", approver="bob",
                              approver_role="sre", tools={"call_aws_api": call_aws_api}, store=store,
                              stop_on_failure=True)
    assert [r["status"] for r in results] == ["succeeded", "succeeded"]
    assert seen[2:] == [("CreateVpcPeeringConnection", None), ("AcceptVpcPeeringConnection", "444455556666")]


def test_stop_on_failure_leaves_later_steps_pending(store):
    gate, _, _ = run(store, [
        ("call_aws_api", {"service": "ec2", "operation": "CreateFail", "account": "platform"}),
        ("call_aws_api", {"service": "ec2", "operation": "AcceptVpcPeeringConnection", "account": "prod"}),
    ])
    first, second = (a["action_id"] for a in gate.queued)
    results = execute_actions([first, second], tenant_id="t1", approver="bob", approver_role="sre",
                              tools={"call_aws_api": call_aws_api}, store=store, stop_on_failure=True)
    assert [r["status"] for r in results] == ["failed", "skipped"]
    assert store.get(second)["status"] == "pending"


def test_unknown_account_is_reported_to_the_model_not_run(store):
    gate, _, model = run(store, [("describe_vpcs", {"account": "staging"}),
                                 ("call_aws_api", {"service": "ec2", "operation": "CreateVpc", "account": "staging"})])
    assert seen == [] and gate.queued == []
    texts = [c["text"] for r in model.tool_results for c in r["content"] if "text" in c]
    assert sum("Unknown account 'staging'" in t for t in texts) == 2


def test_default_account_is_used_when_the_model_omits_it(store):
    run(store, [("describe_vpcs", {})], default="111122223333")
    assert seen == [("describe", "111122223333")]
