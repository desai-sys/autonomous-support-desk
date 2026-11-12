"""query_aws_api runs with no approval, so what it accepts must stay narrow."""
import json

import botocore.session
import pytest

import config
from src.tools import aws_api_tools as api
from src.tools.aws_api_tools import query_aws_api, read_refusal, redact

SESSION = botocore.session.get_session()
ALLOWED = [(svc, op) for svc in SESSION.get_available_services()
           for op in SESSION.get_service_model(svc).operation_names if not read_refusal(svc, op)]


@pytest.mark.parametrize("service,operation", [
    ("elasticache", "TestFailover"), ("apigateway", "TestInvokeMethod"), ("stepfunctions", "TestState"),
    ("lightsail", "TestAlarm"), ("dms", "TestConnection"),                       # side effects
    ("dynamodb", "Scan"), ("dynamodb", "Query"), ("dynamodb", "GetItem"), ("dynamodb", "BatchGetItem"),
    ("s3", "GetObject"), ("athena", "GetQueryResults"), ("kinesis", "GetRecords"),
    ("rds-data", "BatchExecuteStatement"), ("bedrock-runtime", "InvokeModel"),   # stored data
    ("secretsmanager", "GetSecretValue"), ("secretsmanager", "BatchGetSecretValue"),
    ("ecr", "GetAuthorizationToken"), ("sts", "GetSessionToken"), ("sts", "GetFederationToken"),
    ("ec2", "GetPasswordData"), ("lightsail", "GetInstanceAccessDetails"), ("lightsail", "GetKeyPair"),
    ("apigateway", "GetApiKeys"), ("appsync", "ListApiKeys"), ("glue", "GetConnection"),
    ("s3control", "GetDataAccess"), ("ec2", "GetVpnConnectionDeviceSampleConfiguration"),
    ("iam", "GetLoginProfile"), ("emr", "GetOnClusterAppUIPresignedURL"), ("ssm", "StartSession"),
    ("gamelift", "GetInstanceAccess"), ("kafka", "GetBootstrapBrokers"),         # secrets and access
    ("ec2", "RunInstances"), ("s3", "DeleteBucket"),                             # changes
])
def test_refused_without_approval(service, operation):
    assert read_refusal(service, operation)


@pytest.mark.parametrize("service,operation", [
    ("ec2", "DescribeVpcs"), ("s3", "GetBucketPolicy"), ("lambda", "GetFunction"), ("iam", "ListAccessKeys"),
    ("sts", "GetCallerIdentity"), ("secretsmanager", "ListSecrets"), ("logs", "GetLogEvents"),
    ("cloudformation", "ValidateTemplate"), ("iam", "SimulatePrincipalPolicy"), ("ce", "GetCostAndUsage"),
])
def test_ordinary_reads_allowed(service, operation):
    assert not read_refusal(service, operation)


def test_only_read_prefixes_and_reviewed_extras_are_allowed():
    odd = {(s, o) for s, o in ALLOWED if not o.startswith(api.READ_PREFIXES)}
    assert odd <= set(api.EXTRA_READS)
    assert len(ALLOWED) > 5000          # still broadly useful for metadata


def test_new_sensitive_looking_operations_are_blocked_by_default():
    leaked = [(s, o) for s, o in ALLOWED if api.SENSITIVE_READS.search(o)
              and (s, o) not in api.SENSITIVE_EXCEPTIONS]
    assert leaked == []


def test_secret_values_are_redacted():
    out = redact({
        "Configuration": {"FunctionName": "f", "Environment": {"Variables": {
            "DB_PASSWORD": "hunter2", "LOG_LEVEL": "INFO", "DATABASE_URL": "postgres://u:p@h/db"}}},
        "Code": {"Location": "https://s3/x?X-Amz-Signature=abc"},
        "containerDefinitions": [{"environment": [{"name": "API_TOKEN", "value": "t0k"},
                                                  {"name": "PORT", "value": "8080"}]}],
        "environmentVariables": {"CROSS_ACCOUNT_EXTERNAL_ID": "abc123", "AGENT_MODEL_ID": "nova"},
        "UserData": "IyEvYmlu", "KmsKeyArn": "arn:aws:kms:us-east-1:1:key/1", "SecretArn": "arn:aws:sm:x",
        "note": "connect with mysql://root:pw@db.internal/app",
    })
    variables = out["Configuration"]["Environment"]["Variables"]
    assert variables == {"DB_PASSWORD": api.REDACTED, "LOG_LEVEL": "INFO", "DATABASE_URL": api.REDACTED}
    assert out["Code"]["Location"] == api.REDACTED
    assert out["containerDefinitions"][0]["environment"] == [
        {"name": "API_TOKEN", "value": api.REDACTED}, {"name": "PORT", "value": "8080"}]
    assert out["environmentVariables"] == {"CROSS_ACCOUNT_EXTERNAL_ID": api.REDACTED, "AGENT_MODEL_ID": "nova"}
    assert out["UserData"] == api.REDACTED and out["note"] == api.REDACTED
    assert out["KmsKeyArn"].startswith("arn:") and out["SecretArn"].startswith("arn:")


def test_revealing_parameters_and_platform_resources_refused(aws, monkeypatch):
    out = json.loads(query_aws_api(service="ssm", operation="GetParameter",
                                   parameters='{"Name": "/x", "WithDecryption": true}'))
    assert out["error_type"] == "sensitive_operation"
    out = json.loads(query_aws_api(service="apigateway", operation="GetUsagePlanKeys",
                                   parameters='{"usagePlanId": "u", "includeValue": true}'))
    assert out["error_type"] == "sensitive_operation"
    out = json.loads(query_aws_api(service="dynamodb", operation="DescribeTable",
                                   parameters=json.dumps({"TableName": config.AUDIT_TABLE})))
    assert out["error_type"] == "platform_resource"
    monkeypatch.setattr(config, "PROTECTED_RESOURCE_NAMES", ["us-east-1_PoolId"])
    out = json.loads(query_aws_api(service="cognito-idp", operation="ListUsers",
                                   parameters='{"UserPoolId": "us-east-1_PoolId"}'))
    assert out["error_type"] == "platform_resource"


def test_results_are_redacted_end_to_end(aws):
    from src.core.aws_clients import boto3
    boto3.client("ec2", region_name="us-east-1").run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1, UserData="echo secret")
    instance = json.loads(query_aws_api(service="ec2", operation="DescribeInstances"))[
        "Reservations"][0]["Instances"][0]["InstanceId"]
    out = json.loads(query_aws_api(service="ec2", operation="DescribeInstanceAttribute",
                                   parameters=json.dumps({"InstanceId": instance, "Attribute": "userData"})))
    assert "secret" not in json.dumps(out)
