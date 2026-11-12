"""
Background jobs for slow console requests.

Agent turns and approved deployments can run for minutes, longer than
CloudFront lets a request wait. The API starts them as jobs and the browser
polls. Job state lives in DynamoDB (``JOBS_TABLE``, TTL ``expires_at``) so any
console task can answer a poll.

The work itself runs in the console task that accepted it. While it runs, that
task refreshes the job's heartbeat. If the task is replaced or crashes, the
heartbeat goes stale and the next poll (on any task) settles the job instead of
leaving the browser waiting: a registered ``recover`` function reports what
actually happened (for approvals, read from the actions table, because the
executor carries on without the console), otherwise the job fails with a clear
message.
"""

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

import boto3
import structlog

import config

logger = structlog.get_logger()

_TTL_SECONDS = 24 * 3600
_MAX_RESULT_BYTES = 350_000
HEARTBEAT_SECONDS = 15
STALE_SECONDS = 75
LOST_MESSAGE = ("The console restarted while this request was running, so its answer was lost. "
                "Please send it again. Any changes it had already proposed are waiting for approval.")

# Returns the job's result, or None if the outcome isn't known yet.
Recover = Callable[[dict, str], Optional[dict]]


class JobStore:
    def __init__(self, table_name: str = None, workers: int = 16, recover: Dict[str, Recover] = None):
        self.table = boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(
            table_name or config.JOBS_TABLE)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self.recover = recover or {}
        self._active: set = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._beat = threading.Thread(target=self._heartbeat_loop, name="job-heartbeat", daemon=True)
        self._beat.start()

    def shutdown(self) -> None:
        """Stop the heartbeat and wait for running jobs (used by tests and clean exits)."""
        self._stop.set()
        self.pool.shutdown(wait=True)

    def submit(self, owner: str, kind: str, fn: Callable[[], dict], meta: dict = None) -> str:
        job_id = uuid.uuid4().hex
        now = int(time.time())
        self.table.put_item(Item={
            "job_id": job_id, "owner": owner, "kind": kind, "status": "running",
            "meta": json.dumps(meta or {}), "created_at": now, "heartbeat_at": now,
            "expires_at": now + _TTL_SECONDS,
        })
        with self._lock:
            self._active.add(job_id)
        self.pool.submit(self._run, job_id, fn)
        return job_id

    def _run(self, job_id: str, fn: Callable[[], dict]) -> None:
        try:
            result, status = fn(), "succeeded"
        except Exception as e:  # surfaced to the browser
            logger.exception("job_failed", job_id=job_id)
            result, status = {"error": str(e)}, "failed"
        finally:
            with self._lock:
                self._active.discard(job_id)
        self._finish(job_id, status, result)

    def _finish(self, job_id: str, status: str, result: dict) -> bool:
        """Record the outcome once; False if the job was already settled."""
        body = json.dumps(result, default=str)
        if len(body) > _MAX_RESULT_BYTES:
            body = json.dumps({"error": "Result too large to display."})
            status = "failed"
        try:
            self.table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :s, #r = :r, finished_at = :t",
                ConditionExpression="#s = :running",
                ExpressionAttributeNames={"#s": "status", "#r": "result"},
                ExpressionAttributeValues={":s": status, ":r": body, ":t": int(time.time()),
                                           ":running": "running"},
            )
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            with self._lock:
                active = list(self._active)
            for job_id in active:
                try:
                    self.table.update_item(Key={"job_id": job_id},
                                           UpdateExpression="SET heartbeat_at = :t",
                                           ExpressionAttributeValues={":t": int(time.time())})
                except Exception as e:
                    logger.warning("job_heartbeat_failed", job_id=job_id, error=str(e))

    def get(self, job_id: str, owner: str) -> Optional[dict]:
        item = self.table.get_item(Key={"job_id": job_id}).get("Item")
        if not item or item.get("owner") != owner:
            return None
        if item["status"] == "running" and self._abandoned(item):
            item = self._settle(item)
        return {"job_id": job_id, "status": item["status"], "kind": item.get("kind"),
                "result": json.loads(item["result"]) if item.get("result") else None}

    def _abandoned(self, item: dict) -> bool:
        with self._lock:
            if item["job_id"] in self._active:        # running right here
                return False
        last = int(item.get("heartbeat_at") or item.get("created_at") or 0)
        return time.time() - last > STALE_SECONDS

    def _settle(self, item: dict) -> dict:
        """The task that ran this job is gone: report what happened, or fail it clearly."""
        recover = self.recover.get(str(item.get("kind")))
        status, result = "failed", {"error": LOST_MESSAGE}
        if recover:
            try:
                recovered = recover(json.loads(item.get("meta") or "{}"), item["owner"])
            except Exception as e:
                logger.warning("job_recovery_failed", job_id=item["job_id"], error=str(e))
                recovered = None
            if recovered is None:
                return item                             # still in progress elsewhere; keep polling
            status, result = "succeeded", recovered
        logger.warning("job_settled_after_restart", job_id=item["job_id"], status=status)
        self._finish(item["job_id"], status, result)
        return self.table.get_item(Key={"job_id": item["job_id"]}).get("Item") or item
