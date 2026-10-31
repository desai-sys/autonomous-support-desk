"""
Knowledge-base lookups over resolved tickets (Bedrock Knowledge Base).

Every document carries a ``tenant_id`` metadata attribute (written by the
learning loop) and every query filters on it, so tenants never see each
other's resolutions.
"""

import boto3
import structlog

import config
from src.models.query import Query, Resolution

logger = structlog.get_logger()


class RAGService:
    def __init__(self):
        self.client = boto3.client("bedrock-agent-runtime", region_name=config.AWS_REGION)
        self.knowledge_base_id = config.KNOWLEDGE_BASE_ID

    def resolve(self, query: Query) -> Resolution:
        if not self.knowledge_base_id:
            return Resolution(text="The knowledge base is not configured.",
                              resolution_type="error", confidence=0.0, escalate=True)
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={"text": query.text},
                retrievalConfiguration={"vectorSearchConfiguration": {
                    "numberOfResults": 3,
                    "filter": {"equals": {"key": "tenant_id", "value": query.customer_id}},
                }},
            )
        except Exception as e:
            logger.error("rag_retrieve_failed", error=str(e))
            return Resolution(text=f"Knowledge base error: {e}", resolution_type="error",
                              confidence=0.0, escalate=True)

        results = [r for r in response.get("retrievalResults", [])
                   if r.get("score", 0) >= config.RAG_MIN_SCORE]
        if not results:
            return Resolution(text="No matching resolution found in past tickets.",
                              resolution_type="escalated", confidence=0.0, escalate=True)

        best = results[0]
        parts = [r["content"]["text"].strip() for r in results]
        return Resolution(
            text="Based on past resolutions:\n\n" + "\n\n---\n\n".join(parts),
            resolution_type="rag",
            confidence=min(1.0, float(best.get("score", 0))),
            escalate=False,
        )
