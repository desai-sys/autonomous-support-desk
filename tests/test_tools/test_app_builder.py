"""App builder: validation, versioned drafts, deploy (CloudFormation in moto) and the console preview."""
import io
import json
import zipfile

import boto3
import pytest

import config
from src.core import request_context
from src.services.app_bundles import AppBundleStore, BundleError, code_zip, validate
from src.tools import app_builder_tools as abt

TEMPLATE = """AWSTemplateFormatVersion: "2010-09-09"
Parameters:
  ArtifactBucket: {Type: String}
  ArtifactKey: {Type: String}
Resources:
  Table:
    Type: AWS::DynamoDB::Table
    Properties:
      BillingMode: PAY_PER_REQUEST
      AttributeDefinitions: [{AttributeName: id, AttributeType: S}]
      KeySchema: [{AttributeName: id, KeyType: HASH}]
  Role:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement: [{Effect: Allow, Principal: {Service: lambda.amazonaws.com}, Action: sts:AssumeRole}]
      Policies:
        - PolicyName: table
          PolicyDocument:
            Version: "2012-10-17"
            Statement: [{Effect: Allow, Action: [dynamodb:PutItem, dynamodb:GetItem], Resource: !GetAtt Table.Arn}]
  Fn:
    Type: AWS::Lambda::Function
    Properties:
      Runtime: python3.12
      Handler: app.handler
      Role: !GetAtt Role.Arn
      Code: {S3Bucket: !Ref ArtifactBucket, S3Key: !Ref ArtifactKey}
      Environment: {Variables: {TABLE: !Ref Table}}
Outputs:
  FunctionName: {Value: !Ref Fn}
"""
FILES = {"src/app.py": "def handler(event, context):\n    return {'statusCode': 200, 'body': 'ok'}\n",
         "README.md": "# Demo\n"}


@pytest.fixture
def bucket(aws, monkeypatch):
    monkeypatch.setattr(config, "ARTIFACT_BUCKET", "cloudops-artifacts-test")
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="cloudops-artifacts-test")
    with request_context.bind("t1", "alice", "s1"):
        yield "cloudops-artifacts-test"


class TestValidation:
    def test_valid_bundle(self):
        assert validate("demo-app", FILES, TEMPLATE) == []

    @pytest.mark.parametrize("files,template,msg", [
        (FILES, TEMPLATE.replace("AWS::DynamoDB::Table", "AWS::IAM::User"), "not allowed"),
        (FILES, TEMPLATE + "\n# arn:aws:iam::aws:policy/AdministratorAccess\n", "administrator"),
        (FILES, TEMPLATE.replace("[dynamodb:PutItem, dynamodb:GetItem]", '"*"'), 'Action "*"'),
        (FILES, TEMPLATE.replace("ArtifactKey: {Type: String}", ""), "ArtifactBucket and ArtifactKey"),
        ({"../evil.py": "x", **FILES}, TEMPLATE, "Unsafe file path"),
        (FILES, "not: [valid", "not valid YAML"),
    ])
    def test_rejections(self, files, template, msg):
        with pytest.raises(BundleError, match=msg):
            validate("demo-app", files, template)

    def test_bad_name(self):
        with pytest.raises(BundleError, match="app_id"):
            validate("Demo App", FILES, TEMPLATE)

    def test_code_zip_strips_src(self):
        names = zipfile.ZipFile(io.BytesIO(code_zip(FILES))).namelist()
        assert names == ["app.py"]


def draft_app(app_id="demo-app", files=FILES, template=TEMPLATE, description="demo"):
    for path, content in files.items():
        assert json.loads(abt.prepare_app_file(app_id=app_id, path=path, content=content))["status"] == "success"
    abt.prepare_app_template(app_id=app_id, template=template)
    return json.loads(abt.prepare_app_bundle(app_id=app_id, description=description))


class TestStoreAndTools:
    def test_versions_are_immutable_and_increment(self, bucket):
        store = AppBundleStore(boto3.client("s3"), bucket)
        assert store.save("t1", "demo-app", "v1", FILES, TEMPLATE, "alice")["version"] == 1
        changed = {**FILES, "src/app.py": FILES["src/app.py"] + "# v2\n"}
        assert store.save("t1", "demo-app", "v2", changed, TEMPLATE, "alice")["version"] == 2
        assert store.load("t1", "demo-app", 1)[1] == FILES
        assert store.load("t1", "demo-app")[0]["version"] == 2
        with pytest.raises(BundleError):
            store.load("t2", "demo-app")        # other tenants can't see it

    def test_prepare_validates_and_points_to_deploy(self, bucket):
        out = draft_app()
        assert out["status"] == "success", out
        assert out["version"] == 1 and "deploy_generated_app" in out["next_step"]

    def test_empty_draft_and_unsafe_paths_are_refused(self, bucket):
        assert json.loads(abt.prepare_app_bundle(app_id="nothing-here", description="d"))["error_type"] == "empty_draft"
        assert json.loads(abt.prepare_app_file(app_id="x-app", path="../etc/x", content="x"))["error_type"] == "invalid_path"
        assert json.loads(abt.prepare_app_file(app_id="x-app", path="./src/app.py", content="x"))["files"] == ["src/app.py"]

    def test_invalid_bundle_reports_problems(self, bucket):
        out = draft_app(template=TEMPLATE.replace("AWS::DynamoDB::Table", "AWS::IAM::User"))
        assert out["error_type"] == "invalid_bundle" and "not allowed" in out["error_message"]

    def test_drafts_are_isolated_per_request(self, bucket):
        with request_context.bind("t1", "alice", "a"):
            abt.prepare_app_file(app_id="iso-app", path="src/app.py", content="x")
        with request_context.bind("t1", "alice", "b"):
            assert json.loads(abt.prepare_app_bundle(app_id="iso-app", description="d"))["error_type"] == "empty_draft"

    def test_get_loads_draft_so_edits_only_send_changed_files(self, bucket):
        draft_app()
        with request_context.bind("t1", "alice", "next-turn"):
            out = json.loads(abt.get_generated_app(app_id="demo-app"))
            assert out["files"] == FILES and out["template"] == TEMPLATE
            abt.prepare_app_file(app_id="demo-app", path="src/app.py", content="# v2\n" + FILES["src/app.py"])
            v2 = json.loads(abt.prepare_app_bundle(app_id="demo-app", description="v2"))
        assert v2["version"] == 2 and v2["files"] == sorted(FILES)

    def test_deploy_creates_stack_with_code_then_delete(self, bucket, monkeypatch):
        monkeypatch.setattr(abt.time, "sleep", lambda s: None)
        draft_app()
        out = json.loads(abt.deploy_generated_app(app_id="demo-app", version=1))
        assert out["status"] == "success", out
        assert out["details"]["action"] == "created" and out["details"]["stack_name"] == "cloudops-app-demo-app"
        fn = out["details"]["outputs"]["FunctionName"]
        cfg = boto3.client("lambda", region_name="us-east-1").get_function(FunctionName=fn)["Configuration"]
        assert cfg["Handler"] == "app.handler"
        tags = boto3.client("cloudformation").describe_stacks(StackName="cloudops-app-demo-app")["Stacks"][0]["Tags"]
        assert {"Key": "cloudops:version", "Value": "1"} in tags

        deleted = json.loads(abt.delete_generated_app(app_id="demo-app"))
        assert deleted["status"] == "success"

    def test_smoke_test_reports_url_outputs(self, monkeypatch):
        class Resp:
            status = 200
            def read(self, n): return b"hello"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        monkeypatch.setattr(abt.urllib.request, "urlopen", lambda req, timeout: Resp())
        res = abt._smoke_test({"ApiUrl": "https://x.example.com/", "TableName": "t"})
        assert res == [{"output": "ApiUrl", "url": "https://x.example.com/", "status": 200, "body": "hello"}]


def test_gate_and_router_know_the_build_domain():
    from src.core.approvals import is_mutating
    from src.agents.router import DomainRouter
    assert not is_mutating("prepare_app_bundle") and not is_mutating("get_generated_app")
    assert is_mutating("deploy_generated_app") and is_mutating("delete_generated_app")
    assert "build" in DomainRouter.VALID_DOMAINS


def test_agent_builds_app_file_by_file_and_only_deploy_is_queued(bucket, tables):
    """A real Strands agent (scripted model) drives the whole build flow through the approval gate."""
    from strands import Agent
    from tests.test_core.test_approvals import ScriptedModel
    from src.core.approvals import ActionStore, ApprovalGate

    calls = [("prepare_app_file", {"app_id": "demo-app", "path": p, "content": c}) for p, c in FILES.items()]
    calls += [("prepare_app_template", {"app_id": "demo-app", "template": TEMPLATE}),
              ("prepare_app_bundle", {"app_id": "demo-app", "description": "demo"}),
              ("deploy_generated_app", {"app_id": "demo-app", "version": 1})]
    gate = ApprovalGate(ActionStore(region="us-east-1"), tenant_id="t1", session_id="s1",
                        requested_by="alice", account_id=None, region="us-east-1")
    tools = [abt.prepare_app_file, abt.prepare_app_template, abt.prepare_app_bundle, abt.deploy_generated_app]
    Agent(model=ScriptedModel(calls), tools=tools, hooks=[gate], callback_handler=None)("build it")

    assert [a["tool_name"] for a in gate.queued] == ["deploy_generated_app"]
    manifest, files, _ = AppBundleStore(boto3.client("s3"), bucket).load("t1", "demo-app", 1)
    assert files == FILES
    assert boto3.client("cloudformation").list_stacks()["StackSummaries"] == []   # nothing deployed yet


def test_malformed_tool_call_is_retried_once(monkeypatch):
    import src.agentcore_entrypoint as ep
    attempts = []

    class FlakyAgent:
        def __init__(self, system_prompt, **kw):
            self.system_prompt = system_prompt

        def __call__(self, prompt):
            attempts.append(self.system_prompt)
            if len(attempts) == 1:
                raise RuntimeError("modelStreamErrorException: Model produced invalid sequence as part of ToolUse")
            return "<thinking>hmm</thinking>Built it."
    monkeypatch.setattr(ep, "Agent", FlakyAgent)
    from unittest.mock import MagicMock
    text, _, _ = ep._run_agent_with_domains("build a game", ["build"], [], MagicMock())
    assert text == "Built it." and len(attempts) == 2 and "malformed" in attempts[1]
