"""Proposals that are known to fail are refused before they become approval cards."""
import json

from strands import Agent, tool

from src.core.approvals import ActionStore, ApprovalGate
from src.core.proposal_checks import ProposalChecks
from tests.test_core.test_approvals import ScriptedModel


def api(op, **params):
    return "call_aws_api", {"service": "ec2", "operation": op, "parameters": json.dumps(params)}


def test_peering_through_generic_api_is_redirected():
    checks = ProposalChecks()
    for op in ("create_vpc_peering_connection", "CreateVpcPeeringConnection", "accept_vpc_peering_connection"):
        assert "create_vpc_peering" in checks.problem(*api(op, VpcId="vpc-1", PeerVpcId="vpc-2"))
    assert checks.problem("create_vpc_peering", {"requester_vpc_id": "vpc-1"}) is None


def test_overlapping_vpcs_in_one_request_are_refused():
    checks = ProposalChecks()
    assert checks.problem(*api("create_vpc", CidrBlock="10.0.0.0/16")) is None
    problem = checks.problem(*api("CreateVpc", CidrBlock="10.0.0.0/16"))
    assert "overlaps" in problem and "10.1.0.0/16" in problem
    assert checks.problem(*api("create_vpc", CidrBlock="10.1.0.0/16")) is None
    assert checks.problem(*api("create_vpc", CidrBlock="10.1.128.0/17")) is not None
    assert checks.problem("stop_ec2_instance", {"instance_id": "i-1"}) is None


@tool
def call_aws_api(service: str, operation: str, parameters: str = "{}") -> str:
    """Call any AWS API."""
    raise AssertionError("must not run")


def test_gate_refuses_without_queuing(tables):
    store = ActionStore(region="us-east-1")
    gate = ApprovalGate(store, tenant_id="t1", session_id="s1", requested_by="alice",
                        account_id=None, region="us-east-1")
    model = ScriptedModel([api("create_vpc", CidrBlock="10.0.0.0/16"),
                           api("create_vpc", CidrBlock="10.0.0.0/16")])
    Agent(model=model, tools=[call_aws_api], hooks=[gate], callback_handler=None)("make two vpcs")
    assert len(gate.queued) == 1
    texts = [c["text"] for r in model.tool_results for c in r["content"] if "text" in c]
    assert any(t.startswith("NOT QUEUED") and "overlaps" in t for t in texts)
