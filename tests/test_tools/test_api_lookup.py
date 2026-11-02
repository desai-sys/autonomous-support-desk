"""describe_aws_operation / list_aws_operations read the installed SDK's API definitions."""
import json

import botocore

from src.core.approvals import is_mutating
from src.tools.aws_api_tools import describe_aws_operation, list_aws_operations


def test_lookup_tools_are_read_only():
    assert not is_mutating("describe_aws_operation") and not is_mutating("list_aws_operations")


def test_describe_returns_required_params_types_and_enums():
    d = json.loads(describe_aws_operation(service="ec2", operation="create_vpc_peering_connection"))
    assert d["operation"] == "CreateVpcPeeringConnection" and d["required"] == ["VpcId"]
    assert {"PeerVpcId", "PeerOwnerId", "PeerRegion"} <= set(d["parameters"])
    tags = d["parameters"]["TagSpecifications"]["shape"]
    assert tags["type"] == "list" and "vpc-peering-connection" in tags["of"]["ResourceType"]["shape"]["enum"]
    assert d["returns"] == ["VpcPeeringConnection"] and d["sdk_version"] == botocore.__version__
    assert not d["read_only"]


def test_large_operations_are_summarised_and_drillable():
    big = describe_aws_operation(service="ec2", operation="RunInstances")
    assert len(big) <= 12000 and "parameter=" in json.loads(big)["note"]
    ebs = json.loads(describe_aws_operation(service="ec2", operation="RunInstances", parameter="BlockDeviceMappings.Ebs"))
    assert "VolumeSize" in ebs["parameters"] and ebs["parameter"] == "BlockDeviceMappings.Ebs"


def test_errors():
    assert json.loads(describe_aws_operation(service="nope", operation="X"))["status"] == "failed"
    assert json.loads(describe_aws_operation(service="ec2", operation="RunInstances", parameter="Bogus"))["status"] == "failed"


def test_list_operations_with_keyword():
    out = json.loads(list_aws_operations(service="ec2", keyword="vpc peering"))
    names = {o["name"]: o["read_only"] for o in out["operations"]}
    assert names["CreateVpcPeeringConnection"] is False and names["DescribeVpcPeeringConnections"] is True
