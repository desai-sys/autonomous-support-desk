# Technology Stack

- Python 3.12; Strands Agents on Amazon Bedrock AgentCore Runtime (code deployment from S3, arm64)
- Models: Amazon Nova Micro (router), Nova Pro (agent), Titan Text Embeddings V2 (knowledge base)
- Bedrock Knowledge Base on S3 Vectors; DynamoDB; S3; Secrets Manager
- Console: React + TypeScript + Tailwind (Vite) served by FastAPI; Cognito OIDC via Authlib; ECS Fargate arm64, ALB, CloudFront, WAF
- Observability: structlog JSON logs, CloudWatch EMF metrics, alarms, dashboard
- IaC: CloudFormation (infra/), deployed by scripts/deploy.py; console image built by CodeBuild

## Commands
- Install: `pip install -r requirements/dev.txt`
- Test: `pytest -q` and `cfn-lint infra/*.yaml`
- Deploy/update: `python scripts/deploy.py --region us-east-1 --admin-email you@example.com`
- Local: `python agent_main.py`, `AUTH_MODE=local AGENT_ENDPOINT_URL=http://localhost:8080 uvicorn console.server:app --port 8090`, and `npm run dev` in web/ (proxies the API)
- Diagram: `python docs/architecture.py`
