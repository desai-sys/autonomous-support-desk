"""Work that outlives the process that started it must end in a clear state, never hang or re-run."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import config
from src.core import approvals
from src.core.approvals import ActionStore, execute_actions, recorded_results


@pytest.fixture
def store(tables):
    return ActionStore(region="us-east-1")


def _action(store, **kw):
    return store.create(tenant_id="t1", session_id="s1", requested_by="alice", account_id=None,
                        region="us-east-1", tool_name="stop", tool_input={"instance_id": "i-1"}, **kw)


def test_an_action_stuck_executing_becomes_interrupted_and_is_never_rerun(store):
    ran = []
    action = _action(store)
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=config.EXECUTION_TIMEOUT_SECONDS + 60)).isoformat()
    assert store.transition(action["action_id"], "pending", "executing", approved_by="bob", approved_at=long_ago)

    current = store.get(action["action_id"])                 # the executor died mid-run
    assert current["status"] == "interrupted" and "unknown whether" in current["result"]
    results = execute_actions([action["action_id"]], tenant_id="t1", approver="bob", approver_role="sre",
                              tools={"stop": lambda **kw: ran.append(kw)}, store=store)
    assert results[0]["status"] == "error" and "already interrupted" in results[0]["error"] and ran == []


def test_an_action_executing_recently_is_left_alone(store):
    action = _action(store)
    store.transition(action["action_id"], "pending", "executing", approved_by="bob",
                     approved_at=datetime.now(timezone.utc).isoformat())
    assert store.get(action["action_id"])["status"] == "executing"
    assert recorded_results([action["action_id"]], tenant_id="t1", store=store) is None


def test_recorded_results_report_what_happened(store):
    done, pending = _action(store), _action(store)
    execute_actions([done["action_id"]], tenant_id="t1", approver="bob", approver_role="sre",
                    tools={"stop": lambda **kw: json.dumps({"status": "success", "message": "stopped"})},
                    store=store)
    results = recorded_results([done["action_id"], pending["action_id"], "missing"], tenant_id="t1", store=store)
    assert [r["status"] for r in results] == ["succeeded", "skipped", "error"]
    text = approvals.describe_results(results, lambda a: "the platform account")
    assert "✅" in text and "stopped" in text and "⏭️" in text
    assert recorded_results([done["action_id"]], tenant_id="other", store=store)[0]["status"] == "error"


# ── Console jobs ─────────────────────────────────────────────────────────────

@pytest.fixture
def jobs(tables):
    """Factory for job stores (each stands for one console task); `hold` is a job that never finishes."""
    import threading
    from console.jobs import JobStore
    release, made = threading.Event(), []

    def factory(**kw):
        made.append(JobStore(**kw))
        return made[-1]
    factory.hold = lambda: release.wait(60) or {}
    yield factory
    release.set()
    for store in made:
        store.shutdown()


def _wait(store, job_id, owner="alice"):
    for _ in range(100):
        job = store.get(job_id, owner)
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_a_job_lost_with_its_console_task_fails_clearly(jobs, monkeypatch):
    from console import jobs as module
    first = jobs()
    job_id = first.submit("alice", "chat", jobs.hold)
    assert first.get(job_id, "alice")["status"] == "running"       # running in this task: not abandoned

    survivor = jobs()                                              # another console task answers the poll
    assert survivor.get(job_id, "alice")["status"] == "running"    # heartbeat still fresh
    monkeypatch.setattr(module, "STALE_SECONDS", -1)               # ...then the first task's heartbeat stops
    lost = survivor.get(job_id, "alice")
    assert lost["status"] == "failed" and "restarted" in lost["result"]["error"]
    assert survivor.get(job_id, "mallory") is None


def test_a_lost_approval_reports_what_the_executor_did(jobs, store, monkeypatch):
    from console import jobs as module
    action = _action(store)
    execute_actions([action["action_id"]], tenant_id="t1", approver="bob", approver_role="sre",
                    tools={"stop": lambda **kw: json.dumps({"status": "success", "message": "stopped"})},
                    store=store)

    def recover(meta, owner):
        results = recorded_results(meta["action_ids"], tenant_id=meta["tenant_id"], store=store)
        return None if results is None else {"results": results, "recovered": True}

    first = jobs()
    job_id = first.submit("alice", "approve", jobs.hold,
                          meta={"action_ids": [action["action_id"]], "tenant_id": "t1"})
    monkeypatch.setattr(module, "STALE_SECONDS", -1)
    recovered = jobs(recover={"approve": recover}).get(job_id, "alice")
    assert recovered["status"] == "succeeded" and recovered["result"]["recovered"]
    assert recovered["result"]["results"][0]["status"] == "succeeded"


def test_a_lost_approval_keeps_waiting_while_the_executor_is_still_running(jobs, monkeypatch):
    from console import jobs as module
    first = jobs()
    job_id = first.submit("alice", "approve", jobs.hold, meta={"action_ids": ["a"]})
    monkeypatch.setattr(module, "STALE_SECONDS", -1)
    assert jobs(recover={"approve": lambda meta, owner: None}).get(job_id, "alice")["status"] == "running"


def test_a_finished_job_is_not_overwritten(jobs, monkeypatch):
    from console import jobs as module
    store = jobs()
    job_id = store.submit("alice", "chat", lambda: {"text": "done"})
    assert _wait(store, job_id)["result"] == {"text": "done"}
    monkeypatch.setattr(module, "STALE_SECONDS", -1)
    assert jobs().get(job_id, "alice")["result"] == {"text": "done"}
