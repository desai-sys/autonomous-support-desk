# Migration Guide — Deploying to a Different AWS Account

This guide is the single source of truth for standing the Autonomous CloudOps Command Center up in a **new AWS account** (or a new region). It lists every account-specific value you must change, every command to run (Windows PowerShell), and how to verify the result.

> **Platform note:** All commands are written for **Windows PowerShell** (the project's target shell). On macOS/Linux, swap `./venv/Scripts/...` for `./venv/bin/...` and use `export VAR=value` instead of `$env:VAR = "value"`.

---

## 0. TL;DR — the fastest path

If you just want it running in a fresh account and you're comfortable letting the script do everything:

```powershell
# 1. Configure credentials for the NEW account (see Section 2 — do NOT hardcode them)
aws configure

# 2. Run the one-shot deploy (creates infra, deploys agent, launches EC2, starts apps)
./deploy.ps1

# 3. When it finishes, open the printed URLs (http://<ElasticIP>:8501 and :8502)
```

`deploy.ps1` regenerates `config.py` for the new account automatically (via `scripts/setup_aws_infrastructure.py`) and rewrites `AGENT_ARN` after the agent deploys. **Before running it, read Section 1 — the current `deploy.ps1` has hardcoded credentials that must be removed.**

If you prefer to understand and control each step, follow Sections 2–9 instead.

---

## 1. ⚠️ Security must-dos before you migrate

These are not optional. The repo currently contains secrets that must not travel to a new account or a shared remote.

| Item | Where | Action |
|---|---|---|
| Hardcoded AWS access key + secret | `deploy.ps1` lines ~8-9 (`$AWS_ACCESS_KEY_ID`, `$AWS_SECRET_ACCESS_KEY`) | **Delete these values.** Replace with the credential handling in Section 2. Rotate/deactivate the exposed key `AKIAQ3EGUG7POZMUORWN` in IAM immediately if it was ever real. |
| Private SSH keys | `cloudops-key.pem`, `cloudops-key-20260628.pem` (already deleted from the tree) | Keep them out of git. `.gitignore` now blocks `*.pem`. If they were ever committed, rotate the EC2 key pair. |
| Hardcoded ARN | `scripts/fix_arn.py` | One-off remote helper with an old ARN baked in. Don't rely on it for a new account — `deploy.ps1`/`setup_aws_infrastructure.py` handle the ARN correctly. |
| Credentials written to EC2 | `deploy.ps1` Step 9 writes your keys into `~/.aws/credentials` on the instance | Prefer the **EC2 instance role** (`CloudOpsEC2Role`, which the script already attaches) and remove the credential-copy block, so no long-lived keys land on the box. |

**Recommended fix for `deploy.ps1`** — read credentials from the environment instead of hardcoding:

```powershell
$AWS_ACCESS_KEY_ID     = $env:AWS_ACCESS_KEY_ID
$AWS_SECRET_ACCESS_KEY = $env:AWS_SECRET_ACCESS_KEY
$AWS_REGION            = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }

if (-not $AWS_ACCESS_KEY_ID -or -not $AWS_SECRET_ACCESS_KEY) {
    Write-Host "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY env vars first." -ForegroundColor Red
    exit 1
}
```

Then before running: `$env:AWS_ACCESS_KEY_ID = "..."; $env:AWS_SECRET_ACCESS_KEY = "..."` (these are process-scoped and never written to a tracked file).

---

## 2. Prerequisites

On your local machine:

- **Python 3.10+** — `python --version`
- **AWS CLI v2** — `aws --version`
- **OpenSSH client** (`ssh`, `scp`) — bundled with Windows 10/11; verify with `ssh -V`
- **An IAM user/role in the NEW account** with permissions to create DynamoDB tables, S3 buckets, IAM roles, EC2 instances, and Bedrock/AgentCore resources (AdministratorAccess is simplest for a first deploy)

Configure credentials for the **new** account (never commit these):

```powershell
aws configure
# AWS Access Key ID:     <new account key>
# AWS Secret Access Key: <new account secret>
# Default region name:   us-east-1
# Default output format:  json

# Confirm you're pointed at the NEW account:
aws sts get-caller-identity
```

The `Account` field in that output is your **new account ID** — you'll see it substituted into ARNs automatically.

---

## 3. What changes per account — the complete variable map

Almost everything account-specific lives in **`config.py`**, which is **auto-generated** by `scripts/setup_aws_infrastructure.py`. You normally do **not** edit it by hand — but here is every value so you know what moves.

### 3.1 Auto-generated into `config.py` (by the setup script)

| Variable | Example (old account) | How it's set for the new account |
|---|---|---|
| `AWS_REGION` | `us-east-1` | Argument to the setup script (defaults to `us-east-1`) |
| `UNIQUE_ID` | `6d55a7d0` | New random 8-char id per run |
| `S3_BUCKET_NAME` | `cloudops-kb-6d55a7d0` | New bucket `cloudops-kb-<UNIQUE_ID>` |
| `KNOWLEDGE_BASE_ID` | `PLACEHOLDER` | New Bedrock KB id (or `PLACEHOLDER` if KB creation is skipped) |
| `DATA_SOURCE_ID` | `PLACEHOLDER` | New KB data source id |
| `KB_ROLE_ARN` | `arn:aws:iam::741448928336:role/CloudOpsKnowledgeBaseRole-6d55a7d0` | New role in the new account |
| `AGENT_ROLE_ARN` | `arn:aws:iam::741448928336:role/CloudOpsAgentCoreRole-6d55a7d0` | New role in the new account |
| `SECURITY_GROUP_ID` | `sg-06d24da880413e8db` | New SG in the new account's default VPC |

### 3.2 Set after the agent deploys

| Variable | File | Set by |
|---|---|---|
| `AGENT_ARN` | `config.py` | `deploy.ps1` Step 6 rewrites it after `agentcore deploy`. Manual path: copy the ARN from `agentcore status`. |

### 3.3 Values that are stable (don't change per account)

These are fine as-is: `TICKETS_TABLE`, `USERS_TABLE`, `TENANTS_TABLE`, `USAGE_TABLE`, `AUDIT_TABLE` (table **names**, not ARNs), `DEFAULT_TENANT_ID`, `DEFAULT_USER_ID`, `STREAMLIT_PORT_COMMAND_CENTER` (8501), `STREAMLIT_PORT_SRE_PORTAL` (8502).

### 3.4 Account-specific values that live OUTSIDE `config.py` — fix these by hand

These are **not** regenerated by the setup script. Update them (or delete/ignore where noted):

| Location | Current value | What to do |
|---|---|---|
| `deploy.ps1` (`$AWS_ACCESS_KEY_ID` / `$AWS_SECRET_ACCESS_KEY`) | old account keys | Replace with env-var reading (Section 1). |
| `deploy.ps1` Step 5 (`--role-name AmazonBedrockAgentCoreSDKRuntime-us-east-1-7bd3d46147`) | old account's AgentCore runtime role | The suffix (`7bd3d46147`) is account-specific. After your first `agentcore deploy`, find the real name with the command in Section 5.2 and update this line, or remove the line and attach `PowerUserAccess` to whatever runtime role AgentCore created. |
| `src/agents/action_agent.py` (`AGENTCORE_AGENT_ARN` default) | `arn:...:741448928336:runtime/...eWsFZM3S6Y` | This is a **deprecated** module used only by tests. It reads `AGENTCORE_AGENT_ARN` from the env at runtime, so the hardcoded default only matters if you actually invoke it. Optionally update the default to your new ARN, or leave it — the live app uses `config.AGENT_ARN`, not this. |
| `scripts/fix_arn.py` | old ARN | One-off helper; ignore for new deploys. |
| `deployment_info.json` | old account roles/ids | Auto-overwritten by the setup script on the next run. |

> **Known inconsistency to resolve:** In the old deployment, `config.py`'s `AGENT_ARN` was account `058264401886`, while `AGENT_ROLE_ARN`/`KB_ROLE_ARN` and `action_agent.py` used `741448928336`. A clean re-deploy into one new account makes them all consistent because the setup script + `deploy.ps1` write a single account ID everywhere. If you deploy manually, make sure every ARN uses the **same** account from `aws sts get-caller-identity`.

---

## 4. Provision infrastructure (DynamoDB, S3, IAM, Bedrock KB, SG)

This creates all backing resources and regenerates `config.py` + `deployment_info.json` for the new account.

```powershell
# From the project root
python -m venv venv
./venv/Scripts/Activate.ps1
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents

# Create everything (region optional, defaults to us-east-1)
python scripts/setup_aws_infrastructure.py
# or a different region:
python scripts/setup_aws_infrastructure.py us-west-2
```

What it creates:
- **DynamoDB tables:** `CloudOpsTickets`, `CloudOpsUsers`, `CloudOpsTenants`, `CloudOpsUsage`, `CloudOpsAuditLog` (all PAY_PER_REQUEST)
- **S3 bucket:** `cloudops-kb-<UNIQUE_ID>` (versioned, public access blocked)
- **IAM roles:** `CloudOpsKnowledgeBaseRole-<id>` and `CloudOpsAgentCoreRole-<id>` (PowerUserAccess + IAMReadOnlyAccess)
- **Bedrock Knowledge Base** + S3 data source + a sample resolution document + first ingestion job
- **EC2 security group** opening ports 22, 8501, 8502

> If Bedrock KB creation fails (common if S3 Vectors isn't available in your region), the script continues and writes `KNOWLEDGE_BASE_ID = "PLACEHOLDER"`. The app still runs; only the RAG fallback is inactive until you create a KB and update `config.py`.

**Enable Bedrock model access** (one-time per account/region) — the agent needs Nova Micro + Nova Pro:

```powershell
aws bedrock list-foundation-models --region us-east-1 --query "modelSummaries[?contains(modelId,'nova')].modelId" --output text
```
If Nova models aren't listed as accessible, open **Bedrock Console → Model access** and enable **Amazon Nova Micro** and **Amazon Nova Pro**.

---

## 5. Deploy the AgentCore agent

### 5.1 Configure and deploy

```powershell
# Point AgentCore at the entrypoint (creates .bedrock_agentcore.yaml + a Dockerfile)
./venv/Scripts/agentcore.exe configure --entrypoint src/agentcore_entrypoint.py --non-interactive

# Build (ARM64 via AWS CodeBuild — no local Docker needed) and deploy (~60s)
./venv/Scripts/agentcore.exe deploy
```

### 5.2 Grant the runtime role AWS permissions

AgentCore creates its own execution role named like `AmazonBedrockAgentCoreSDKRuntime-<region>-<hash>`. The `<hash>` is account-specific. Find it and attach `PowerUserAccess` so the tools can call AWS APIs:

```powershell
# Find the runtime role name
$runtimeRole = aws iam list-roles --query "Roles[?starts_with(RoleName, 'AmazonBedrockAgentCoreSDKRuntime')].RoleName" --output text
$runtimeRole

# Attach broad access (idempotent)
aws iam attach-role-policy --role-name $runtimeRole --policy-arn "arn:aws:iam::aws:policy/PowerUserAccess"
```

If you use `deploy.ps1`, update its Step 5 `--role-name` to `$runtimeRole` (or delete the hardcoded name and use the two commands above).

### 5.3 Capture the Agent ARN into config.py

```powershell
# See the deployed agent's ARN/status
./venv/Scripts/agentcore.exe status
```

Copy the runtime ARN (looks like `arn:aws:bedrock-agentcore:us-east-1:<NEW_ACCOUNT>:runtime/src_agentcore_entrypoint-XXXXXXXX`) into `config.py`:

```python
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:<NEW_ACCOUNT>:runtime/src_agentcore_entrypoint-XXXXXXXX"
```

(`deploy.ps1` does this rewrite automatically in Step 6.)

### 5.4 Smoke-test the agent

```powershell
./venv/Scripts/agentcore.exe invoke '{"prompt":"hello","domain":"auto"}'
```
You should get a JSON response with a `response` field. First invocation may take a minute while the container warms up.

---

## 6. Run the apps locally (optional, for a quick check)

```powershell
# Command Center
./venv/Scripts/streamlit.exe run app.py
# In a second terminal — SRE Portal
./venv/Scripts/streamlit.exe run sre_portal.py --server.port 8502
```
Open http://localhost:8501 and http://localhost:8502.

---

## 7. Deploy the apps to EC2 (production)

`deploy.ps1` Steps 8-9 automate this: it creates a key pair (`cloudops-key.pem`), a security group (`cloudops-streamlit-sg`), an instance profile (`CloudOpsEC2Role` with PowerUserAccess), launches a `t3.medium` on Amazon Linux 2023, allocates an **Elastic IP** (so the URL never changes), then SCPs the project up and starts both Streamlit apps.

Manual equivalent, if you're not using the script:

```powershell
# Create a key pair (writes cloudops-key.pem locally — keep it out of git)
aws ec2 create-key-pair --key-name cloudops-key --query "KeyMaterial" --output text | Out-File -Encoding ascii cloudops-key.pem

# Launch (use the SG id from config.py and the latest AL2023 AMI)
$sg = (Select-String -Path config.py -Pattern 'SECURITY_GROUP_ID = "(.+)"').Matches.Groups[1].Value
$ami = aws ec2 describe-images --owners amazon --filters "Name=name,Values=al2023-ami-2023*-x86_64" "Name=state,Values=available" --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text
aws ec2 run-instances --image-id $ami --instance-type t3.medium --key-name cloudops-key --security-group-ids $sg --iam-instance-profile "Name=CloudOpsEC2Role" --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=cloudops-streamlit}]"
```

Then copy the project up (zip excludes secrets and venv) and start the apps over SSH — see `deploy.ps1` Step 9 for the exact commands, or `DEPLOYMENT_GUIDE.md` for the long-form walkthrough.

> **Prefer the instance role over copied keys.** The instance profile `CloudOpsEC2Role` already grants the app AWS access on the box, so you can skip writing `~/.aws/credentials` on EC2 entirely.

---

## 8. Verify the migration

```powershell
# 1. Right account?
aws sts get-caller-identity

# 2. Tables exist?
aws dynamodb list-tables --query "TableNames[?starts_with(@, 'CloudOps')]"

# 3. Bucket exists?
$bucket = (Select-String -Path config.py -Pattern 'S3_BUCKET_NAME = "(.+)"').Matches.Groups[1].Value
aws s3 ls "s3://$bucket"

# 4. Agent responds?
./venv/Scripts/agentcore.exe invoke '{"prompt":"list my S3 buckets","domain":"auto"}'

# 5. config.py has a real AGENT_ARN (not the placeholder)?
Select-String -Path config.py -Pattern 'AGENT_ARN'
```

In the UI: sign in, run a read-only query like `list my EC2 instances`, and confirm you get a real answer. Then try a query with no matching tool to confirm it escalates and creates a ticket in the SRE Portal.

---

## 9. Tear down (to clean up the old account or a failed attempt)

```powershell
# Preview what would be deleted (safe)
./teardown.ps1

# Actually delete everything CloudOps-tagged in the account
./teardown.ps1 --confirm
```

`teardown.ps1` removes the AgentCore agent, EC2 instances + Elastic IPs, the five DynamoDB tables, the S3 bucket (from `config.py`), CloudOps IAM roles + instance profiles, security groups, and key pairs. It prints a reminder to manually clean up the Bedrock KB, ECR repos, and CodeBuild projects if needed.

---

## Appendix A — Account-specific values checklist

Copy this and tick each item when migrating:

- [ ] Removed hardcoded keys from `deploy.ps1`; rotated the exposed key in IAM
- [ ] `aws sts get-caller-identity` shows the **new** account
- [ ] Bedrock **Nova Micro + Nova Pro** access enabled in the target region
- [ ] Ran `setup_aws_infrastructure.py` → new `config.py` + `deployment_info.json`
- [ ] `agentcore deploy` succeeded; `PowerUserAccess` attached to the new runtime role
- [ ] `config.py` `AGENT_ARN` points at the **new** account's runtime ARN
- [ ] All ARNs in `config.py` share the **same** account id
- [ ] (If used) `deploy.ps1` Step 5 `--role-name` updated to the new runtime role
- [ ] Smoke test (`agentcore invoke`) returns a response
- [ ] EC2 apps reachable at `http://<ElasticIP>:8501` and `:8502`

## Appendix B — Files that reference account-specific data

| File | Regenerated automatically? | Notes |
|---|---|---|
| `config.py` | ✅ by `setup_aws_infrastructure.py` (except `AGENT_ARN`, set by `deploy.ps1`) | Primary config the app reads |
| `deployment_info.json` | ✅ by `setup_aws_infrastructure.py` | Record of the last provision |
| `deploy.ps1` | ❌ | Remove hardcoded keys; update runtime role name |
| `src/agents/action_agent.py` | ❌ | Deprecated (tests only); reads `AGENTCORE_AGENT_ARN` env var at runtime |
| `scripts/fix_arn.py` | ❌ | One-off; ignore for new deploys |
| `.bedrock_agentcore.yaml` | ✅ by `agentcore configure` | Recreated per environment |
