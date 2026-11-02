"""S3 public-access checks follow S3's rules and never call an unchecked bucket private."""
import json

import boto3
import pytest
from botocore.exceptions import ClientError

from src.tools import storage_tools
from src.tools.storage_tools import get_s3_bucket_public_access_status, list_public_s3_buckets

ALL_ON = {k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}


def public_policy(bucket):
    return json.dumps({"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": "*", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*"}]})


@pytest.fixture
def s3(aws):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="plain")                                  # nothing configured
    client.create_bucket(Bucket="policy-public")
    client.put_bucket_policy(Bucket="policy-public", Policy=public_policy("policy-public"))
    client.create_bucket(Bucket="acl-public")
    client.put_bucket_acl(Bucket="acl-public", ACL="public-read")
    client.create_bucket(Bucket="acl-ignored")
    client.put_bucket_acl(Bucket="acl-ignored", ACL="public-read")
    client.put_public_access_block(Bucket="acl-ignored", PublicAccessBlockConfiguration={
        "BlockPublicAcls": False, "IgnorePublicAcls": True, "BlockPublicPolicy": False, "RestrictPublicBuckets": False})
    client.create_bucket(Bucket="locked")
    client.put_public_access_block(Bucket="locked", PublicAccessBlockConfiguration=ALL_ON)
    return client


def scan():
    return json.loads(list_public_s3_buckets())


def test_matrix(s3):
    out = scan()
    assert out["status"] == "success" and out["total_buckets"] == 5
    assert sorted(b["bucket"] for b in out["public_buckets"]) == ["acl-public", "policy-public"]
    assert out["not_public_count"] == 3 and out["unchecked_buckets"] == []
    reasons = {b["bucket"]: b["reasons"] for b in out["public_buckets"]}
    assert reasons["policy-public"] == ["bucket policy is public"]
    assert reasons["acl-public"] == ["ACL grants READ to AllUsers"]


def test_account_level_block_overrides_everything(s3):
    boto3.client("s3control", region_name="us-east-1").put_public_access_block(
        AccountId="123456789012", PublicAccessBlockConfiguration=ALL_ON)
    out = scan()
    assert out["public_buckets"] == [] and out["account_block_public_access"] == "all four settings on"
    assert out["summary"] == "None of the 5 buckets are publicly accessible."


def test_unreadable_bucket_is_reported_not_called_private(s3, monkeypatch):
    real_client = storage_tools.boto3.client

    def client(name, **kw):
        c = real_client(name, **kw)
        if name == "s3":
            original = c.get_bucket_policy_status

            def status(Bucket):
                if Bucket == "plain":
                    raise ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetBucketPolicyStatus")
                return original(Bucket=Bucket)
            c.get_bucket_policy_status = status
        return c
    monkeypatch.setattr(storage_tools.boto3, "client", client)
    out = scan()
    assert out["status"] == "incomplete" and "Do not report the unchecked buckets as private" in out["summary"]
    assert out["unchecked_buckets"] == [{"bucket": "plain", "errors": ["bucket policy status unreadable (AccessDenied)"]}]


def test_single_bucket_status(s3):
    assert json.loads(get_s3_bucket_public_access_status(bucket_name="policy-public"))["verdict"] == "PUBLIC"
    locked = json.loads(get_s3_bucket_public_access_status(bucket_name="locked"))
    assert locked["verdict"] == "NOT PUBLIC" and locked["block_public_access"]["effective"] == ALL_ON
    assert json.loads(get_s3_bucket_public_access_status(bucket_name="plain"))["verdict"] == "NOT PUBLIC"


def test_no_buckets(aws):
    assert scan()["summary"] == "No S3 buckets in this account."
