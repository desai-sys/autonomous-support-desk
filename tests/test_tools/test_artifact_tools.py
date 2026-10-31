"""
Tests for uploaded-zip deployment: ArtifactService (UI side), artifact_tools
(agent side), and the entrypoint's account / context handling.
"""
import importlib
import io
import json
import sys
import zipfile
from unittest.mock import MagicMock

import pytest

# Several legacy test modules replace boto3/botocore/structlog in sys.modules with
# MagicMocks at import time and never restore them. These tests run real boto3
# against moto, so drop those mocks and re-import the modules under test.
_MOCKED_ROOTS = [r for r in ("boto3", "botocore", "structlog")
                 if isinstance(sys.modules.get(r), MagicMock)]
if _MOCKED_ROOTS:
    _reimport = set(_MOCKED_ROOTS)
    if _reimport & {"boto3", "botocore"}:
        # Real submodules left behind would not attach to a fresh package import.
        _reimport |= {"boto3", "botocore", "s3transfer", "moto"}
    for _name in list(sys.modules):
        if _name.split(".")[0] in _reimport:
            del sys.modules[_name]
for _name in ("src.core.aws_clients", "src.tools.deployment_tools",
              "src.tools.artifact_tools", "src.services.artifact_service"):
    if _name in sys.modules:
        importlib.reload(sys.modules[_name])

import boto3  # noqa: E402
from moto import mock_aws  # noqa: E402

import config  # noqa: E402
from src.services.artifact_service import ArtifactService, ArtifactValidationError  # noqa: E402
from src.tools import artifact_tools  # noqa: E402


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


PY_LAMBDA = "import json\n\ndef lambda_handler(event, context):\n    return {'ok': True}\n"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws(config={"iam": {"load_aws_managed_policies": True}}):
        yield


@pytest.fixture
def uploaded(aws):
    """Upload a zip through ArtifactService and return its reference."""
    def _upload(files, filename="app.zip"):
        return ArtifactService().upload(make_zip(files), filename, "tenant-1", "alice")
    return _upload


# ── ArtifactService ───────────────────────────────────────────────────────────

class TestArtifactService:
    def test_upload_creates_private_bucket_and_returns_reference(self, uploaded):
        ref = uploaded({"app.py": PY_LAMBDA})
        assert ref["uri"].startswith(f"s3://{config.ARTIFACT_BUCKET}/uploads/tenant-1/")
        assert ref["uri"].endswith("/app.zip")
        assert ref["file_count"] == 1
        s3 = boto3.client("s3", region_name="us-east-1")
        block = s3.get_public_access_block(Bucket=config.ARTIFACT_BUCKET)
        assert all(block["PublicAccessBlockConfiguration"].values())

    @pytest.mark.parametrize("data,filename,message", [
        (b"not a zip", "x.zip", "not a valid zip"),
        (make_zip({"a.py": "x"}), "x.tar", "Only .zip"),
        (b"", "x.zip", "empty"),
        (make_zip({"../evil.sh": "x"}), "x.zip", "Unsafe path"),
    ])
    def test_rejects_bad_uploads(self, aws, data, filename, message):
        with pytest.raises(ArtifactValidationError, match=message):
            ArtifactService().validate(data, filename)


# ── Zip helpers ───────────────────────────────────────────────────────────────

class TestZipHelpers:
    def test_strips_single_wrapping_folder_and_os_metadata(self):
        raw = make_zip({
            "myapp/app.py": PY_LAMBDA,
            "myapp/lib/util.py": "x = 1",
            "__MACOSX/myapp/._app.py": "junk",
            "myapp/.DS_Store": "junk",
        })
        package, files, root = artifact_tools._normalize_zip(raw)
        assert root == "myapp/"
        assert sorted(files) == ["app.py", "lib/util.py"]
        with zipfile.ZipFile(io.BytesIO(package)) as zf:
            assert sorted(zf.namelist()) == ["app.py", "lib/util.py"]

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Unsafe path"):
            artifact_tools._normalize_zip(make_zip({"a/../../etc/passwd": "x"}))

    @pytest.mark.parametrize("files,expected", [
        ({"app.py": PY_LAMBDA}, ("python3.12", "app.lambda_handler")),
        ({"main.py": "def handler(e, c):\n    pass\n"}, ("python3.12", "main.handler")),
        ({"index.mjs": "export const handler = async () => 1;"}, ("nodejs20.x", "index.handler")),
        ({"index.js": "exports.handler = async () => 1;"}, ("nodejs20.x", "index.handler")),
        ({"server.py": "print('hi')"}, ("python3.12", "")),
        ({"index.html": "<html></html>"}, ("", "")),
    ])
    def test_detects_lambda_entry(self, files, expected):
        files = {k: v.encode() for k, v in files.items()}
        assert artifact_tools._detect_lambda_entry(files) == expected

    def test_rejects_uri_outside_artifact_bucket(self):
        with pytest.raises(ValueError, match="upload bucket"):
            artifact_tools._parse_artifact_uri("s3://someone-elses-bucket/secrets.zip")


# ── Agent tools ───────────────────────────────────────────────────────────────

class TestInspect:
    def test_recommends_lambda_and_warns_about_unvendored_deps(self, uploaded):
        ref = uploaded({"app/app.py": PY_LAMBDA, "app/requirements.txt": "requests"})
        result = json.loads(artifact_tools.inspect_uploaded_artifact(artifact_uri=ref["uri"]))
        assert result["status"] == "success"
        assert result["detected"]["lambda_handler"] == "app.lambda_handler"
        assert result["stripped_top_level_folder"] == "app"
        assert "lambda" in result["recommended_targets"]
        assert any("requirements.txt" in w for w in result["warnings"])

    def test_dockerfile_recommends_ecs(self, uploaded):
        ref = uploaded({"Dockerfile": "FROM nginx", "index.html": "<html/>"})
        result = json.loads(artifact_tools.inspect_uploaded_artifact(artifact_uri=ref["uri"]))
        assert result["recommended_targets"][0] == "ecs"

    def test_missing_artifact_reports_error(self, aws):
        result = json.loads(artifact_tools.inspect_uploaded_artifact(
            artifact_uri=f"s3://{config.ARTIFACT_BUCKET}/uploads/nope.zip"))
        assert result["status"] == "failed"


class TestDeployLambda:
    def test_creates_function_and_execution_role(self, uploaded):
        ref = uploaded({"app.py": PY_LAMBDA})
        result = json.loads(artifact_tools.deploy_artifact_to_lambda(
            artifact_uri=ref["uri"], function_name="hello-fn"))
        assert result["status"] == "success", result
        assert result["details"]["action"] == "created"
        assert result["details"]["handler"] == "app.lambda_handler"
        boto3.client("iam").get_role(RoleName=artifact_tools.LAMBDA_ROLE_NAME)
        fn = boto3.client("lambda", region_name="us-east-1").get_function(FunctionName="hello-fn")
        assert fn["Configuration"]["Runtime"] == "python3.12"

    def test_updates_existing_function(self, uploaded):
        first = uploaded({"app.py": PY_LAMBDA})
        artifact_tools.deploy_artifact_to_lambda(artifact_uri=first["uri"], function_name="hello-fn")
        second = uploaded({"app.py": PY_LAMBDA + "\n# v2\n"})
        result = json.loads(artifact_tools.deploy_artifact_to_lambda(
            artifact_uri=second["uri"], function_name="hello-fn", memory_mb=512))
        assert result["status"] == "success", result
        assert result["details"]["action"] == "updated"

    def test_requires_handler_when_undetectable(self, uploaded):
        ref = uploaded({"index.html": "<html/>"})
        result = json.loads(artifact_tools.deploy_artifact_to_lambda(artifact_uri=ref["uri"]))
        assert result["status"] == "failed"
        assert "runtime and handler" in result["error_message"]


class TestDeployEc2:
    def test_rejects_instance_not_registered_in_ssm(self, uploaded, monkeypatch):
        ref = uploaded({"app.py": "print(1)"})
        ssm = MagicMock()
        ssm.describe_instance_information.return_value = {"InstanceInformationList": []}
        real_client = artifact_tools.boto3.client
        monkeypatch.setattr(
            artifact_tools.boto3, "client",
            lambda name, **kw: ssm if name == "ssm" else real_client(name, **kw),
        )
        result = json.loads(artifact_tools.deploy_artifact_to_ec2(
            artifact_uri=ref["uri"], instance_id="i-0123456789abcdef0"))
        assert result["error_type"] == "ssm_not_ready"

    def test_rejects_malformed_instance_id(self, uploaded):
        ref = uploaded({"app.py": "print(1)"})
        result = json.loads(artifact_tools.deploy_artifact_to_ec2(
            artifact_uri=ref["uri"], instance_id="i-123; rm -rf /"))
        assert result["error_type"] == "invalid_payload"


class TestDeployEcsAndStatic:
    def test_ecs_requires_dockerfile(self, uploaded):
        ref = uploaded({"app.py": "print(1)"})
        result = json.loads(artifact_tools.deploy_artifact_to_ecs(artifact_uri=ref["uri"]))
        assert "Dockerfile" in result["error_message"]

    def test_ecs_reports_missing_roles(self, uploaded):
        ref = uploaded({"Dockerfile": "FROM nginx"})
        result = json.loads(artifact_tools.deploy_artifact_to_ecs(artifact_uri=ref["uri"]))
        assert result["error_type"] == "missing_prerequisite"

    def test_static_site_uploads_files_with_content_types(self, uploaded):
        ref = uploaded({"site/index.html": "<html/>", "site/css/app.css": "body{}"})
        result = json.loads(artifact_tools.deploy_artifact_to_static_site(
            artifact_uri=ref["uri"], bucket_name="my-site-bucket"))
        assert result["status"] == "success", result
        obj = boto3.client("s3").head_object(Bucket="my-site-bucket", Key="css/app.css")
        assert obj["ContentType"] == "text/css"


# ── Entrypoint account selection and context ─────────────────────────────────

class TestEntrypointTargeting:
    @pytest.fixture(autouse=True)
    def entrypoint(self):
        import src.agentcore_entrypoint as ep
        self.ep = ep

    def test_explicit_empty_list_pins_base_account(self):
        payload = {"account_ids": [], "user_message": "check 111122223333"}
        assert self.ep._extract_target_accounts(payload, "check 111122223333") == []

    def test_explicit_accounts_win(self):
        payload = {"account_ids": ["111122223333", "444455556666"]}
        assert self.ep._extract_target_accounts(payload, "anything 777788889999") == [
            "111122223333", "444455556666"]

    def test_history_is_not_scanned(self):
        prompt = ("Conversation so far:\nAssistant: role arn:aws:iam::999988887777:role/x\n\n"
                  "User: yes go ahead")
        payload = {"user_message": "yes go ahead"}
        assert self.ep._extract_target_accounts(payload, prompt) == []

    def test_latest_message_accounts_still_work_without_explicit_list(self):
        payload = {"user_message": "list buckets in 111122223333"}
        assert self.ep._extract_target_accounts(payload, "") == ["111122223333"]

    def test_context_notes_include_artifact_and_region(self):
        notes = self.ep._build_context_notes(
            {"uri": "s3://b/uploads/t/x/app.zip", "filename": "app.zip", "target": "lambda",
             "options": {"function_name": "f"}},
            "eu-west-1",
        )
        joined = "\n".join(notes)
        assert "region='eu-west-1'" in joined
        assert "artifact_uri: s3://b/uploads/t/x/app.zip" in joined
        assert "requested target: lambda" in joined

    def test_artifact_forces_deployment_tools(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(self.ep.router, "route", lambda *a, **k: ["storage"])

        def fake_run(prompt, domains, history=None, context_notes=None):
            captured["domains"] = domains
            return "ok", domains, 1
        monkeypatch.setattr(self.ep, "_run_agent_with_domains", fake_run)
        self.ep._resolve_query("deploy it", "auto", [], [], has_artifact=True)
        assert captured["domains"] == ["storage", "deployment"]


class TestEc2Script:
    """Run the exact shell script sent through SSM locally, with a file:// URL."""

    def test_script_downloads_extracts_and_runs(self, uploaded, monkeypatch, tmp_path):
        import shutil
        import subprocess
        if not shutil.which("bash") or not shutil.which("curl"):
            pytest.skip("needs bash and curl")

        raw = make_zip({"myapp/run.sh": "echo started > out.txt\n"})
        ref = ArtifactService().upload(raw, "myapp.zip", "tenant-1", "alice")
        local_zip = tmp_path / "src.zip"
        local_zip.write_bytes(raw)

        ssm = MagicMock()
        ssm.describe_instance_information.return_value = {
            "InstanceInformationList": [{"PingStatus": "Online", "PlatformType": "Linux"}]}
        ssm.send_command.return_value = {"Command": {"CommandId": "cmd-1"}}
        ssm.get_command_invocation.return_value = {"Status": "Success", "StandardOutputContent": "ok"}
        real_client = artifact_tools.boto3.client
        monkeypatch.setattr(
            artifact_tools.boto3, "client",
            lambda name, **kw: ssm if name == "ssm" else real_client(name, **kw),
        )
        monkeypatch.setattr(artifact_tools.time, "sleep", lambda s: None)

        dest = tmp_path / "deploy"
        result = json.loads(artifact_tools.deploy_artifact_to_ec2(
            artifact_uri=ref["uri"], instance_id="i-0123456789abcdef0",
            deploy_path=str(dest), run_command="bash run.sh", run_in_background=False))
        assert result["status"] == "success", result
        assert result["details"]["deployment_path"] == f"{dest}/myapp"

        commands = ssm.send_command.call_args.kwargs["Parameters"]["commands"]
        script = "\n".join(commands)
        presigned = script.split("curl -fsSL -o \"$TMP\" ", 1)[1].split(";", 1)[0]
        script = script.replace(presigned, f"file://{local_zip}")
        subprocess.run(["bash", "-c", script], check=True, capture_output=True)
        assert (dest / "myapp" / "out.txt").read_text().strip() == "started"


class TestCrossAccount:
    def test_lambda_lands_in_target_account_while_zip_is_read_from_base(self, uploaded):
        from src.core import aws_clients
        aws_clients._creds_cache.clear()
        ref = uploaded({"app.py": PY_LAMBDA})        # staged in the base account (moto default)
        target = "111111111111"

        with aws_clients.use_account(target):
            result = json.loads(artifact_tools.deploy_artifact_to_lambda(
                artifact_uri=ref["uri"], function_name="xacct-fn"))
        assert result["status"] == "success", result
        assert result["account_id"] == target
        assert f":{target}:function:xacct-fn" in result["resource_arn"]

        # The base account must not have received the function.
        base_lambda = boto3.client("lambda", region_name="us-east-1")
        assert "xacct-fn" not in [f["FunctionName"] for f in base_lambda.list_functions()["Functions"]]
