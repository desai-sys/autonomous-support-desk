# Autonomous CloudOps Command Center — Deployment & Features

A single reference for **what this agent does**, **how to deploy it**, and **exactly what you must change** to run it in your own AWS account. It is grounded in the current `config.py`, `src/agentcore_entrypoint.py`, and the deploy scripts.

> All commands target **Windows PowerShell** (the project's shell). On macOS/Linux, swap `./venv/Scripts/...` for `./venv/bin/...` and use `export VAR=value` instead of `$env:VAR = "value"`.

---

## Part 1 — What this agent does (features)

The Autonomous CloudOps Command Center is an agentic SRE platform that detects, diagnoses, and fixes AWS infrastructure issues in real time, with a human-in-the-loop (HITL) safety layer before any change is executed.

### 1.1 Core capabilities

| Feature | Description |
|---|---|
| **Multi-domain SRE agent** | A Nova Pro reasoning agent with **275+ AWS tools** organized into **7 domain groups**. Runs inside an AWS Bedrock AgentCore ARM64 container. |
| **Domain routing** | A Nova Micro (`us.amazon.nova-micro-v1:0`) classifier (`DomainRouter`) picks **1–2 of 7 domains** per prompt and loads only that domain's tools (~30–50), keeping the model context small and reasoning sharp. Supports cross-service queries (e.g. "Lambda writing to S3" → `[compute, storage]`). |
| **Auto-retry on missing tools** | If the agent signals it lacks the right tool, the entrypoint asks the router for one more domain and retries — **up to 3 domains total** — before escalating. |
| **Human-in-the-loop safety** | The agent always diagnoses first, then asks for confirmation. It never executes a write/delete until the user says "yes", "go ahead", "do it", or "confirm". Read operations run immediately. |
| **RAG fallback** | When the agent returns `ESCALATE`, the RAG service queries a Bedrock Knowledge Base of past resolutions. Results below **0.6 confidence** are treated as no-match. |
| **Escalation + ticketing** | If the agent and RAG both fail, a DynamoDB ticket is auto-created in `CloudOpsTickets` and surfaced in the SRE Portal. |
| **Continuous learning loop** | Closed tickets → S3 → Bedrock KB ingestion. Every human-resolved ticket makes future RAG lookups smarter. |
| **Multi-tenancy & audit** | Tenant tiers, usage limits, and audit logging via `CloudOpsUsers`, `CloudOpsTenants`, `CloudOpsUsage`, `CloudOpsAuditLog`. |
| **Two UIs** | A Streamlit **Command Center** (`app.py`, port 8501) for operators and an **SRE Resolution Portal** (`sre_portal.py`, port 8502) for engineers. |
| **Native session memory** | AgentCore STM keeps conversation context across HITL turns. `customer_id` is zero-padded to 33+ chars to satisfy AgentCore's `runtimeSessionId` requirement. |

### 1.2 The 7 tool domains

| Domain | Constant | Coverage | ~Tools |
|---|---|---|---|
| Compute | `TOOLS_COMPUTE` | EC2, Lambda, ECS, EKS, Batch, SageMaker, App Runner, Lightsail, CloudWatch, SSM | ~62 |
| Storage | `TOOLS_STORAGE` | S3, EBS, EFS, Backup, DataSync, Transfer Family, ECR, Bedrock KB | ~33 |
| Network | `TOOLS_NETWORK` | VPC, Security Groups, ALB/NLB, Route53, CloudFront, API Gateway, SNS, SQS, Kinesis, MSK, EventBridge | ~41 |
| Database | `TOOLS_DATABASE` | RDS, DynamoDB, ElastiCache, DocumentDB, Redshift, Neptune, OpenSearch, Glue, Athena, Timestream, QLDB, DMS | ~40 |
| Security | `TOOLS_SECURITY` | IAM, Secrets Manager, KMS, WAF, GuardDuty, Security Hub, Inspector, Macie, Shield, CloudTrail, Config, Access Analyzer, Cognito | ~35 |
| Cost & Ops | `TOOLS_COST_OPS` | Cost Explorer, Budgets, Service Quotas, Organizations, Trusted Advisor, Health, CloudFormation, CodePipeline, SSM, Step Functions, SES, Bedrock | ~36 |
| Deployment | `TOOLS_DEPLOYMENT` | Deploy/rollback for Lambda, ECS/ECR, EC2 (SSM), App Runner, S3 static sites, CloudFormation/SAM | ~15 |

Every domain also includes `search_resource_by_name` (cross-service lookup) and `describe_agent_capabilities` (answers "what can you do?").

### 1.3 Request flow

```
Operator → app.py (Command Center)
   → AgentGateway.send()  (boto3 invoke_agent_runtime)
      → AgentCore Runtime (agentcore_entrypoint.py)
         → DomainRouter (Nova Micro) picks 1–2 domains
         → Nova Pro agent reasons over merged tool subset
         → HITL confirm → tool executes real AWS API calls
      ↳ if ESCALATE → RAG (Bedrock KB, ≥0.6 conf)
         ↳ if still unresolved → DynamoDB ticket → SRE Portal → learning loop
```

The invoke response looks like:

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

---

## Part 2 — Prerequisites

On your local machine:

- **Python 3.10+** — `python --version`
- **AWS CLI v2** — `aws --version`
- **An AWS account** with permission to create DynamoDB tables, S3 buckets, IAM roles, EC2 instances, and Bedrock/AgentCore resources (`AdministratorAccess` is simplest for a first deploy)
- **Bedrock model access** for **Amazon Nova Micro** and **Amazon Nova Pro** in your target region (enable in Bedrock Console → Model access)

No local Docker or Kubernetes is needed — the AgentCore container is built by **AWS CodeBuild** (ARM64).

Configure credentials for the target account (never commit these):

```powershell
aws configure
# Region: us-east-1 (or your choice)
aws sts get-caller-identity   # the Account field is your account ID
```

---

## Part 3 — What you must change per environment

Almost everything account-specific lives in **`config.py`**, which is **auto-generated** by `scripts/setup_aws_infrastructure.py`. You normally do not hand-edit it. The table below is the full map.

### 3.1 Auto-generated into `config.py` (by the setup script)

| Variable | Current value (example) | Set for a new account by |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Argument to the setup script (defaults to `us-east-1`) |
| `UNIQUE_ID` | `6d55a7d0` | New random 8-char id per run |
| `S3_BUCKET_NAME` | `cloudops-kb-6d55a7d0` | New bucket `cloudops-kb-<UNIQUE_ID>` |
| `KNOWLEDGE_BASE_ID` | `PLACEHOLDER` | New Bedrock KB id (or stays `PLACEHOLDER` if KB creation is skipped) |
| `DATA_SOURCE_ID` | `PLACEHOLDER` | New KB data source id |
| `KB_ROLE_ARN` | `...:741448928336:role/CloudOpsKnowledgeBaseRole-6d55a7d0` | New role in the new account |
| `AGENT_ROLE_ARN` | `...:741448928336:role/CloudOpsAgentCoreRole-6d55a7d0` | New role in the new account |
| `SECURITY_GROUP_ID` | `sg-06d24da880413e8db` | New SG in the new account's default VPC |

### 3.2 Set after the agent deploys

| Variable | File | Set by |
|---|---|---|
| `AGENT_ARN` | `config.py` | `deploy.ps1` rewrites it after `agentcore deploy`. Manual path: copy the runtime ARN from `agentcore status`. |

> **Note:** The current `config.py` has `AGENT_ARN` on account `058264401886` while the role ARNs use `741448928336`. A clean re-deploy makes them consistent — every ARN should share the **same** account id from `aws sts get-caller-identity`.

### 3.3 Stable values (no change needed)

`TICKETS_TABLE`, `USERS_TABLE`, `TENANTS_TABLE`, `USAGE_TABLE`, `AUDIT_TABLE` (table names, not ARNs), `DEFAULT_TENANT_ID`, `DEFAULT_USER_ID`, `STREAMLIT_PORT_COMMAND_CENTER` (8501), `STREAMLIT_PORT_SRE_PORTAL` (8502).

### 3.4 Account-specific values OUTSIDE `config.py` — fix by hand

| Location | Issue | What to do |
|---|---|---|
| `deploy.ps1` (`$AWS_ACCESS_KEY_ID` / `$AWS_SECRET_ACCESS_KEY`) | Hardcoded credentials | **Remove them.** Read from env vars instead (see 3.5). Rotate any exposed key in IAM. |
| `deploy.ps1` Step 5 `--role-name AmazonBedrockAgentCoreSDKRuntime-us-east-1-<hash>` | The `<hash>` is account-specific | After your first `agentcore deploy`, find the real runtime role name (Part 5.2) and update this line. |
| `src/agents/action_agent.py` (`AGENTCORE_AGENT_ARN` default) | Old hardcoded ARN | **Deprecated** module used only by tests; reads `AGENTCORE_AGENT_ARN` from env at runtime. The live app uses `config.AGENT_ARN`, so you can leave it or update the default. |
| `scripts/fix_arn.py` | Old ARN baked in | One-off helper; ignore for new deploys. |
| `deployment_info.json` | Old account ids | Auto-overwritten by the setup script on the next run. |

### 3.5 Secure `deploy.ps1` before running it

Replace hardcoded keys with environment reads:

```powershell
$AWS_ACCESS_KEY_ID     = $env:AWS_ACCESS_KEY_ID
$AWS_SECRET_ACCESS_KEY = $env:AWS_SECRET_ACCESS_KEY
$AWS_REGION            = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }

if (-not $AWS_ACCESS_KEY_ID -or -not $AWS_SECRET_ACCESS_KEY) {
    Write-Host "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars first." -ForegroundColor Red
    exit 1
}
```

Prefer the **EC2 instance role** (`CloudOpsEC2Role`, attached by the script) over copying long-lived keys onto the instance.

---

## Part 4 — Fast path (one-shot deploy)

If you want it running with minimal steps:

```powershell
# 1. Configure credentials for the target account
aws configure

# 2. Secure deploy.ps1 first (Part 3.5), then run the one-shot deploy
./deploy.ps1
```

`deploy.ps1` creates infrastructure, deploys the agent, launches EC2, and starts both apps. It regenerates `config.py` and rewrites `AGENT_ARN` automatically. When it finishes, open the printed `http://<ElasticIP>:8501` and `:8502`.

Prefer to control each step? Follow Parts 5–8.

---

## Part 5 — Manual deployment

### 5.1 Set up the environment and provision infrastructure

```powershell
python -m venv venv
./venv/Scripts/Activate.ps1
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents

# Create all backing resources + regenerate config.py / deployment_info.json
python scripts/setup_aws_infrastructure.py
# or a different region:
python scripts/setup_aws_infrastructure.py us-west-2
```

This creates:
- **DynamoDB tables:** `CloudOpsTickets`, `CloudOpsUsers`, `CloudOpsTenants`, `CloudOpsUsage`, `CloudOpsAuditLog` (PAY_PER_REQUEST)
- **S3 bucket:** `cloudops-kb-<UNIQUE_ID>` (versioned, public access blocked)
- **IAM roles:** `CloudOpsKnowledgeBaseRole-<id>`, `CloudOpsAgentCoreRole-<id>` (PowerUserAccess + IAMReadOnlyAccess)
- **Bedrock Knowledge Base** + S3 data source + a sample resolution + first ingestion job
- **EC2 security group** opening ports 22, 8501, 8502

> If Bedrock KB creation fails (e.g. S3 Vectors unavailable in the region), the script continues and writes `KNOWLEDGE_BASE_ID = "PLACEHOLDER"`. The app still runs; only the RAG fallback is inactive until you create a KB and update `config.py`.

Confirm Nova model access:

```powershell
aws bedrock list-foundation-models --region us-east-1 --query "modelSummaries[?contains(modelId,'nova')].modelId" --output text
```

### 5.2 Deploy the AgentCore agent

```powershell
# Point AgentCore at the entrypoint (creates .bedrock_agentcore.yaml + Dockerfile)
./venv/Scripts/agentcore.exe configure --entrypoint src/agentcore_entrypoint.py --non-interactive

# Build via CodeBuild (ARM64, no local Docker) and deploy (~60s)
./venv/Scripts/agentcore.exe deploy
```

Grant the runtime role AWS permissions (the `<hash>` is account-specific):

```powershell
$runtimeRole = aws iam list-roles --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime')].RoleName" --output text
$runtimeRole
aws iam attach-role-policy --role-name $runtimeRole --policy-arn "arn:aws:iam::aws:policy/PowerUserAccess"
```

### 5.3 Capture the Agent ARN into `config.py`

```powershell
./venv/Scripts/agentcore.exe status
```

Copy the runtime ARN into `config.py`:

```python
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:<YOUR_ACCOUNT>:runtime/src_agentcore_entrypoint-XXXXXXXX"
```

(`deploy.ps1` does this automatically.)

### 5.4 Smoke-test

```powershell
./venv/Scripts/agentcore.exe invoke '{"prompt":"hello","domain":"auto"}'
```

You should get JSON with a `response` field. The first invocation can take a minute while the container warms up.

---

## Part 6 — Run the apps

### Locally

```powershell
./venv/Scripts/streamlit.exe run app.py
# Second terminal:
./venv/Scripts/streamlit.exe run sre_portal.py --server.port 8502
```

Open http://localhost:8501 (Command Center) and http://localhost:8502 (SRE Portal).

### On EC2 (production)

`deploy.ps1` automates this: it creates a key pair, security group, and instance profile (`CloudOpsEC2Role` with PowerUserAccess), launches a `t3.medium` on Amazon Linux 2023, allocates an **Elastic IP** (stable URL), copies the project up, and starts both Streamlit apps. See `DEPLOYMENT_GUIDE.md` for the long-form manual walkthrough.

---

## Part 7 — Verify

```powershell
aws sts get-caller-identity                                             # right account?
aws dynamodb list-tables --query "TableNames[?starts_with(@, 'CloudOps')]"   # tables exist?
$bucket = (Select-String -Path config.py -Pattern 'S3_BUCKET_NAME = "(.+)"').Matches.Groups[1].Value
aws s3 ls "s3://$bucket"                                                 # bucket exists?
./venv/Scripts/agentcore.exe invoke '{"prompt":"list my S3 buckets","domain":"auto"}'  # agent responds?
Select-String -Path config.py -Pattern 'AGENT_ARN'                      # real ARN, not placeholder?
```

In the UI: sign in, run a read-only query like `list my EC2 instances`, then try a query with no matching tool to confirm it escalates and creates a ticket in the SRE Portal.

---

## Part 8 — Tear down

```powershell
./teardown.ps1            # preview (safe)
./teardown.ps1 --confirm  # actually delete CloudOps-tagged resources
```

`teardown.ps1` removes the AgentCore agent, EC2 instances + Elastic IPs, the five DynamoDB tables, the S3 bucket, CloudOps IAM roles + instance profiles, security groups, and key pairs. It prints a reminder to manually clean up the Bedrock KB, ECR repos, and CodeBuild projects if needed.

---

## Deployment checklist

- [ ] Removed hardcoded keys from `deploy.ps1`; rotated any exposed key in IAM
- [ ] `aws sts get-caller-identity` shows the correct account
- [ ] Bedrock **Nova Micro + Nova Pro** access enabled in the target region
- [ ] Ran `setup_aws_infrastructure.py` → fresh `config.py` + `deployment_info.json`
- [ ] `agentcore deploy` succeeded; `PowerUserAccess` attached to the runtime role
- [ ] `config.py` `AGENT_ARN` points at this account's runtime ARN
- [ ] All ARNs in `config.py` share the **same** account id
- [ ] Smoke test (`agentcore invoke`) returns a response
- [ ] Apps reachable (localhost or `http://<ElasticIP>:8501` / `:8502`)

---

## Related docs

- `README.md` — full system overview and architecture (source of truth)
- `MIGRATION_GUIDE.md` — redeploying into a different AWS account (detailed)
- `DEPLOYMENT_GUIDE.md` — from-scratch AWS setup walkthrough
- `deployment/deploy_agentcore.md` — AgentCore-specific deployment steps
- `AGENT_CAPABILITIES.md` — sample queries for every tool, grouped by domain
