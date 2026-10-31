"""
Learning loop: add resolved tickets to the knowledge base.

Runs hourly as a scheduled ECS task (infra/app.yaml) and on demand from the
console. For each closed ticket not yet vectorized it writes the resolution
document plus a Bedrock metadata file (tenant_id, so retrieval can filter per
tenant) to the knowledge-base bucket, then starts one ingestion job.

    python -m src.jobs.learning_loop
"""

import json

import boto3
import structlog

import config
from src.core.monitoring import configure_logging, metrics
from src.services.ticket_service import TicketService

logger = structlog.get_logger()


def sync_closed_tickets() -> dict:
    tickets_svc = TicketService()
    s3 = boto3.client("s3", region_name=config.AWS_REGION)
    tickets = tickets_svc.list_pending_vectorization()
    uploaded, failed = 0, 0

    for ticket in tickets:
        key = f"resolutions/{ticket['tenant_id']}/{ticket['ticket_id']}.txt"
        body = (
            f"Issue: {ticket.get('query_text', '')}\n"
            f"Category: {ticket.get('category', 'General')}\n"
            f"Resolution: {ticket.get('resolution', '')}\n"
        )
        meta = {"metadataAttributes": {
            "tenant_id": ticket["tenant_id"],
            "ticket_id": ticket["ticket_id"],
            "category": ticket.get("category", "General"),
        }}
        try:
            s3.put_object(Bucket=config.S3_BUCKET_NAME, Key=key, Body=body.encode())
            s3.put_object(Bucket=config.S3_BUCKET_NAME, Key=f"{key}.metadata.json",
                          Body=json.dumps(meta).encode())
            tickets_svc.mark_vectorized(ticket["ticket_id"])
            uploaded += 1
        except Exception as e:
            failed += 1
            logger.error("ticket_sync_failed", ticket_id=ticket["ticket_id"], error=str(e))

    job_id = None
    if uploaded:
        job = boto3.client("bedrock-agent", region_name=config.AWS_REGION).start_ingestion_job(
            knowledgeBaseId=config.KNOWLEDGE_BASE_ID, dataSourceId=config.DATA_SOURCE_ID
        )
        job_id = job["ingestionJob"]["ingestionJobId"]

    metrics.put("TicketsVectorized", uploaded)
    result = {"uploaded": uploaded, "failed": failed, "ingestion_job_id": job_id}
    logger.info("learning_loop_finished", **result)
    return result


if __name__ == "__main__":
    configure_logging("learning-loop")
    sync_closed_tickets()
