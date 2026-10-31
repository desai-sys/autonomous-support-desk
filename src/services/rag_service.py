"""
RAG service for CloudOps platform.
"""

import boto3
from src.models.query import Query, Resolution
from config import KNOWLEDGE_BASE_ID, AWS_REGION


class RAGService:
    """Service for RAG operations."""
    
    def __init__(self):
        self.client = boto3.client('bedrock-agent-runtime', region_name=AWS_REGION)
        self.knowledge_base_id = KNOWLEDGE_BASE_ID

    def resolve(self, query: Query) -> Resolution:
        """Resolve query using RAG."""
        if self.knowledge_base_id == 'PLACEHOLDER':
            return Resolution(
                text="Knowledge Base not configured. Please complete the setup.",
                resolution_type="error",
                confidence=0.0,
                escalate=True
            )
        
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.knowledge_base_id,
                retrievalQuery={'text': query.text}
            )
            results = response.get('retrievalResults', [])

            if not results:
                return Resolution(
                    text="No known resolution found in history. Escalating to human SRE.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True
                )

            top = results[0]
            score = top.get('score', 0)
            top_text = top['content']['text']

            if score < 0.6:
                return Resolution(
                    text="No high-confidence resolution found in history. Escalating to human SRE.",
                    resolution_type="escalated",
                    confidence=0.0,
                    escalate=True
                )

            return Resolution(
                text=f"Based on historical resolutions:\n\n{top_text}",
                resolution_type="rag",
                confidence=score,
                escalate=False
            )
            
        except Exception as e:
            return Resolution(
                text=f"RAG service error: {str(e)}",
                resolution_type="error",
                confidence=0.0,
                escalate=True
            )