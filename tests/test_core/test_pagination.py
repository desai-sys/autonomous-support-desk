"""Clients from the platform return complete listings, and say so when they can't."""
import json

import boto3 as raw_boto3
import pytest
from strands import Agent, tool

from src.core import aws_clients
from src.core.account_scope import AccountDirectory, scope_tools
from src.core.aws_clients import boto3
from tests.test_core.test_approvals import ScriptedModel


@pytest.fixture
def topics(aws):
    sns = boto3.client("sns", region_name="us-east-1")
    for i in range(230):                                   # SNS returns 100 topics per page
        sns.create_topic(Name=f"topic-{i:03d}")
    return sns


def test_plain_boto3_returns_one_page(topics):
    assert len(raw_boto3.client("sns", region_name="us-east-1").list_topics()["Topics"]) == 100


def test_lists_span_every_page(topics):
    result = topics.list_topics()
    assert len(result["Topics"]) == 230 and "NextToken" not in result


def test_explicit_limits_and_tokens_are_respected(aws):
    logs = boto3.client("logs", region_name="us-east-1")
    for i in range(130):                                   # CloudWatch Logs returns 50 groups per page
        logs.create_log_group(logGroupName=f"/group/{i:03d}")
    assert len(logs.describe_log_groups()["logGroups"]) == 130
    first = logs.describe_log_groups(limit=10)
    assert len(first["logGroups"]) == 10 and first["nextToken"]
    assert len(logs.describe_log_groups(nextToken=first["nextToken"], limit=10)["logGroups"]) == 10


def test_tools_now_see_everything(aws):
    from src.tools.aws_tools import list_log_groups
    logs = boto3.client("logs", region_name="us-east-1")
    for i in range(130):
        logs.create_log_group(logGroupName=f"/group/{i:03d}")
    out = list_log_groups()
    assert "/group/129" in out                              # used to stop at the first 50


def test_cap_is_reported_not_hidden(topics, monkeypatch):
    monkeypatch.setattr(aws_clients, "MAX_AUTO_PAGES", 2)
    with aws_clients.track_truncations() as truncated:
        result = topics.list_topics()
    assert len(result["Topics"]) == 200 and truncated == ["sns:list_topics"]
    assert result.get("NextToken")                          # a caller that pages can still continue


def test_data_reads_and_other_calls_pass_through(aws):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="paging-test-bucket")
    for i in range(5):
        s3.put_object(Bucket="paging-test-bucket", Key=f"k{i}", Body=b"x")
    assert s3.list_objects_v2(Bucket="paging-test-bucket", MaxKeys=2)["KeyCount"] == 2
    assert s3.get_paginator("list_objects_v2") and s3.meta.region_name == "us-east-1"
    with pytest.raises(s3.exceptions.NoSuchKey):
        s3.get_object(Bucket="paging-test-bucket", Key="missing")


@tool
def list_all_topics() -> str:
    """List SNS topics."""
    return json.dumps({"count": len(boto3.client("sns", region_name="us-east-1").list_topics()["Topics"])})


def test_the_model_is_told_when_a_listing_is_incomplete(topics, monkeypatch):
    monkeypatch.setattr(aws_clients, "MAX_AUTO_PAGES", 1)
    model = ScriptedModel([("list_all_topics", {})])
    Agent(model=model, tools=scope_tools([list_all_topics], AccountDirectory("999999999999", {})),
          callback_handler=None)("which topics exist?")
    texts = [c["text"] for r in model.tool_results for c in r["content"] if "text" in c]
    assert any('"count": 100' in t for t in texts)
    assert any("INCOMPLETE" in t and "sns:list_topics" in t for t in texts)
