"""
Learning Loop — syncs closed DynamoDB tickets to S3 and re-indexes the Bedrock Knowledge Base.

Run manually:
    python learning_loop.py

Or trigger from the SRE Portal (Admin role → "Trigger AI Knowledge Sync").
"""

import boto3
from src.services.dynamo_service import DynamoDBService
from config import S3_BUCKET_NAME, KNOWLEDGE_BASE_ID, DATA_SOURCE_ID


def sync_closed_tickets():
    """
    1. Scan DynamoDB for closed tickets where vectorized=False
    2. Upload each resolution as a .txt file to S3 (resolutions/ prefix)
    3. Mark each ticket as vectorized=True in DynamoDB
    4. Trigger a Bedrock Knowledge Base ingestion job to re-index
    """
    s3 = boto3.client('s3', region_name='us-east-1')
    bedrock_agent = boto3.client('bedrock-agent', region_name='us-east-1')
    dynamo = DynamoDBService()

    tickets = dynamo.get_unvectorized_closed_tickets()

    if not tickets:
        print("No new closed tickets to sync.")
        return

    print(f"Found {len(tickets)} closed ticket(s). Syncing to S3...")

    uploaded = 0
    for ticket in tickets:
        try:
            content = (
                f"Issue: {ticket.get('query_text', '')}\n"
                f"Category: {ticket.get('category', 'General')}\n"
                f"Resolution: {ticket.get('resolution', '')}\n"
                f"Resolved at: {ticket.get('updated_at', '')}"
            )
            s3.put_object(
                Bucket=S3_BUCKET_NAME,
                Key=f"resolutions/{ticket['ticket_id']}.txt",
                Body=content.encode('utf-8')
            )
            dynamo.mark_ticket_vectorized(ticket['ticket_id'])
            uploaded += 1
            print(f"  ✅ Uploaded ticket {ticket['ticket_id']}")
        except Exception as e:
            print(f"  ❌ Failed to upload ticket {ticket['ticket_id']}: {e}")

    if uploaded > 0:
        try:
            bedrock_agent.start_ingestion_job(
                knowledgeBaseId=KNOWLEDGE_BASE_ID,
                dataSourceId=DATA_SOURCE_ID
            )
            print(f"\n✅ Uploaded {uploaded} resolution(s) and triggered Bedrock ingestion job.")
        except Exception as e:
            print(f"\n⚠️  Uploaded {uploaded} resolution(s) but failed to trigger ingestion: {e}")
    else:
        print("No tickets were successfully uploaded.")


if __name__ == "__main__":
    sync_closed_tickets()
