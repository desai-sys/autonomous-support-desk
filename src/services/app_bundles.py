"""
Generated application bundles (the app builder's drafts).

The agent writes an application as a set of files plus a CloudFormation
template. A bundle is validated, then stored as an immutable, numbered version
in the platform's artifact bucket under ``apps/<tenant>/<app_id>/v<N>/``:

    manifest.json   name, description, version, file list, author, time
    files.json      every file's content (shown on the approval card)
    template.yaml   the CloudFormation template
    code.zip        files under src/, zipped with src/ stripped (the Lambda package)

Nothing in a user's AWS account changes until the deploy action is approved.
"""

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import yaml

import config

APP_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,40}$")
MAX_FILES = 60
MAX_TOTAL_BYTES = 1_000_000
MAX_RESOURCES = 40
CODE_DIR = "src/"

# Resource types generated apps may never create.
FORBIDDEN_TYPES = (
    "AWS::IAM::User", "AWS::IAM::AccessKey", "AWS::IAM::Group", "AWS::IAM::UserToGroupAddition",
    "AWS::IAM::OIDCProvider", "AWS::IAM::SAMLProvider", "AWS::Organizations::", "AWS::SSO::",
    "AWS::IdentityStore::", "AWS::Account", "AWS::CloudTrail::", "AWS::GuardDuty::Detector",
    "AWS::Config::ConfigurationRecorder",
)
_ADMIN_POLICY_RE = re.compile(r"arn:aws:iam::aws:policy/(AdministratorAccess|IAMFullAccess|PowerUserAccess)")
_STAR_ACTION_RE = re.compile(r"""Action["']?\s*:\s*\[?\s*["']\*["']""")


class BundleError(ValueError):
    """The generated bundle is not acceptable; the message lists every problem."""


class _CfnLoader(yaml.SafeLoader):
    pass


# Accept CloudFormation short-form tags (!Ref, !Sub, !GetAtt, ...) when parsing.
_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: (
    loader.construct_scalar(node) if isinstance(node, yaml.ScalarNode)
    else loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node)))


def parse_template(template: str) -> dict:
    try:
        doc = yaml.load(template, Loader=_CfnLoader)
    except yaml.YAMLError as e:
        raise BundleError(f"Template is not valid YAML/JSON: {e}")
    if not isinstance(doc, dict) or not isinstance(doc.get("Resources"), dict) or not doc["Resources"]:
        raise BundleError("Template must be a CloudFormation document with at least one resource.")
    return doc


def validate(app_id: str, files: Dict[str, str], template: str) -> List[str]:
    """Raise BundleError listing every problem; return non-fatal warnings."""
    errors, warnings = [], []
    if not APP_ID_RE.match(app_id or ""):
        errors.append("app_id must be 3-41 lowercase letters, digits or hyphens, starting with a letter.")
    if not isinstance(files, dict) or not files:
        errors.append("files must be a non-empty object of {path: content}.")
        files = {}
    if len(files) > MAX_FILES:
        errors.append(f"At most {MAX_FILES} files.")
    total = 0
    for path, content in files.items():
        if not isinstance(content, str):
            errors.append(f"{path}: content must be text.")
            continue
        total += len(content.encode())
        if path.startswith("/") or "\\" in path or ".." in path.split("/") or not path.strip():
            errors.append(f"Unsafe file path: {path!r}")
    if total > MAX_TOTAL_BYTES:
        errors.append(f"Files total {total} bytes; the limit is {MAX_TOTAL_BYTES}.")
    if not any(p.startswith(CODE_DIR) for p in files):
        warnings.append("No files under src/, so there is no code package (infrastructure only).")

    try:
        doc = parse_template(template)
    except BundleError as e:
        errors.append(str(e))
        doc = {"Resources": {}}
    resources = doc.get("Resources", {})
    if len(resources) > MAX_RESOURCES:
        errors.append(f"Template has {len(resources)} resources; the limit is {MAX_RESOURCES}.")
    for name, res in resources.items():
        rtype = (res or {}).get("Type", "") if isinstance(res, dict) else ""
        if any(rtype == t or (t.endswith("::") and rtype.startswith(t)) for t in FORBIDDEN_TYPES):
            errors.append(f"Resource {name} ({rtype}) is not allowed in generated apps.")
    if _ADMIN_POLICY_RE.search(template):
        errors.append("Template attaches an administrator-level managed policy; use least privilege.")
    if _STAR_ACTION_RE.search(template):
        errors.append('Template grants Action "*"; list the specific actions the app needs.')
    params = doc.get("Parameters") or {}
    if any(p.startswith(CODE_DIR) for p in files) and not {"ArtifactBucket", "ArtifactKey"} <= set(params):
        errors.append("Template must declare String parameters ArtifactBucket and ArtifactKey and use "
                      "them as the Lambda code location.")
    if errors:
        raise BundleError("\n".join(f"- {e}" for e in errors))
    return warnings


def code_zip(files: Dict[str, str]) -> Optional[bytes]:
    """Zip files under src/ (with src/ stripped), deterministically. None if there are none."""
    code = {p[len(CODE_DIR):]: c for p, c in files.items() if p.startswith(CODE_DIR) and p != CODE_DIR}
    if not code:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(code):
            info = zipfile.ZipInfo(path, date_time=(2020, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, code[path])
    return buf.getvalue()


class AppBundleStore:
    """Versioned bundles in the artifact bucket. ``s3`` must use platform-account credentials."""

    def __init__(self, s3, bucket: str = None):
        self.s3 = s3
        self.bucket = bucket or config.ARTIFACT_BUCKET

    @staticmethod
    def _prefix(tenant: str, app_id: str, version: int) -> str:
        return f"apps/{tenant}/{app_id}/v{version}/"

    def latest_version(self, tenant: str, app_id: str) -> int:
        resp = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=f"apps/{tenant}/{app_id}/v",
                                       Delimiter="/")
        versions = [int(m.group(1)) for p in resp.get("CommonPrefixes", [])
                    if (m := re.search(r"/v(\d+)/$", p["Prefix"]))]
        return max(versions, default=0)

    def save(self, tenant: str, app_id: str, description: str, files: Dict[str, str],
             template: str, author: str) -> dict:
        warnings = validate(app_id, files, template)
        version = self.latest_version(tenant, app_id) + 1
        prefix = self._prefix(tenant, app_id, version)
        manifest = {
            "app_id": app_id, "version": version, "description": description[:2000],
            "files": sorted(files), "has_code": code_zip(files) is not None,
            "created_by": author, "created_at": datetime.now(timezone.utc).isoformat(),
            "warnings": warnings,
        }
        put = lambda key, body, ctype: self.s3.put_object(  # noqa: E731
            Bucket=self.bucket, Key=prefix + key, Body=body, ContentType=ctype,
            ServerSideEncryption="AES256")
        put("files.json", json.dumps(files).encode(), "application/json")
        put("template.yaml", template.encode(), "text/yaml")
        package = code_zip(files)
        if package:
            put("code.zip", package, "application/zip")
        put("manifest.json", json.dumps(manifest).encode(), "application/json")
        return manifest

    def _get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def load(self, tenant: str, app_id: str, version: int = 0) -> Tuple[dict, Dict[str, str], str]:
        """Return (manifest, files, template) for a version (0 = latest)."""
        version = int(version) or self.latest_version(tenant, app_id)
        if not version:
            raise BundleError(f"No app named {app_id!r}.")
        prefix = self._prefix(tenant, app_id, version)
        try:
            manifest = json.loads(self._get(prefix + "manifest.json"))
        except self.s3.exceptions.NoSuchKey:
            raise BundleError(f"{app_id} has no version {version}.")
        files = json.loads(self._get(prefix + "files.json"))
        template = self._get(prefix + "template.yaml").decode()
        return manifest, files, template

    def load_code(self, tenant: str, app_id: str, version: int) -> Optional[bytes]:
        try:
            return self._get(self._prefix(tenant, app_id, version) + "code.zip")
        except self.s3.exceptions.NoSuchKey:
            return None
