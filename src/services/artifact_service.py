"""
Artifact Service — stages operator-uploaded code zips in S3 for the agent.

The Command Center uploads the zip here; the agent's deploy tools
(src/tools/artifact_tools.py) read it back by its s3:// URI. Uploads are
private, encrypted, and expire via a bucket lifecycle rule.
"""

import hashlib
import io
import re
import uuid
import zipfile
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

import config

ARTIFACT_RETENTION_DAYS = 7
MAX_ZIP_ENTRIES = 20000


class ArtifactValidationError(ValueError):
    """The uploaded file is not an acceptable code zip."""


class ArtifactService:
    def __init__(self):
        self.region = config.AWS_REGION
        self.bucket = config.ARTIFACT_BUCKET
        self.prefix = getattr(config, "ARTIFACT_PREFIX", "uploads/")
        self.max_bytes = getattr(config, "MAX_ARTIFACT_MB", 200) * 1024 * 1024
        self.s3 = boto3.client("s3", region_name=self.region)
        self._bucket_ready = False

    def validate(self, data: bytes, filename: str) -> dict:
        """Check the upload is a safe, non-empty zip. Returns a short summary."""
        if not filename.lower().endswith(".zip"):
            raise ArtifactValidationError("Only .zip files are supported.")
        if not data:
            raise ArtifactValidationError("The file is empty.")
        if len(data) > self.max_bytes:
            raise ArtifactValidationError(
                f"The file is larger than the {self.max_bytes // (1024 * 1024)} MB limit."
            )
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ArtifactValidationError("The file is not a valid zip archive.")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if not infos:
                raise ArtifactValidationError("The zip contains no files.")
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ArtifactValidationError(f"The zip has more than {MAX_ZIP_ENTRIES} files.")
            for info in infos:
                name = info.filename
                if name.startswith("/") or ".." in name.split("/") or "\\" in name:
                    raise ArtifactValidationError(f"Unsafe path in zip: {name}")
            bad = zf.testzip()
            if bad:
                raise ArtifactValidationError(f"Corrupt entry in zip: {bad}")
            names = [i.filename for i in infos]

        return {
            "file_count": len(names),
            "unzipped_size_bytes": sum(i.file_size for i in infos),
            "sample_files": sorted(names)[:15],
        }

    def upload(self, data: bytes, filename: str, tenant_id: str, user_id: str) -> dict:
        """Validate and upload a zip. Returns the artifact reference passed to the agent."""
        summary = self.validate(data, filename)
        self._ensure_bucket()

        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", filename)[-100:]
        safe_tenant = re.sub(r"[^A-Za-z0-9_.-]", "-", tenant_id)[:64]
        key = f"{self.prefix}{safe_tenant}/{uuid.uuid4().hex}/{safe_name}"
        sha256 = hashlib.sha256(data).hexdigest()

        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/zip",
            ServerSideEncryption="AES256",
            Metadata={"uploaded-by": user_id[:128], "tenant-id": safe_tenant, "sha256": sha256},
        )

        return {
            "uri": f"s3://{self.bucket}/{key}",
            "filename": filename,
            "size_bytes": len(data),
            "sha256": sha256,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            **summary,
        }

    def _ensure_bucket(self):
        """Create the private upload bucket (with expiry lifecycle) on first use."""
        if self._bucket_ready:
            return
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
                raise
            params = {"Bucket": self.bucket}
            if self.region != "us-east-1":
                params["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
            self.s3.create_bucket(**params)
            self.s3.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            self.s3.put_bucket_lifecycle_configuration(
                Bucket=self.bucket,
                LifecycleConfiguration={"Rules": [{
                    "ID": "expire-uploads",
                    "Status": "Enabled",
                    "Filter": {"Prefix": self.prefix},
                    "Expiration": {"Days": ARTIFACT_RETENTION_DAYS},
                }]},
            )
        self._bucket_ready = True
