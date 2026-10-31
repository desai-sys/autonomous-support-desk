# Autonomous CloudOps Command Center

An agentic AI-powered platform that autonomously resolves AWS infrastructure alerts in real time. The system combines LLM-based intent routing, a Bedrock Knowledge Base for historical resolution lookup, a 200+ tool CloudOps Action Agent deployed on AWS Bedrock AgentCore, and a Human-in-the-Loop (HITL) confirmation layer — all surfaced through a live Command Center UI. Tools are organized into seven domain groups (Compute, Storage, Network, Database, Security, Cost & Ops, Deployment) and the agent selects the right subset per prompt to stay within model context limits.

---

## Architecture Overview

```
Operator / SRE Engineer
    │
    ▼
EC2 t3.medium  (Streamlit Apps)
├── app.py :8501  (Command Center)
└── sre_portal.py :8502  (SRE Portal)
    │
    └── AgentGateway  (Python class, runs inside the Streamlit process)
            │  send() → _invoke_agent() → boto3 → AgentCore
            │
            ▼  A2A invoke  (invoke_agent_runtime)
┌───────────────────────────────────────────────────────┐
│  AWS Bedrock AgentCore Runtime  (ARM64 Container)      │
│  agentcore_entrypoint.py                               │
│                                                        │
│  Nova Micro  ──loads tool subset──►  Nova Pro Agent    │
│  DomainRouter (router.py)            Reasons + acts    │
│  route() → 1-2 domains              275+ tools / 7 domains│
└───────────────────────────────────────────────────────┘
            │  boto3 tool calls
            ▼
    AWS Services (Compute / Storage / Network / Database / Security / Ops)

    │ (escalate=True only)
    ▼
RAG Fallback  ──►  Bedrock KB  ◄──  S3 Bucket
                   Vector Index

    │ (on escalate)
    ▼
DynamoDB  CloudOpsTickets  ──►  learning_loop.py  ──►  S3  ──►  Bedrock KB
          (+ Users / Tenants /       (standalone script,
           Usage / AuditLog)          manual / admin trigger)
```

**Orchestrator placement:** The `AgentGateway` is a Python class that runs inside the Streamlit process on EC2 — it is not a separate Lambda or microservice. It calls AgentCore via `boto3` (`bedrock-agentcore.invoke_agent_runtime()`).

**Domain classifier placement:** The `DomainRouter` class (`src/agents/router.py`) handles domain classification via a single Nova Micro LLM call. It returns 1–2 domains per query. The AgentCore entrypoint (`agentcore_entrypoint.py`) imports the router, merges tools from the selected domains, and passes the focused subset to Nova Pro for reasoning. The deployment domain is classified via keywords like "deploy", "ship", "push", "release", "launch code", "rollback", "update function", "static site", etc.

**Learning loop:** `learning_loop.py` is a standalone Python script triggered manually or by an Admin role — it is not a Lambda function.

Fallback chain: AgentCore (Nova Pro, 1–2 domains) → auto-retry with expanded domain (up to 3) → RAG on `escalate=True` → human SRE ticket

---

## Core Modules

| Module | File | Description |
|---|---|---|
| Command Center UI | `app.py` | Streamlit enterprise glassmorphism UI — centered sign-in, chat interface, HITL warning boxes, live status bar |
| SRE Resolution Portal | `sre_portal.py` | Human dashboard for resolving escalated tickets |
| Agent Gateway | `src/agents/orchestrator.py` | Thin gateway that sends every query to the Action Agent via A2A; no classification step. RAG is used only as a fallback when the agent escalates. Also exposes `query_knowledge_base()`, a KB-only mode that queries the Bedrock KB directly without invoking the Action Agent (no AWS actions). |
| Domain Router | `src/agents/router.py` | Nova Micro LLM-based classifier. Returns 1–2 domains per query to focus the Action Agent's tool set. Supports cross-service queries (e.g. "Lambda writing to S3" → `[compute, storage]`). |
| Action Agent | `src/agentcore_entrypoint.py` | 275+ tools across 7 domain groups, deployed as AgentCore runtime. Uses `DomainRouter` to select 1–2 domain groups per prompt and merges/deduplicates tools to stay within model context limits. Auto-retries with an expanded domain (up to 3 total) if the agent indicates no matching tool was found. Includes `describe_agent_capabilities` for "what can you do?" queries. |
| RAG Service | `src/services/rag_service.py` | Bedrock KB retrieval with 0.6 confidence threshold |
| DynamoDB Service | `src/services/dynamo_service.py` | Ticket CRUD on `CloudOpsTickets` table |
| Learning Loop | `learning_loop.py` | Closed tickets → S3 → Bedrock ingestion job |

---

## Application Flow

### 1. Operator submits a query via `app.py`

The Streamlit Command Center presents a centered sign-in form on first load. After authenticating, operators interact via a chat interface in the main area. The last 4 conversation turns are passed as history for context.

### 2. AgentGateway sends the query directly to the Action Agent

There is no classification step. Every query goes straight to the Action Agent via A2A communication. The agent uses Nova Pro to reason about the user's intent and autonomously selects the appropriate tools to call.

Conversation history is prepended to the prompt when present, giving the agent multi-turn context. The session ID is derived from `customer_id` and zero-padded to at least 33 characters to meet AgentCore's `runtimeSessionId` requirement.

### 3. Action Agent reasons and acts

The Action Agent (Strands SDK, Nova Pro) receives the prompt and a focused tool subset selected by the domain router. The `DomainRouter` (`src/agents/router.py`) makes a single Nova Micro LLM call to classify the query into 1 or 2 domains. When a query spans two services (e.g. "Lambda writing to S3" → `[compute, storage]`, "security group of my RDS" → `[database, network]`), both domains' tools are merged and deduplicated. This keeps the model's tool schema small enough to reason effectively. The agent diagnoses the issue and asks for HITL confirmation before executing any fix. If no appropriate tool exists, the agent responds with `ESCALATE`.

**Auto-retry with domain expansion:** After the initial run, if the agent's response contains phrases indicating it couldn't find the right tool (e.g. "don't have a tool", "ESCALATE"), the entrypoint automatically retries by asking the router which additional domain is needed and expanding the tool set — up to 3 domains total. This reduces false escalations for queries that straddle domain boundaries without requiring a second user prompt.

Domain classification uses a single-stage strategy via `DomainRouter.route()`:
- A Nova Micro Bedrock call classifies the query into 1–2 domains based on the prompt and recent conversation history.
- For costs/billing/spending, the classifier always includes `ops`.
- For IAM/permissions/credentials, the classifier always includes `security`.
- For ambiguous or general queries ("hello", "what can you do"), the classifier returns `ops`.
- Initial classification is capped at 2 domains to keep tool count manageable.
- If the agent can't resolve with the initial domains, the entrypoint auto-retries with up to 1 additional domain (3 total max).
- On classification failure (network, auth, throttle), the router falls back to `["compute"]`.

When multiple domains are selected, the entrypoint merges tools from all domain groups (deduplicated), uses the primary domain's system prompt, and appends a cross-service context note listing the secondary domains. If the initial 1–2 domain run fails to resolve (agent signals missing tools), a retry expands to up to 3 domains before escalating.

If the request targets a specific account (see [Targeting Accounts](#targeting-accounts)), the run executes inside that account's assumed-role context and the system prompt gains an `ACCOUNT CONTEXT` note so the agent reports the account ID it acted on.

### 4. RAG fallback on escalation

When the agent responds with `ESCALATE`, the gateway calls the RAG Service as a supplementary knowledge source. The `RAGService` calls `bedrock-agent-runtime.retrieve()` against the Bedrock Knowledge Base. Results with confidence < 0.6 are treated as no-match and escalate to human.

**Knowledge Base mode (read-only lookups):** For historical resolution or how-to lookups where no AWS action should be taken, callers can use `AgentGateway.query_knowledge_base()` instead of `send()`. This mode queries the Bedrock KB directly via the RAG Service and never invokes the Action Agent, so it performs no AWS actions. It records a `kb_queries` usage event and a `kb_query_submitted` audit event, and returns the RAG result as-is — escalating on low confidence or no match, just like the fallback path.

### 5. Escalation

If both the Action Agent and RAG fail to resolve, the gateway sets `escalate=True`. The UI auto-creates a DynamoDB ticket and displays the ticket ID.

### 6. SRE Portal resolves the ticket

SREs log into `sre_portal.py`, view open tickets sorted by urgency, and provide a resolution. Closing a ticket saves the resolution text to DynamoDB.

### 7. Learning Loop

An Admin triggers `sync_closed_tickets()` from the Command Center sidebar (Admin role) or the SRE Portal sidebar. The learning loop:
1. Scans DynamoDB for closed tickets where `vectorized=False`
2. Formats each as `"Issue: ...\nResolution: ..."`
3. Uploads to S3 at `resolutions/{ticket_id}.txt`
4. Marks `vectorized=True` in DynamoDB
5. Calls `bedrock-agent.start_ingestion_job()` to re-index the Knowledge Base

Future RAG queries now return these human-resolved solutions.

---

## Tool Groups (Action Agent)

All tools are first-class citizens in `src/agentcore_entrypoint.py` and `src/tools/`. They are organized into seven domain groups. The `DomainRouter` (`src/agents/router.py`) classifies incoming prompts into 1–2 domains via a Nova Micro LLM call, and the entrypoint merges and deduplicates tools from the selected domains (~50 tools) to keep the model context manageable. When multiple domains are selected for cross-service queries, the primary domain's system prompt is used with cross-service context notes about the additional domains. The helper function `_run_agent_with_domains()` handles tool merging, prompt assembly, and agent execution for both the initial run and any auto-retry expansion.

Domain classification uses a pure LLM approach — a single Nova Micro call that returns 1 or 2 domain words. The classifier includes conversation history (last 4 turns) for resolving follow-up queries. On any failure, classification falls back to `["compute"]`.

| Group | Constant | Domain | ~Size |
|---|---|---|---|
| Compute | `TOOLS_COMPUTE` | EC2, Lambda, ECS, EKS, Batch, SageMaker (domains, user profiles, apps, models, notebooks, pipelines, experiments, feature groups, model packages, training jobs, endpoints), App Runner, Lightsail, CloudWatch, SSM | ~62 |
| Storage | `TOOLS_STORAGE` | S3 (list, analyze, objects, versioning, lifecycle, encryption, public access scan, per-bucket public status, public access block), EBS, EFS, Backup, DataSync, Transfer Family, ECR, Bedrock KB | ~33 |
| Network | `TOOLS_NETWORK` | VPC, Security Groups (list/analyze/add/remove/update/delete rules, create/delete groups), ALB/NLB, Route53, CloudFront, API Gateway, SNS, SQS, Kinesis, MSK, EventBridge | ~41 |
| Database | `TOOLS_DATABASE` | RDS, DynamoDB, ElastiCache, DocumentDB, Redshift, Neptune, OpenSearch (managed + Serverless), Glue, Athena, Timestream, QLDB, DMS | ~40 |
| Security | `TOOLS_SECURITY` | IAM, Secrets Manager, KMS, WAF, GuardDuty, Security Hub, Inspector, Macie, Shield, CloudTrail, Config, Access Analyzer, Cognito | ~35 |
| Cost & Ops | `TOOLS_COST_OPS` | Cost Explorer, Budgets, Service Quotas, Organizations, Control Tower, Trusted Advisor, Health, CloudFormation, CodePipeline, SSM Parameters, Step Functions, SES, Bedrock Models | ~36 |
| Deployment | `TOOLS_DEPLOYMENT` | Lambda deploy/rollback, ECS/ECR deploy/rollback, EC2 deploy via SSM, App Runner deploy (container + source), S3 static site deploy, CloudFormation/SAM validate/deploy/rollback, code payload preparation, language detection, deployment plan confirmation | ~15 |
| Meta | — | `describe_agent_capabilities` — self-description tool; called when the user asks "what can you do?" or "help". Also triggered via the `ops` keyword shortcut so no LLM round-trip is needed. | 1 |

### Legacy Tools (backward compatibility)

The original 26 named tools (`analyze_ec2_sg`, `fix_ec2_sg`, `check_s3_public_access`, etc.) are retained and distributed across the relevant domain groups. They delegate to the newer comprehensive tools where applicable.

## Extended Tool Modules (`src/tools/`)

| Module | Domain |
|---|---|
| `compute_tools.py` | EC2, Lambda, Auto Scaling, ECS |
| `storage_tools.py` | S3 (list, analyze, objects, versioning, lifecycle, encryption, public access scan, per-bucket public status, public access block), EBS, EFS |
| `network_tools.py` | VPC, Security Groups (list/analyze/add/remove/update/delete rules, create/delete groups), Route53, CloudFront, ALB, API Gateway |
| `database_tools.py` | RDS, DynamoDB, ElastiCache, DocumentDB |
| `security_tools.py` | IAM, Secrets Manager, KMS, WAF, GuardDuty |
| `deployment_tools.py` | Lambda deploy/rollback, ECS/ECR deploy/rollback, EC2 deploy (SSM), App Runner (container + source), S3 static sites, CloudFormation/SAM validate/deploy/rollback, code payload handling, language detection |
| `aws_tools.py` | All remaining AWS services (150+ tools) + `describe_agent_capabilities` self-description tool + `search_resource_by_name` cross-service resource search |

---

## Agent Behavior Rules (enforced via system prompt)

### Honesty & Accuracy
- Never report a resource that was not actually found — if a search returns nothing, say so explicitly.
- Never substitute a different resource type (e.g. an EBS volume) when the user asked for something else (e.g. an RDS instance).
- If a named resource cannot be found, use `search_resource_by_name` for a broad search, then report exactly what was found (or not found) and ask the user to confirm the name, resource type, or region.
- Do not keep searching random services hoping to find something to report — ask for clarification instead.
- If a result is uncertain or partial, say so explicitly rather than presenting it as definitive.
- When a request runs against a specific target account (via an assumed cross-account role), the system prompt injects an `ACCOUNT CONTEXT` note with that account ID. The agent must state which account it acted on and must not claim to have acted on any other account.

### HITL Safety
- RULE 1: Always diagnose before acting
- RULE 2: Ask for confirmation after diagnosis
- RULE 3: Never execute until user says "Yes", "go ahead", "do it", or "confirm"
- RULE 4: ESCALATE if no matching tool exists
- RULES 5–14: Domain-specific workflows for each service category

---

## AWS Services Used

| Service | Purpose |
|---|---|
| Bedrock AgentCore Runtime | Hosts the Action Agent (Nova Micro classifier + Nova Pro agent) in an ARM64 container |
| Bedrock AgentCore Memory (STM) | Native conversation memory across HITL turns |
| Amazon Nova Micro (Bedrock) | Domain classifier (`DomainRouter` in `src/agents/router.py`) — classifies queries into 1–2 tool groups per prompt via a single LLM call |
| Amazon Nova Pro (Bedrock) | Action Agent model — reasons over focused tool subset; max_tokens capped at 8192 |
| Bedrock Knowledge Base | RAG over historical resolutions (fallback when agent escalates) |
| DynamoDB (`CloudOpsTickets`) | Ticket storage |
| DynamoDB (`CloudOpsUsers`, `CloudOpsTenants`, `CloudOpsUsage`, `CloudOpsAuditLog`) | Multi-tenant user management, usage tracking, and audit logging |
| S3 | Resolution documents for KB ingestion |
| OpenSearch Serverless | Vector index for KB |
| AWS CodeBuild | ARM64 container builds for AgentCore (deploy pipeline only, not request path) |
| Amazon ECR | Container registry |
| AWS App Runner | Hosts the Streamlit UI (optional) |

---

## Project Structure

```
.
├── app.py                          # Streamlit Command Center UI
├── sre_portal.py                   # SRE Human-in-the-Loop resolution portal
├── learning_loop.py                # DynamoDB → S3 → Bedrock KB sync
├── generate_architecture_diagram.py  # Generates cloudops_architecture.png (requires Graphviz)
├── requirements.txt
├── .bedrock_agentcore.yaml         # AgentCore configuration
│
├── src/
│   ├── agentcore_entrypoint.py     # 275+ tools in 7 domain groups + BedrockAgentCoreApp
│   ├── agents/
│   │   ├── orchestrator.py         # AgentGateway — thin A2A layer + fallback chain
│   │   ├── router.py              # DomainRouter — Nova Micro LLM classifier (1-2 domains)
│   │   └── action_agent.py         # DEPRECATED: Legacy wrapper (kept for tests)
│   ├── models/
│   │   ├── query.py                # Query and Resolution Pydantic models
│   │   ├── ticket.py               # Ticket and TicketUpdate models
│   │   └── metrics.py              # Dashboard metrics model
│   ├── services/
│   │   ├── dynamo_service.py       # DynamoDB CRUD for CloudOpsTickets
│   │   └── rag_service.py          # Bedrock Knowledge Base retrieval
│   └── tools/
│       ├── deployment_tools.py     # Code deployment tools (Lambda, ECS, EC2, App Runner, S3, CFN)
│       ├── compute_tools.py        # EC2, Lambda, Auto Scaling, ECS tools
│       ├── storage_tools.py        # S3, EBS, EFS tools
│       ├── network_tools.py        # VPC, Security Groups, Route53, CloudFront tools
│       ├── database_tools.py       # RDS, DynamoDB, ElastiCache, DocumentDB tools
│       ├── security_tools.py       # IAM, Secrets Manager, KMS, WAF, GuardDuty tools
│       └── aws_tools.py            # All remaining AWS services (150+ tools)
│
└── deployment/
    └── deploy_agentcore.md         # AgentCore deployment guide
```

---

## Quick Start

See `DEPLOYMENT_GUIDE.md` for the full from-scratch setup, or `MIGRATION_GUIDE.md` to redeploy into a different AWS account (every variable to change + exact PowerShell commands). Summary:

```bash
# 1. Install dependencies
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents

# 2. Deploy the agent
agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
agentcore deploy

# 3. Run the Command Center
streamlit run app.py

# 4. Run the SRE Portal (separate terminal)
streamlit run sre_portal.py --server.port 8502

# 5. Test via CLI
agentcore invoke '{"prompt":"Hello"}'

# 6. Generate architecture diagram (optional — requires Graphviz)
# Windows: winget install graphviz  |  Mac: brew install graphviz
python generate_architecture_diagram.py
# Output: cloudops_architecture.png
```

---

## Configuration

All deployment-specific values are centralized in `config.py`. Update this file after provisioning AWS resources.

| Variable | File | Description |
|---|---|---|
| `AGENT_ARN` | `config.py` | Action Agent ARN for A2A communication |
| `KNOWLEDGE_BASE_ID` | `config.py` | Bedrock KB ID (used by RAG service and learning loop) |
| `DATA_SOURCE_ID` | `config.py` | Bedrock KB data source ID (used by learning loop) |
| `S3_BUCKET_NAME` | `config.py` | S3 bucket for resolution documents |
| `AWS_REGION` | `config.py` | AWS region for all service clients |
| `TICKETS_TABLE` | `config.py` | DynamoDB table name (`CloudOpsTickets`) |
| `CROSS_ACCOUNT_ROLE_NAME` | `config.py` | Name of the IAM role the agent assumes in each managed target account (default `CloudOpsCrossAccountRole`). The role must exist in every target account, trust the AgentCore runtime role, and grant the permissions the tools need. |

### Invoke Response Shape

```json
{
  "response": "...",
  "domain": "compute+storage",
  "session_id": "...",
  "tenant_id": "...",
  "execution_time_ms": 1234.5,
  "tools_available": 45
}
```

`domain` shows the final domain(s) used (joined with `+`). If auto-retry expanded the domains, this reflects the expanded set. `tools_available` reflects the number of deduplicated tools from all selected domains, not the total tool count. Use `ALL_TOOLS` in the entrypoint if you need the full set for a "check everything" scenario.

When a request targets one or more explicit accounts (see below), the response also includes an `accounts` array listing the target account IDs. Multi-account requests additionally include a `per_account` array with a per-account entry (`account_id`, `response`, `domain`, and an `error` type on failure), and the top-level `response` is a combined report with a `=== Account <id> ===` header per account. Because tool calls run under an assumed cross-account role, the agent states the target account ID in its report.

### Targeting Accounts

By default a request acts on the runtime's own (base) account. To target other accounts, callers can pass explicit fields in the invoke payload — `accounts`/`account_ids` (list) or `account_id`/`account` (single) — or simply mention one or more 12-digit account numbers anywhere in the prompt text. Each target account is handled under its own assumed `CROSS_ACCOUNT_ROLE_NAME` credentials; multiple accounts fan out and are aggregated into a single report.
