"""Tickets, knowledge-base lookups, the learning loop, the account registry, audit and usage."""
import json
from unittest.mock import MagicMock

import boto3
import pytest

import config
from src.core.accounts import AccountRegistry
from src.core.security import SecurityManager, User, UserRole, role_from_groups
from src.core.usage import UsageMeter
from src.models.query import Query
from src.services.rag_service import RAGService
from src.services.ticket_service import TicketService


class TestTickets:
    def test_open_tickets_are_scoped_to_tenant_and_sorted_by_urgency(self, tables):
        svc = TicketService()
        svc.create(tenant_id="t1", query_text="low one", category="X", urgency="low")
        svc.create(tenant_id="t1", query_text="critical one", category="X", urgency="critical")
        svc.create(tenant_id="t2", query_text="other tenant", category="X")
        assert [t["query_text"] for t in svc.list_open("t1")] == ["critical one", "low one"]

    def test_close_queues_for_vectorization_and_rejects_other_tenants(self, tables):
        svc = TicketService()
        ticket = svc.create(tenant_id="t1", query_text="disk full", category="X")
        assert not svc.close(ticket["ticket_id"], tenant_id="t2", resolution="r", resolved_by="x")
        assert svc.close(ticket["ticket_id"], tenant_id="t1", resolution="grew volume", resolved_by="bob")
        assert svc.list_open("t1") == []
        assert [t["ticket_id"] for t in svc.list_pending_vectorization()] == [ticket["ticket_id"]]
        svc.mark_vectorized(ticket["ticket_id"])
        assert svc.list_pending_vectorization() == []

    def test_close_requires_resolution(self, tables):
        svc = TicketService()
        ticket = svc.create(tenant_id="t1", query_text="q", category="X")
        with pytest.raises(ValueError):
            svc.close(ticket["ticket_id"], tenant_id="t1", resolution="  ", resolved_by="bob")


class TestLearningLoop:
    def test_uploads_resolution_and_tenant_metadata_then_ingests(self, tables, monkeypatch):
        from src.jobs import learning_loop
        monkeypatch.setattr(config, "S3_BUCKET_NAME", "kb-bucket")
        boto3.client("s3").create_bucket(Bucket="kb-bucket")
        svc = TicketService()
        ticket = svc.create(tenant_id="t1", query_text="disk full", category="Storage")
        svc.close(ticket["ticket_id"], tenant_id="t1", resolution="grew volume", resolved_by="bob")

        bedrock = MagicMock()
        bedrock.start_ingestion_job.return_value = {"ingestionJob": {"ingestionJobId": "job-1"}}
        real_client = boto3.client
        monkeypatch.setattr(learning_loop.boto3, "client",
                            lambda name, **kw: bedrock if name == "bedrock-agent" else real_client(name, **kw))

        result = learning_loop.sync_closed_tickets()
        assert result == {"uploaded": 1, "failed": 0, "ingestion_job_id": "job-1"}
        key = f"resolutions/t1/{ticket['ticket_id']}.txt"
        s3 = boto3.client("s3")
        assert b"grew volume" in s3.get_object(Bucket="kb-bucket", Key=key)["Body"].read()
        meta = json.loads(s3.get_object(Bucket="kb-bucket", Key=key + ".metadata.json")["Body"].read())
        assert meta["metadataAttributes"]["tenant_id"] == "t1"
        assert learning_loop.sync_closed_tickets()["uploaded"] == 0


class TestRag:
    def test_filters_by_tenant_and_score(self, monkeypatch):
        monkeypatch.setattr(config, "KNOWLEDGE_BASE_ID", "kb-1")
        svc = RAGService()
        svc.knowledge_base_id = "kb-1"
        svc.client = MagicMock()
        svc.client.retrieve.return_value = {"retrievalResults": [
            {"score": 0.9, "content": {"text": "restart nginx"}},
            {"score": 0.2, "content": {"text": "irrelevant"}},
        ]}
        res = svc.resolve(Query(text="502 errors", customer_id="t1"))
        assert not res.escalate and "restart nginx" in res.text and "irrelevant" not in res.text
        flt = svc.client.retrieve.call_args.kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
        assert flt == {"equals": {"key": "tenant_id", "value": "t1"}}

    def test_low_scores_escalate(self):
        svc = RAGService()
        svc.knowledge_base_id = "kb-1"
        svc.client = MagicMock()
        svc.client.retrieve.return_value = {"retrievalResults": [{"score": 0.1, "content": {"text": "x"}}]}
        assert svc.resolve(Query(text="q", customer_id="t1")).escalate

    def test_unconfigured(self):
        svc = RAGService()
        svc.knowledge_base_id = ""
        assert svc.resolve(Query(text="q", customer_id="t1")).resolution_type == "error"


class TestAccountsAuditUsage:
    def test_registry_merges_config_and_console_accounts(self, tables, monkeypatch):
        monkeypatch.setattr(config, "MANAGED_ACCOUNTS", {"111122223333": "prod"})
        reg = AccountRegistry()
        reg.add("4444-5555-6666", "dev", "alice")
        accounts = reg.list()
        assert accounts["111122223333"]["source"] == "config"
        assert accounts["444455556666"]["name"] == "dev"
        assert reg.is_allowed("444455556666") and not reg.is_allowed("777788889999")
        with pytest.raises(ValueError):
            reg.add("12345", "bad", "alice")
        reg.remove("444455556666")
        assert not reg.is_allowed("444455556666")

    def test_audit_is_queried_newest_first_per_tenant(self, tables):
        sec = SecurityManager()
        sec.log_audit_event("alice", "t1", "first", "r", "SUCCESS")
        sec.log_audit_event("bob", "t2", "other", "r", "SUCCESS")
        sec.log_audit_event("alice", "t1", "second", "r", "SUCCESS", details={"n": 1.5})
        assert [e["action"] for e in sec.get_audit_logs("t1")] == ["second", "first"]

    def test_audit_failure_never_raises(self):
        sec = SecurityManager()
        sec._audit_table = MagicMock()
        sec._audit_table.put_item.side_effect = RuntimeError("down")
        sec.log_audit_event("a", "t", "x", "r", "SUCCESS")

    def test_daily_limit(self, tables, monkeypatch):
        from src.core import usage
        monkeypatch.setattr(usage, "DAILY_REQUEST_LIMIT", 2)
        meter = UsageMeter()
        assert [meter.allow_request("t1") for _ in range(3)] == [True, True, False]
        assert meter.allow_request("t2")

    def test_roles(self):
        assert role_from_groups(["viewer", "sre"]) == UserRole.SRE
        assert role_from_groups([]) == UserRole.VIEWER
        viewer = User.build("v", "v@x", UserRole.VIEWER, "t")
        from src.core.security import Permission
        assert viewer.can(Permission.USE_AGENT) and not viewer.can(Permission.APPROVE_CHANGES)
