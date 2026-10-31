# Building an Autonomous CloudOps Command Center on AWS Bedrock AgentCore

**Author:** CloudOps Engineering Team  
**Date:** May 2026  
**Tags:** AWS, Bedrock, AgentCore, SRE, AI Agents, CloudOps, Autonomous Operations

---

## Introduction

Modern cloud infrastructure is too complex for humans to manage reactively. A mid-size AWS account can have hundreds of EC2 instances, dozens of RDS clusters, thousands of Lambda functions, and hundreds of S3 buckets — all generating alerts, metrics, and events simultaneously. On-call engineers spend more time context-switching between AWS Console tabs than actually solving problems.

We built the **Autonomous CloudOps Command Center** to change that. It's an AI-powered SRE platform that understands plain English, reasons about your AWS infrastructure, and takes action — all through a conversational interface. This post covers the architecture, the engineering decisions behind it, and where we're taking it next.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Users                                         │
│   Operator (Command Center :8501)    SRE Engineer (Portal :8502)    │
└──────────────────┬──────────────────────────┬───────────────────────┘
                   │                          │
┌──────────────────▼──────────────────────────▼───────────────────────┐
│              Streamlit Apps (EC2 t3.medium)                          │
│   app.py — Command Center          sre_portal.py — SRE Portal        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                    Orchestration Layer                                │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Agent Gateway (orchestrator.py)                             │    │
│  │                                                              │    │
│  │  ┌──────────────────────────────────────────────────────┐   │    │
│  │  │  A2A invoke → AgentCore  (no local classification)    │   │    │
│  │  └──────────────────────────────────────────────────────┘   │    │
│  │                                                              │    │
│  │  ┌──────────────────────────────────────────────────────┐   │    │
│  │  │  Bedrock Knowledge Base (RAG) — fallback on ESCALATE  │   │    │
│  │  │  S3 → Vector Index → Historical Resolutions           │   │    │
│  │  └──────────────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ A2A invoke
┌──────────────────────────▼──────────────────────────────────────────┐
│              AWS Bedrock AgentCore (ARM64 Container)                  │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Nova Micro Domain Router (router.py) → picks 1-2 domains    │    │
│  │  ~50ms, ~$0.00001 · returns e.g. [compute, storage]          │    │
│  ├─────────────────────────────────────────────────────────────┤    │
│  │  CloudOps Agent — Amazon Nova Pro                            │    │
│  │  275+ tools across 7 domain groups                           │    │
│  │                                                              │    │
│  │  Compute Storage Network Database Security  Ops   Deployment │    │
│  │  ~62     ~33     ~41     ~40      ~35       ~36    ~15       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  Built by: AWS CodeBuild (ARM64) → ECR → AgentCore Runtime           │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ boto3 calls
┌──────────────────────────▼──────────────────────────────────────────┐
│                    AWS Services (275+ tools cover)                    │
│                                                                      │
│  EC2  Lambda  ECS  EKS  Batch  SageMaker  App Runner  Lightsail     │
│  S3  EBS  EFS  ECR  Backup  DataSync  Transfer Family                │
│  RDS  DynamoDB  ElastiCache  Redshift  Neptune  OpenSearch           │
│  VPC  ALB  Route53  CloudFront  API Gateway  SNS  SQS  Kinesis       │
│  IAM  GuardDuty  WAF  KMS  CloudTrail  Config  Inspector  Macie      │
│  CloudWatch  CloudFormation  CodePipeline  SSM  Cost Explorer        │
│  Deployment: Lambda/ECS/EC2/App Runner/S3 site/CloudFormation       │
│  + 30 more services                                                  │
└─────────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────────┐
│                 Persistence & Continuous Learning                     │
│                                                                      │
│  DynamoDB (CloudOpsTickets) → Learning Loop → S3 → Bedrock KB       │
│  Every human-resolved ticket trains the AI for next time             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### 1. Streamlit Frontend (app.py + sre_portal.py)

Two separate Streamlit applications serve different personas:

**Command Center (app.py, port 8501)** — for operators and SREs who need to investigate and fix issues. Features an enterprise glassmorphism design with Inter/JetBrains Mono typography, an animated aurora background with floating orbs (motion disabled under `prefers-reduced-motion`), real-time streaming responses, HITL (Human-in-the-Loop) confirmation boxes for destructive actions, a live status bar, and automatic ticket creation on escalation.

**SRE Resolution Portal (sre_portal.py, port 8502)** — for engineers resolving escalated tickets. Every resolution entered here becomes training data for the AI through the learning loop.

Both apps run as systemd services on a single EC2 t3.medium instance, making the deployment simple and cost-effective (~$30/month).

### 2. Agent Gateway

The gateway is the thin layer between the UI and the agent. It receives every user query and decides how to handle it:

```python
def send(self, query: Query, history: List[str] = None) -> Resolution:
    # 1. Try the agent (Nova Pro with domain-specific tools)
    result = self._invoke_agent(query, history)
    
    # 2. If agent escalates, try RAG (historical resolutions)
    if result.escalate:
        rag = self.rag_service.resolve(query)
        if not rag.escalate:
            return rag
    
    return result
```

The gateway is stateless — all session memory is managed by AgentCore's built-in STM (Short-Term Memory) using the tenant ID as the session key. The gateway does **no** domain classification itself; that happens inside the AgentCore container (see below).

### 3. Nova Micro Domain Router

This is one of the most interesting engineering decisions in the system. The agent has 275+ tools, but Nova Pro can only reason effectively over ~50-60 tools at a time (context window constraint). We solve this with a lightweight LLM router that runs inside the AgentCore container (`src/agents/router.py`), before the Nova Pro agent is constructed.

**Single Nova Micro call (~50ms, ~$0.00001):**
`DomainRouter.route()` sends the prompt (plus the last 4 conversation turns for follow-up context) to Nova Micro with a classification prompt, and gets back 1 or 2 of seven domain words: `compute`, `storage`, `network`, `database`, `security`, `ops`, `deployment`.

```python
domains = router.route(prompt, history)   # e.g. ["compute", "storage"]
```

**Cross-service queries** return two domains — "Lambda writing to S3" → `[compute, storage]`, "security group of my RDS" → `[database, network]` — and the entrypoint merges and deduplicates both domains' tools. The classifier prompt also encodes a few always-on rules: costs/billing → include `ops`, IAM/permissions → include `security`, deploy/ship/rollback → include `deployment`, and ambiguous prompts ("hello", "what can you do") → `ops`.

**Auto-retry with domain expansion:** After the first run, if the agent's response signals it couldn't find a matching tool (e.g. "don't have a tool", "ESCALATE"), the entrypoint asks the router which additional domain is needed and expands the tool set — up to 3 domains total — before escalating. On any router failure (network, throttle, auth) the classifier falls back to `["compute"]`.

The result: the agent always gets the right ~15-90 tools for the task, never all 275.

### 4. AgentCore Agent (Nova Pro)

The agent runs inside an ARM64 Docker container on AWS Bedrock AgentCore. Key design decisions:

**Model choice — Amazon Nova Pro:** We use Amazon Nova Pro (`us.amazon.nova-pro-v1:0`, max_tokens 8192) for the reasoning agent and Amazon Nova Micro (`us.amazon.nova-micro-v1:0`) for the domain router. Both are available without any Marketplace subscription requirement, and Nova Pro handles multi-step reasoning and tool chaining well.

**Tool design philosophy:** Every tool follows the same pattern:
- `list_*` — enumerate resources (no parameters needed)
- `analyze_*` — deep inspection of a specific resource
- `create_*` / `modify_*` / `delete_*` — mutations (always require confirmation)

This makes the agent's behavior predictable: it always lists first, then analyzes, then acts.

**Safety by design:** The system prompt instructs the agent to:
1. Use read/list/analyze tools immediately without asking
2. Describe what it will do and ask "Should I proceed?" before any mutation
3. Execute only when the user says "yes", "go ahead", "do it", or "confirm"

**Honesty by design:** The system prompt also enforces strict accuracy rules:
1. Never report a resource that was not actually found — if a search returns nothing, say so explicitly
2. Never substitute a different resource type (e.g. an EBS volume) when the user asked for something else (e.g. an RDS instance)
3. If a named resource cannot be found, use `search_resource_by_name` for a broad search, then report exactly what was found (or not found) and ask for clarification
4. Do not keep searching random services hoping to find something to report
5. If a result is uncertain or partial, say so explicitly rather than presenting it as definitive

### 5. Continuous Learning Loop

The most powerful long-term feature. Every ticket resolved by a human SRE feeds back into the AI:

```
Human resolves ticket → DynamoDB (vectorized=False)
                      → learning_loop.py scans for unvectorized tickets
                      → Uploads resolution text to S3
                      → Triggers Bedrock KB ingestion job
                      → Next similar alert → RAG finds the resolution
                      → AI resolves it automatically
```

The system gets smarter with every human intervention. Over time, the escalation rate drops as the knowledge base grows.

---

## Key Engineering Decisions

### Why AgentCore instead of Bedrock Agents?

Bedrock Agents requires defining tools as OpenAPI schemas and deploying them as Lambda functions. AgentCore lets us write tools as plain Python functions with `@tool` decorators and deploy them as a container. This means:
- Full Python ecosystem available (boto3, pandas, etc.)
- No Lambda cold starts for tool execution
- Easier local development and testing
- Direct access to all AWS APIs without Lambda intermediaries

### Why ARM64?

AgentCore uses AWS CodeBuild to build the container. ARM64 (Graviton) builds are ~20% cheaper and often faster than x86_64 for Python workloads. The `agentcore configure` CLI sets this up automatically.

### Why a small LLM router instead of one giant agent?

Loading all 275+ tool schemas at once (~275 × ~100 tokens ≈ 27K tokens) leaves almost no context budget for reasoning, and dilutes the model's tool selection. A single Nova Micro call (~50ms, ~$0.00001) narrows the set to the 1-2 relevant domains before the Nova Pro agent is even built. Nova Micro is fast and cheap enough that the routing overhead is negligible next to the 2-8s Nova Pro reasoning step, and using the same model family for routing and reasoning keeps behavior consistent. When routing is wrong, the auto-retry expansion catches it without a second user prompt.

### Why DynamoDB for tickets instead of a relational DB?

Tickets are write-heavy (created on every escalation), read-light (SREs check the portal periodically), and have no complex relational queries. DynamoDB on-demand billing means we pay nothing when there's no activity, and it scales automatically during incident storms.

---

## Tool Coverage

The system covers 275+ tools across every major AWS service:

| Domain | Tools | Key Services |
|--------|-------|-------------|
| Compute | ~62 | EC2, Lambda, ECS, EKS, Batch, SageMaker, App Runner, Lightsail |
| Storage | ~33 | S3, EBS, EFS, ECR, Backup, DataSync, Transfer Family |
| Network | ~41 | VPC, ALB, Route53, CloudFront, API GW, SNS, SQS, Kinesis, MSK |
| Database | ~40 | RDS, DynamoDB, ElastiCache, Redshift, Neptune, OpenSearch, Glue |
| Security | ~35 | IAM, GuardDuty, WAF, KMS, CloudTrail, Inspector, Macie |
| Cost & Ops | ~36 | CloudWatch, CloudFormation, CodePipeline, Cost Explorer, SSM |
| Deployment | ~15 | Lambda, ECS/ECR, EC2 (SSM), App Runner, S3 sites, CloudFormation/SAM |

Notable capabilities:
- **Bulk security scanning:** `list_public_s3_buckets` scans every bucket in a single pass, checking public access block, ACL, and bucket policy for each
- **Cross-service resource search:** `search_resource_by_name` searches RDS, DynamoDB, ElastiCache, EC2, S3, DocumentDB, Redshift, and OpenSearch in a single call — used when the user refers to a resource by name without specifying its type
- **SageMaker full coverage:** Domains, user profiles, apps, notebooks, models, pipelines, experiments, feature groups, model registry
- **OpenSearch Serverless:** Separate from managed OpenSearch — collections, access policies, index management
- **Security group CRUD:** Create, analyze, add rules, remove rules, update rules, delete

---

## Performance Characteristics

| Operation | Latency | Cost |
|-----------|---------|------|
| Domain classification (keyword) | <1ms | $0 |
| Domain classification (Nova Micro) | ~50ms | ~$0.00001 |
| Agent reasoning (Nova Pro) | 2-8s | ~$0.001-0.01 |
| Tool execution (boto3) | 100-500ms | AWS API costs |
| RAG retrieval (Bedrock KB) | 200-500ms | ~$0.0001 |
| End-to-end simple query | ~3-5s | ~$0.01 |
| End-to-end complex multi-tool | ~10-20s | ~$0.05 |

---

## Deployment Architecture

```
Local Machine
    │
    ├── agentcore configure  →  .bedrock_agentcore.yaml + Dockerfile
    │
    └── agentcore deploy
            │
            ├── Zips source code
            ├── Uploads to S3
            ├── Triggers CodeBuild (ARM64)
            │       ├── Builds Docker image
            │       └── Pushes to ECR
            └── Updates AgentCore runtime endpoint

EC2 t3.medium (Streamlit Apps)
    ├── /opt/cloudops/
    │   ├── app.py (systemd: cloudops-command-center.service)
    │   └── sre_portal.py (systemd: cloudops-sre-portal.service)
    └── IAM Role: CloudOpsEC2Role (Bedrock + DynamoDB + S3)
```

Monthly cost estimate: **~$42-64/month**
- EC2 t3.medium: ~$30
- AgentCore invocations: ~$5-15
- Bedrock Nova Pro: ~$3-10
- DynamoDB + S3: ~$1-5
- Bedrock KB: ~$3-5

---

## Security Model

**Authentication:** Streamlit sidebar sign-in with role selection (Viewer/Operator/SRE/Admin). In production, this integrates with Cognito/SAML.

**RBAC:** Four roles with granular permissions:
- **Viewer:** Read-only access to tickets
- **Operator:** Can submit queries and execute tools
- **SRE:** Can resolve tickets and view audit logs
- **Admin:** Full access including knowledge base sync

**Tenant isolation:** Multi-tenant architecture with DynamoDB-backed usage tracking and per-tenant tool execution limits.

**Audit logging:** Every query, tool execution, and escalation is logged to `CloudOpsAuditLog` DynamoDB table with user ID, tenant ID, action, and result.

**HITL gates:** The agent never executes destructive operations without explicit user confirmation. The system prompt enforces this at the model level, and the tool descriptions reinforce it.

---

## Future Work

### Near-term (Next 3 months)

**1. Multi-region support**
Currently all tools default to `us-east-1`. The next version will auto-detect the user's primary region and support cross-region queries ("show me all EC2 instances across all regions").

**2. Proactive alerting**
Instead of waiting for users to ask, the system will subscribe to CloudWatch alarms and EventBridge events, automatically triggering investigations when thresholds are breached. The agent becomes truly autonomous — it detects, diagnoses, and proposes fixes without human initiation.

**3. Runbook automation**
Define multi-step runbooks as YAML:
```yaml
runbook: high-cpu-ec2
trigger: CPUUtilization > 90% for 5 minutes
steps:
  - analyze_ec2_instance
  - get_ec2_metrics
  - if cpu_steal > 20%: create_ec2_snapshot, modify_ec2_instance_type
  - notify: SNS topic
```

**4. Cost optimization agent**
A dedicated agent that runs weekly, identifies rightsizing opportunities, unused resources (unattached EBS volumes, idle RDS instances, old snapshots), and generates a cost reduction report with one-click remediation.

### Medium-term (3-6 months)

**5. Cognito authentication**
Replace the mock sidebar sign-in with real Cognito user pools, supporting SSO/SAML for enterprise customers.

**6. Slack/Teams integration**
Expose the agent as a Slack bot. `@cloudops check my EC2 instances` triggers the same agent flow, with responses posted back to the channel. Escalated tickets create Slack threads for team collaboration.

**7. Incident timeline reconstruction**
When an incident occurs, the agent automatically correlates CloudTrail events, CloudWatch metrics, and deployment history to reconstruct a timeline: "At 14:32, a Lambda deployment changed the timeout. At 14:35, error rate spiked. At 14:40, the ALB started returning 502s."

**8. Predictive capacity planning**
Use historical CloudWatch metrics to predict when resources will hit capacity limits. "Your RDS instance will run out of storage in approximately 12 days based on current growth rate."

### Long-term (6-12 months)

**9. Multi-cloud support**
Extend the tool framework to Azure and GCP. The gateway and agent architecture is cloud-agnostic — only the tools need to change.

**10. Self-healing infrastructure**
For well-understood failure modes, skip the confirmation step entirely. If an EC2 instance fails a health check and the runbook says "reboot on health check failure", the agent reboots it automatically and notifies the team.

**11. Compliance automation**
Continuous compliance checking against CIS AWS Foundations, SOC 2, and PCI-DSS. The agent monitors Config rules, flags violations, and can auto-remediate common issues (enabling encryption, blocking public access, enforcing MFA).

**12. Agent-to-Agent coordination**
Deploy specialist agents (SecurityAgent, CostAgent, NetworkAgent) that the gateway can delegate to. Each specialist has deep expertise in its domain and can run investigations in parallel.

---

## Lessons Learned

**1. Tool descriptions matter more than you think**

The model reads tool docstrings to decide which tool to call. A vague description like "Stop an EC2 instance" caused the model to warn users about data loss (confusing stop with terminate). Precise descriptions like "Stop a running EC2 instance (NOT terminate — data is preserved on EBS volumes)" completely changed the model's behavior.

**2. Context window is the real constraint**

275+ tools × ~100 tokens per schema ≈ 27,500 tokens just for tool definitions. This leaves almost nothing for reasoning. The Nova Micro domain router that narrows each request to ~15-90 tools was the single most impactful architectural decision.

**3. Fail open on telemetry, fail closed on actions**

Monitoring and usage tracking should never crash the agent. We wrap every telemetry call in `try/except` with silent failure. But for actual AWS actions, we want explicit errors surfaced to the user.

**4. The learning loop is the moat**

The RAG knowledge base starts empty. After 6 months of SRE resolutions, it becomes a competitive advantage — the AI knows your specific infrastructure, your team's preferred solutions, and your organization's patterns. This institutional knowledge is impossible to replicate.

**5. A tiny router model earns its keep**

Routing every query through a single Nova Micro call adds only ~50ms and a fraction of a cent, but it keeps the Nova Pro agent focused on ~15-90 relevant tools instead of drowning in 275 schemas. Pairing it with the auto-retry domain expansion means the rare misroute self-corrects without bothering the user.

---

## Getting Started

Full deployment guide: [DEPLOYMENT_GUIDE.md](./DEPLOYMENT_GUIDE.md)

Quick start:
```bash
# 1. Set up AWS infrastructure
python scripts/setup_aws_infrastructure.py

# 2. Deploy the agent
agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
agentcore deploy

# 3. Update config.py with your resource IDs

# 4. Deploy Streamlit apps to EC2
bash scripts/deploy_to_ec2.sh

# 5. Access
# Command Center: http://YOUR_EC2_IP:8501
# SRE Portal:     http://YOUR_EC2_IP:8502
```

---

## Architecture Diagram

To generate the architecture diagram with official AWS icons:

```bash
# Install Graphviz first
winget install graphviz          # Windows
brew install graphviz            # Mac
sudo apt-get install graphviz    # Linux

# Generate diagram
python generate_architecture_diagram.py
# Output: cloudops_architecture.png
```

---

## Conclusion

The Autonomous CloudOps Command Center demonstrates that AI agents can meaningfully reduce operational burden in complex cloud environments. The key insight is that the agent doesn't need to be perfect — it needs to be right often enough to reduce the number of times a human has to context-switch into the AWS Console.

With 275+ tools, intelligent domain routing, continuous learning, and a safety-first design, the system handles the majority of routine operational tasks autonomously while gracefully escalating the genuinely complex ones to human experts.

The architecture is intentionally simple: two Streamlit apps, one gateway, one AgentCore container, and a handful of AWS managed services. No Kubernetes, no OpenTelemetry, no complex service mesh. Simplicity is a feature.

---

*Source code and deployment guide available in this repository.*  
*Architecture diagram: run `python generate_architecture_diagram.py` after installing Graphviz.*
