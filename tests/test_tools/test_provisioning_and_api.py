"""launch_ec2_instance and the generic AWS API tools (moto)."""
import json

import boto3
import pytest

from src.core.approvals import is_mutating
from src.tools.aws_api_tools import call_aws_api, query_aws_api
from src.tools.provisioning_tools import launch_ec2_instance


def test_gate_classification():
    assert not is_mutating("query_aws_api")
    assert is_mutating("call_aws_api")
    assert is_mutating("launch_ec2_instance")


def test_launch_defaults_to_amazon_linux_with_ssm_profile(aws):
    out = json.loads(launch_ec2_instance(name="web-1", volume_size_gb=20))
    assert out["status"] == "success", out
    [inst] = out["details"]["instances"]
    ec2 = boto3.client("ec2", region_name="us-east-1")
    desc = ec2.describe_instances(InstanceIds=[inst["instance_id"]])["Reservations"][0]["Instances"][0]
    assert desc["InstanceType"] == "t3.micro"
    assert {"Key": "Name", "Value": "web-1"} in desc["Tags"]
    assert "CloudOpsEC2SSMRole" in desc["IamInstanceProfile"]["Arn"]
    boto3.client("iam").get_role(RoleName="CloudOpsEC2SSMRole")


def test_launch_rejects_bad_ami_choices(aws):
    assert "Unknown AMI" in json.loads(launch_ec2_instance(ami="gentoo"))["error_message"]
    out = json.loads(launch_ec2_instance(ami="windows-2022", instance_type="t4g.small"))
    assert "no ARM image" in out["error_message"]


def test_query_runs_read_only_operations(aws):
    boto3.client("sns", region_name="us-east-1").create_topic(Name="alerts")
    out = json.loads(query_aws_api(service="sns", operation="list_topics"))
    assert out["Topics"][0]["TopicArn"].endswith(":alerts")


@pytest.mark.parametrize("service,operation,params,error", [
    ("sns", "CreateTopic", '{"Name": "x"}', "not_read_only"),
    ("secretsmanager", "GetSecretValue", '{"SecretId": "x"}', "sensitive_operation"),
    ("sts", "GetSessionToken", "{}", "sensitive_operation"),
    ("ssm", "GetParameter", '{"Name": "x", "WithDecryption": true}', "sensitive_operation"),
    ("nosuchservice", "ListThings", "{}", "ValueError"),
    ("ec2", "DescribeUnicorns", "{}", "ValueError"),
])
def test_query_refuses(aws, service, operation, params, error):
    assert json.loads(query_aws_api(service=service, operation=operation, parameters=params))["error_type"] == error


def test_call_runs_any_operation(aws):
    out = json.loads(call_aws_api(service="ec2", operation="CreateVpc", parameters='{"CidrBlock": "10.9.0.0/16"}'))
    assert out["status"] == "success" and out["response"]["Vpc"]["CidrBlock"] == "10.9.0.0/16"
    bad = json.loads(call_aws_api(service="ec2", operation="CreateVpc", parameters='[1]'))
    assert bad["status"] == "failed"


def test_call_respects_the_active_account(aws):
    from src.core import aws_clients
    aws_clients._creds_cache.clear()
    with aws_clients.use_account("111111111111"):
        out = json.loads(call_aws_api(service="sns", operation="CreateTopic", parameters='{"Name": "x"}'))
    assert ":111111111111:x" in out["response"]["TopicArn"]
