"""
Domain Router - LLM-based query classifier with multi-domain support.

Uses Nova Micro (fast, cheap) to classify user queries into 1 or 2 AWS domains.
Supports cross-service questions like "Lambda writing to S3" or "RDS security group".
"""

import boto3
from typing import List, Optional


class DomainRouter:
    """
    Routes user queries to the correct specialist agent domain(s) using Nova Micro.

    Returns 1 domain for single-service queries, 2 domains for cross-service queries.
    Tool groups from both domains are merged and deduplicated in the entrypoint.
    """

    VALID_DOMAINS = {"compute", "storage", "network", "database", "security", "ops", "deployment"}

    CLASSIFIER_PROMPT = """You are an AWS query router. Given a user message, classify it into 1 or 2 domains.

Domains:
  compute    - EC2, Lambda, ECS, EKS, Batch, SageMaker, App Runner, instances, functions, containers
  storage    - S3, EBS, EFS, ECR, Backup, DataSync, buckets, volumes, snapshots, objects
  network    - VPC, security groups, load balancers, Route53, CloudFront, API Gateway, SNS, SQS, Kinesis
  database   - RDS, DynamoDB, ElastiCache, DocumentDB, Redshift, Neptune, OpenSearch, Glue, Athena
  security   - IAM, GuardDuty, WAF, KMS, Secrets Manager, CloudTrail, Inspector, Macie, Config, MFA
  ops        - costs, billing, budgets, CodePipeline, SSM, Step Functions, CloudWatch
  deployment - deploy, ship, push, release, launch code, run uploaded code/zip, rollback, update function, deploy container, static site, CloudFormation stack deploy, SAM deploy, code deployment, CI/CD

Rules:
- Return 1 word if the query clearly belongs to one domain.
- Return 2 words (space-separated) if the query spans two domains. Examples:
    "Lambda writing to S3"         -> compute storage
    "security group of my RDS"     -> database network
    "IAM role for Lambda"          -> security compute
    "S3 bucket policy"             -> storage security
    "check EC2 and its EBS"        -> compute storage
    "API Gateway to Lambda"        -> network compute
    "CloudFront for my S3 bucket"  -> network storage
    "cost of my RDS instances"     -> ops database
    "deploy my Lambda function"    -> deployment compute
    "deploy to EC2"                -> deployment compute
    "deploy to ECS"                -> deployment compute
    "ship code to S3 bucket"       -> deployment storage
    "deploy CloudFormation stack"  -> deployment ops
    "rollback my Lambda"           -> deployment compute
    "run this zip on EC2"          -> deployment compute
    "deploy the uploaded code"     -> deployment
- For costs/billing/spending, always include: ops
- For IAM/permissions/credentials, always include: security
- For deploy/ship/push/release/launch code/run uploaded code/rollback, always include: deployment
- For general/ambiguous queries ("hello", "what can you do"), return: ops
- Use conversation history to resolve follow-ups.
- Respond with ONLY 1 or 2 domain words. No punctuation, no explanation."""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def route(self, query: str, history: Optional[list] = None) -> List[str]:
        """
        Classify a user query into 1 or 2 AWS domains.

        Args:
            query: The user's message/question.
            history: Optional conversation history for follow-up context.

        Returns:
            List of 1-2 domain strings, e.g. ["compute"] or ["compute", "storage"]
        """
        try:
            user_text = query
            if history:
                recent = history[-4:]
                context = "\n".join(str(turn) for turn in recent)
                user_text = f"Conversation so far:\n{context}\n\nNew message: {query}"

            response = self.client.converse(
                modelId="us.amazon.nova-micro-v1:0",
                system=[{"text": self.CLASSIFIER_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user_text}]}],
                inferenceConfig={"maxTokens": 10, "temperature": 0.0},
            )

            raw = (
                response["output"]["message"]["content"][0]["text"]
                .strip()
                .lower()
            )

            # Parse 1-2 valid domain words
            domains = [w for w in raw.split() if w in self.VALID_DOMAINS]

            if not domains:
                return ["compute"]

            # Cap at 2 to keep tool count manageable
            return domains[:2]

        except Exception:
            return ["compute"]
