# Technology Stack

## Python Environment
- Python 3.10+
- Virtual environment: `venv/`

## Core Frameworks
- Streamlit — Web UI framework for both `app.py` (Command Center) and `sre_portal.py` (SRE Portal)
- Pydantic 2.x — Data validation for `Query`, `Resolution`, `Ticket`, and multi-tenant/security models
- structlog — Structured logging in the core modules (monitoring, security, multi-tenant)

## AWS Services
- AWS Bedrock AgentCore — Managed ARM64 container runtime hosting the multi-domain SRE agent
- Strands SDK (`strands-agents`) — `@tool` decorator pattern and `Agent`/`BedrockModel` for the AgentCore agent
- `bedrock-agentcore-starter-toolkit` — `agentcore configure` / `agentcore deploy` CLI
- Amazon Bedrock Converse API:
  - Amazon Nova Micro (`us.amazon.nova-micro-v1:0`) — domain classification in `DomainRouter` (1-2 of 7 domains)
  - Amazon Nova Pro (`us.amazon.nova-pro-v1:0`, max_tokens 8192) — the reasoning Action Agent
- Bedrock Knowledge Base (`bedrock-agent-runtime.retrieve()`) — RAG over historical resolutions (fallback path)
- DynamoDB — `CloudOpsTickets` (tickets, PK `ticket_id`) plus `CloudOpsUsers`, `CloudOpsTenants`, `CloudOpsUsage`, `CloudOpsAuditLog` for multi-tenant management, usage tracking, and audit logging
- S3 — Resolution document storage for Bedrock KB ingestion
- OpenSearch Serverless — Vector index managed by Bedrock KB
- CloudWatch — Metrics and dashboards via `src/core/monitoring.py`
- AWS CodeBuild — ARM64 container builds for AgentCore (no local Docker needed)
- Amazon ECR — Container registry for AgentCore image
- AWS App Runner — Optional hosting for Streamlit UI

## Agentic Tooling
- `boto3` — All AWS API calls across 7 domain tool groups in `src/tools/` (compute, storage, network, database, security, cost & ops, deployment)

## Common Commands

### Environment Setup
```bash
# Activate virtual environment (Windows)
./venv/Scripts/activate

# Install dependencies
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents
```

### Run the Apps
```bash
# Command Center (operators)
streamlit run app.py

# SRE Resolution Portal (engineers) — separate terminal
streamlit run sre_portal.py --server.port 8502

# Learning loop (manual trigger)
python learning_loop.py
```

### AgentCore Deployment
```bash
agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
agentcore deploy
agentcore invoke '{"prompt":"Hello"}'
```

## Configuration
- AWS credentials via `aws configure` or environment variables
- No `.env` file — deployment-specific values are centralized in `config.py`:
  - `AGENT_ARN` — Action Agent runtime ARN (read by `src/agents/orchestrator.py`)
  - `KNOWLEDGE_BASE_ID`, `DATA_SOURCE_ID` — Bedrock KB (read by `rag_service.py` and `learning_loop.py`)
  - `S3_BUCKET_NAME`, `AWS_REGION`, `TICKETS_TABLE`
- `src/agents/action_agent.py` is a deprecated wrapper kept only for tests; it reads `AGENTCORE_AGENT_ARN` from the environment.

## Design Constraints
- No Kubernetes, no Docker locally
- AgentCore container is built by AWS CodeBuild — no local Docker required
- All AWS infrastructure is serverless (DynamoDB on-demand, S3, AgentCore, App Runner)
- Session memory is managed by AgentCore STM — no local state management needed
- `runtimeSessionId` must be 33+ characters; `customer_id` is zero-padded via `pad_session_id()` in `src/models/query.py`
