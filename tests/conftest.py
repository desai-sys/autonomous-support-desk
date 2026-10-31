"""
Shared fixtures. Tests run against moto, never real AWS.
"""
import os

import pytest

# Safe defaults before any project module reads config.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("ARTIFACT_BUCKET", "cloudops-artifacts-test")


@pytest.fixture
def aws(monkeypatch):
    """Isolated moto AWS backend with fake credentials."""
    from moto import mock_aws
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws(config={"iam": {"load_aws_managed_policies": True}}):
        yield


def _foundation_tables():
    """DynamoDB table definitions from infra/foundation.yaml, so tests use the deployed schema."""
    import pathlib
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    # Accept CloudFormation short-form tags (!Sub, !Ref, ...) as plain values.
    _Loader.add_multi_constructor("!", lambda loader, suffix, node: (
        loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
        else loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
        else loader.construct_mapping(node)))
    template = yaml.load((pathlib.Path(__file__).parent.parent / "infra/foundation.yaml").read_text(),
                         Loader=_Loader)
    for res in template["Resources"].values():
        if res["Type"] == "AWS::DynamoDB::Table":
            yield res["Properties"]


@pytest.fixture
def tables(aws):
    """Create every platform table (named as config's defaults) in moto."""
    import boto3
    import config

    names = {
        "${AppName}-tickets": config.TICKETS_TABLE, "${AppName}-audit": config.AUDIT_TABLE,
        "${AppName}-actions": config.ACTIONS_TABLE, "${AppName}-accounts": config.ACCOUNTS_TABLE,
        "${AppName}-usage": config.USAGE_TABLE,
    }
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    for props in _foundation_tables():
        kwargs = {
            "TableName": names[props["TableName"]],
            "AttributeDefinitions": props["AttributeDefinitions"],
            "KeySchema": props["KeySchema"],
            "BillingMode": "PAY_PER_REQUEST",
        }
        if props.get("GlobalSecondaryIndexes"):
            kwargs["GlobalSecondaryIndexes"] = props["GlobalSecondaryIndexes"]
        ddb.create_table(**kwargs)
    return names
