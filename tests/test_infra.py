"""The templates must keep the properties the security design depends on."""
import json
import pathlib

import pytest
import yaml

ROOT = pathlib.Path(__file__).parent.parent


class _Loader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_Loader.add_multi_constructor("!", _tag)


def load(name):
    return yaml.load((ROOT / "infra" / name).read_text(), Loader=_Loader)


@pytest.fixture(scope="module")
def app():
    return load("app.yaml")["Resources"]


def _statements(role):
    return [s for p in role["Properties"].get("Policies", []) for s in p["PolicyDocument"]["Statement"]]


def test_the_planner_can_only_read(app):
    planner = app["PlannerRuntimeRole"]["Properties"]
    assert [json.dumps(a) for a in planner["ManagedPolicyArns"]] == [
        json.dumps({"Sub": "arn:${AWS::Partition}:iam::aws:policy/ReadOnlyAccess"})]
    allowed = {a for s in _statements(app["PlannerRuntimeRole"]) if s["Effect"] == "Allow"
               for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
    # Everything it may write is its own bookkeeping; nothing here changes a customer resource.
    assert allowed <= {
        "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "dynamodb:PutItem", "dynamodb:Scan",
        "dynamodb:GetItem", "dynamodb:UpdateItem", "dynamodb:Query", "s3:GetObject", "s3:GetObjectVersion",
        "s3:PutObject", "cloudformation:ValidateTemplate", "sts:AssumeRole", "secretsmanager:GetSecretValue",
        "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
        "xray:PutTraceSegments", "xray:PutTelemetryRecords", "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetWorkloadAccessTokenForJWT", "bedrock-agentcore:GetWorkloadAccessTokenForUserId"}
    assume = next(s for s in _statements(app["PlannerRuntimeRole"]) if s["Action"] == "sts:AssumeRole")
    assert "CrossAccountReadRoleName" in json.dumps(assume["Resource"])      # never the change role
    denied = {s["Sid"] for s in _statements(app["PlannerRuntimeRole"]) if s["Effect"] == "Deny"}
    assert {"PlatformRecords", "PlatformUsers", "PlatformFiles", "PlatformLogs"} <= denied


def test_the_executor_has_no_model_and_cannot_touch_the_platform(app):
    actions = json.dumps(_statements(app["AgentRuntimeRole"]))
    assert "bedrock:InvokeModel" not in actions
    protection = app["ExecutorPlatformProtection"]["Properties"]
    assert protection["RoleName"] == {"Ref": "AgentRuntimeRole"}
    statements = {s["Sid"]: s for s in protection["PolicyDocument"]["Statement"]}
    assert all(s["Effect"] == "Deny" for s in statements.values())
    assert {"Users", "ApprovalsOnlyThroughTheStateMachine", "AuditAndMemoryAreAppendOnly",
            "AccountAllowListIsReadOnly", "PlatformSecrets", "ItsOwnRuntimesStacksAndRoles",
            "ConsoleServiceAndImages", "PlatformBuckets"} <= set(statements)
    assert statements["Users"]["Action"] == "cognito-idp:*"
    assert statements["AuditAndMemoryAreAppendOnly"]["NotAction"] == ["dynamodb:PutItem", "dynamodb:DescribeTable"]
    assert statements["AccountAllowListIsReadOnly"]["NotAction"] == [
        "dynamodb:Scan", "dynamodb:GetItem", "dynamodb:DescribeTable"]


def test_each_runtime_runs_in_its_own_mode_with_its_own_role(app):
    planner, executor = app["PlannerRuntime"]["Properties"], app["AgentRuntime"]["Properties"]
    assert planner["EnvironmentVariables"]["RUNTIME_MODE"] == "planner"
    assert executor["EnvironmentVariables"]["RUNTIME_MODE"] == "executor"
    assert planner["RoleArn"] == {"GetAtt": "PlannerRuntimeRole.Arn"}
    assert executor["RoleArn"] == {"GetAtt": "AgentRuntimeRole.Arn"}
    assert planner["EnvironmentVariables"]["CROSS_ACCOUNT_ROLE_NAME"] == {"Ref": "CrossAccountReadRoleName"}
    assert executor["EnvironmentVariables"]["CROSS_ACCOUNT_ROLE_NAME"] == {"Ref": "CrossAccountRoleName"}


def test_no_secret_values_in_plain_environment(app):
    for runtime in ("PlannerRuntime", "AgentRuntime"):
        env = app[runtime]["Properties"]["EnvironmentVariables"]
        assert "CROSS_ACCOUNT_EXTERNAL_ID" not in env and "IDENTITY_SIGNING_KEY" not in env
        assert {"CROSS_ACCOUNT_EXTERNAL_ID_SECRET_ARN", "IDENTITY_SIGNING_KEY_SECRET_ARN"} <= set(env)
    for task in ("ConsoleTaskDefinition", "LearningLoopTaskDefinition"):
        container = app[task]["Properties"]["ContainerDefinitions"][0]
        plain = {e["Name"] for e in container["Environment"]}
        assert not plain & {"CROSS_ACCOUNT_EXTERNAL_ID", "IDENTITY_SIGNING_KEY", "OIDC_CLIENT_SECRET",
                            "OIDC_COOKIE_SECRET"}
    injected = {s["Name"] for s in app["ConsoleTaskDefinition"]["Properties"]["ContainerDefinitions"][0]["Secrets"]}
    assert {"IDENTITY_SIGNING_KEY", "CROSS_ACCOUNT_EXTERNAL_ID", "OIDC_COOKIE_SECRET"} <= injected


def test_secure_defaults():
    params = load("app.yaml")["Parameters"]
    assert params["RequireSeparateApprover"]["Default"] == "true"
    assert params["OriginAccess"]["Default"] == "private"
    assert params["NatGateways"]["Default"] == 2
    assert "CrossAccountExternalId" not in params                 # no longer passed around as a value


def test_managed_accounts_get_a_read_only_role_for_the_planner():
    cross = load("cross_account_role.yaml")["Resources"]
    read = cross["CrossAccountReadRole"]["Properties"]
    assert read["ManagedPolicyArns"] == ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
    trust = read["AssumeRolePolicyDocument"]["Statement"][0]
    assert trust["Principal"]["AWS"] == {"Ref": "PlannerRuntimeRoleArn"}
    assert trust["Condition"]["StringEquals"]["sts:ExternalId"] == {"Ref": "ExternalId"}
    change = cross["CrossAccountRole"]["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert change["Principal"]["AWS"] == {"Ref": "AgentRuntimeRoleArn"}


@pytest.mark.parametrize("template", ["app.yaml", "cross_account_role.yaml"])
def test_generated_apps_run_as_a_narrow_shared_role(template):
    role = load(template)["Resources"]["GeneratedAppRole"]["Properties"]
    assert role["RoleName"] == "cloudops-generated-app"
    assert role["AssumeRolePolicyDocument"]["Statement"][0]["Principal"] == {"Service": "lambda.amazonaws.com"}
    assert "AWSLambdaBasicExecutionRole" in json.dumps(role["ManagedPolicyArns"]) and len(role["ManagedPolicyArns"]) == 1
    [statement] = role["Policies"][0]["PolicyDocument"]["Statement"]
    assert set(statement["Action"]) == {"dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Query"}
    assert json.dumps(statement["Resource"]).count("table/cloudops-gen-*") == 1      # only generated apps' tables
