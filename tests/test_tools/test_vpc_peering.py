"""create_vpc_peering: one action that requests, accepts and routes, across accounts."""
import json


import pytest

from src.core import aws_clients
from src.core.aws_clients import boto3
from src.core.accounts import AccountRegistry
from src.tools.network_workflow_tools import create_vpc_peering

OTHER = "111111111111"


@pytest.fixture
def two_accounts(tables, monkeypatch):
    monkeypatch.setattr("src.tools.network_workflow_tools.time.sleep", lambda s: None)
    aws_clients._creds_cache.clear()
    AccountRegistry().add(OTHER, "ta-aws-1", "admin")

    def vpc(account, cidr):
        with aws_clients.use_account(account):
            ec2 = boto3.client("ec2", region_name="us-east-1")
            return ec2.create_vpc(CidrBlock=cidr)["Vpc"]["VpcId"]
    return vpc


def _routes(account, vpc_id):
    with aws_clients.use_account(account):
        tables = boto3.client("ec2", region_name="us-east-1").describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]
    return [r for t in tables for r in t["Routes"] if r.get("VpcPeeringConnectionId")]


def test_peers_across_accounts_in_one_action(two_accounts):
    platform_vpc = two_accounts(None, "10.0.0.0/16")
    other_vpc = two_accounts(OTHER, "10.1.0.0/16")
    out = json.loads(create_vpc_peering(requester_vpc_id=other_vpc, accepter_vpc_id=platform_vpc,
                                        requester_account="ta-aws-1", accepter_account="platform",
                                        name="ta-to-platform"))
    assert out["status"] == "success", out
    assert out["peering_status"] == "active" and out["name"] == "ta-to-platform"
    assert out["requester"]["account_id"] == OTHER
    assert _routes(OTHER, other_vpc)[0]["DestinationCidrBlock"] == "10.0.0.0/16"
    assert _routes(None, platform_vpc)[0]["DestinationCidrBlock"] == "10.1.0.0/16"


def test_rejects_overlapping_cidrs_before_creating_anything(two_accounts):
    a = two_accounts(None, "10.0.0.0/16")
    b = two_accounts(OTHER, "10.0.128.0/17")
    out = json.loads(create_vpc_peering(requester_vpc_id=a, accepter_vpc_id=b, accepter_account=OTHER))
    assert out["error_type"] == "overlapping_cidrs" and "10.0.0.0/16" in out["error_message"]
    with aws_clients.use_account(None):
        assert not boto3.client("ec2", region_name="us-east-1").describe_vpc_peering_connections()[
            "VpcPeeringConnections"]


def test_vpc_looked_up_in_its_own_account(two_accounts):
    platform_vpc = two_accounts(None, "10.0.0.0/16")
    out = json.loads(create_vpc_peering(requester_vpc_id=platform_vpc, accepter_vpc_id="vpc-0123456789abcdef0",
                                        accepter_account="ta-aws-1"))
    assert out["error_type"] == "vpc_not_found" and "ta-aws-1" in out["error_message"]


def test_unknown_account(two_accounts):
    out = json.loads(create_vpc_peering(requester_vpc_id="vpc-1", accepter_vpc_id="vpc-2",
                                        accepter_account="nope"))
    assert out["error_type"] == "unknown_account"


def test_needs_approval():
    from src.core.approvals import is_mutating
    assert is_mutating("create_vpc_peering")
