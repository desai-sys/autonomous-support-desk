"""
Background jobs for slow console requests.

Agent turns and approved deployments can run for minutes, longer than
CloudFront lets a request wait. The API starts them as jobs and the browser
polls. Job state lives in DynamoDB (``JOBS_TABLE``, TTL ``expires_at``) so any
console task can answer a poll, even after the one that started it restarts.
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import boto3
import structlog

import config

logger = structlog.get_logger()

_TTL_SECONDS = 24 * 3600
_MAX_RESULT_BYTES = 350_000


class JobStore:
    def __init__(self, table_name: str = None, workers: int = 16):
        self.table = boto3.resource("dynamodb", region_name=config.AWS_REGION).Table(
            table_name or config.JOBS_TABLE)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit(self, owner: str, kind: str, fn: Callable[[], dict]) -> str:
        job_id = uuid.uuid4().hex
        self.table.put_item(Item={
            "job_id": job_id, "owner": owner, "kind": kind, "status": "running",
            "created_at": int(time.time()), "expires_at": int(time.time()) + _TTL_SECONDS,
        })
        self.pool.submit(self._run, job_id, fn)
        return job_id

    def _run(self, job_id: str, fn: Callable[[], dict]) -> None:
        try:
            result, status = fn(), "succeeded"
        except Exception as e:  # surfaced to the browser
            logger.exception("job_failed", job_id=job_id)
            result, status = {"error": str(e)}, "failed"
        body = json.dumps(result, default=str)
        if len(body) > _MAX_RESULT_BYTES:
            body = json.dumps({"error": "Result too large to display."})
            status = "failed"
        self.table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, #r = :r, finished_at = :t",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
            ExpressionAttributeValues={":s": status, ":r": body, ":t": int(time.time())},
        )

    def get(self, job_id: str, owner: str) -> Optional[dict]:
        item = self.table.get_item(Key={"job_id": job_id}).get("Item")
        if not item or item.get("owner") != owner:
            return None
        return {"job_id": job_id, "status": item["status"], "kind": item.get("kind"),
                "result": json.loads(item["result"]) if item.get("result") else None}
