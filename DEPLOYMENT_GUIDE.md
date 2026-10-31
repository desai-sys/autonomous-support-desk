# CloudOps Command Center — Complete Deployment Guide

> **From scratch, no Git required.** Follow every step in order.  
> Estimated time: ~60–90 minutes for a first deployment.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [AWS Account Setup](#2-aws-account-setup)
3. [Create IAM User & Configure CLI](#3-create-iam-user--configure-cli)
4. [Create S3 Bucket for Knowledge Base](#4-create-s3-bucket-for-knowledge-base)
5. [Create DynamoDB Tables](#5-create-dynamodb-tables)
6. [Create Bedrock Knowledge Base (S3 Vector)](#6-create-bedrock-knowledge-base-s3-vector)
7. [Enable Bedrock Model Access](#7-enable-bedrock-model-access)
8. [Run Infrastructure Setup Script](#8-run-infrastructure-setup-script)
9. [Deploy AgentCore Agent](#9-deploy-agentcore-agent)
10. [Update Configuration Values](#10-update-configuration-values)
11. [Launch EC2 Instance for Streamlit Apps](#11-launch-ec2-instance-for-streamlit-apps)
12. [Deploy Streamlit Apps to EC2](#12-deploy-streamlit-apps-to-ec2)
13. [Verify Everything Works](#13-verify-everything-works)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Prerequisites

### On your local machine you need:
- Python 3.10+ installed
- AWS CLI v2 installed ([download here](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html))
- An AWS account with billing enabled
- The project files (this folder)

### Verify Python and AWS CLI:
```bash
python --version        # Should show 3.10+
aws --version           # Should show aws-cli/2.x.x
```

---

## 2. AWS Account Setup

### Enable required AWS services (one-time per account):

1. Go to **AWS Console → Billing → Activate IAM user billing access**
2. Go to **AWS Console → Support → Support Center** — ensure your account is not in sandbox mode for SES (not needed here, but good practice)

### Choose your region:
All steps below use **us-east-1 (N. Virginia)**. If you use a different region, replace `us-east-1` everywhere.

> ⚠️ Bedrock is not available in all regions. Stick with `us-east-1` or `us-west-2`.

---

## 3. Create IAM User & Configure CLI

### 3a. Create IAM User in Console

1. Go to **AWS Console → IAM → Users → Create user**
2. **User name:** `cloudops-admin`
3. Click **Next**
4. Select **Attach policies directly**
5. Attach these policies:
   - `AdministratorAccess` *(for initial setup — restrict later in production)*
6. Click **Create user**

### 3b. Create Access Keys

1. Click on the user `cloudops-admin`
2. Go to **Security credentials** tab
3. Click **Create access key**
4. Select **Command Line Interface (CLI)**
5. Check the confirmation box → **Next**
6. **Download the .csv file** — you won't see the secret key again
7. Note down:
   - **Access key ID** (looks like: `AKIAIOSFODNN7EXAMPLE`)
   - **Secret access key** (looks like: `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`)

### 3c. Configure AWS CLI

Run on your local machine:
```bash
aws configure
```

Enter when prompted:
```
AWS Access Key ID:     AKIAIOSFODNN7EXAMPLE        ← your key ID
AWS Secret Access Key: wJalrXUtnFEMI/K7MDENG/...  ← your secret key
Default region name:   us-east-1
Default output format: json
```

Verify it works:
```bash
aws sts get-caller-identity
```
You should see your account ID and user ARN.

---

## 4. Create S3 Bucket for Knowledge Base

### 4a. Create the bucket in Console

1. Go to **AWS Console → S3 → Create bucket**
2. **Bucket name:** `cloudops-kb-YOURNAME-2024`  
   *(must be globally unique — add your name or random suffix)*
3. **Region:** `us-east-1`
4. **Block all public access:** ✅ Keep checked (default)
5. **Bucket Versioning:** Enable
6. Click **Create bucket**

### 4b. Create folder structure

1. Click into your new bucket
2. Click **Create folder** → name it `resolutions` → **Create folder**
3. Click **Create folder** → name it `knowledge-base-vectors` → **Create folder**

### 4c. Upload a sample resolution document

1. Click into the `resolutions/` folder
2. Click **Upload → Add files**
3. Create a text file on your computer called `sample-resolutions.txt` with this content:

```
Issue: EC2 instance SSH connection timeout
Resolution: Check security group rules. Ensure port 22 is open for your IP. 
Verify the instance is running and has a public IP if accessing from internet.
Use analyze_ec2_instance tool to check security groups.

Issue: S3 bucket public access concern
Resolution: Apply public access block using secure_s3_bucket tool.
Enable server-side encryption. Review bucket policy for overly permissive rules.

Issue: RDS database connection refused
Resolution: Check RDS security group allows port 3306 (MySQL) or 5432 (PostgreSQL).
Verify the database is in the same VPC as the connecting resource.
Use analyze_rds_instance to check status and endpoint.

Issue: Lambda function timeout
Resolution: Increase timeout in function configuration (max 15 minutes).
Check for infinite loops or slow external API calls.
Use analyze_lambda_function to review current settings.

Issue: Auto Scaling Group not scaling
Resolution: Check scaling policies and CloudWatch alarms.
Verify IAM role has autoscaling permissions.
Use analyze_auto_scaling_group to review current capacity settings.
```

4. Upload the file
5. **Note down your bucket name** — you'll need it in Step 10.

---

## 5. Create DynamoDB Tables

### Option A: Use the setup script (recommended)

```bash
# From your project root
python scripts/setup_aws_infrastructure.py
```

This creates all 5 tables automatically. Skip to Step 6.

### Option B: Create manually in Console

Go to **AWS Console → DynamoDB → Create table** for each:

**Table 1: CloudOpsTickets**
- Table name: `CloudOpsTickets`
- Partition key: `ticket_id` (String)
- Table settings: Customize → On-demand capacity
- Click **Create table**

**Table 2: CloudOpsUsers**
- Table name: `CloudOpsUsers`
- Partition key: `user_id` (String)
- Table settings: On-demand capacity
- Click **Create table**

**Table 3: CloudOpsTenants**
- Table name: `CloudOpsTenants`
- Partition key: `tenant_id` (String)
- Table settings: On-demand capacity
- Click **Create table**

**Table 4: CloudOpsUsage**
- Table name: `CloudOpsUsage`
- Partition key: `tenant_id` (String)
- Sort key: `period` (String)
- Table settings: On-demand capacity
- Click **Create table**

**Table 5: CloudOpsAuditLog**
- Table name: `CloudOpsAuditLog`
- Partition key: `event_id` (String)
- Sort key: `timestamp` (String)
- Table settings: On-demand capacity
- Click **Create table**

Wait ~2 minutes for all tables to show **Active** status.

---

## 6. Create Bedrock Knowledge Base (S3 Vector)

### 6a. Create IAM Role for Knowledge Base

1. Go to **AWS Console → IAM → Roles → Create role**
2. **Trusted entity type:** AWS service
3. **Use case:** Select **Bedrock** from the dropdown → choose **Bedrock - Knowledge Base**
4. Click **Next**
5. Search and attach: `AmazonS3FullAccess` and `AmazonBedrockFullAccess`
6. Click **Next**
7. **Role name:** `CloudOpsBedrockKBRole`
8. Click **Create role**
9. **Note down the Role ARN** (looks like: `arn:aws:iam::123456789012:role/CloudOpsBedrockKBRole`)

### 6b. Create the Knowledge Base

1. Go to **AWS Console → Amazon Bedrock → Knowledge bases → Create knowledge base**
2. **Knowledge base name:** `CloudOpsKnowledgeBase`
3. **IAM role:** Select `CloudOpsBedrockKBRole`
4. Click **Next**

**Data source configuration:**
5. **Data source name:** `CloudOpsResolutions`
6. **S3 URI:** `s3://cloudops-kb-YOURNAME-2024/resolutions/`  
   *(use your actual bucket name)*
7. Click **Next**

**Embeddings model:**
8. Select **Amazon Titan Embeddings V2** (or Titan Text Embeddings v1)
9. Click **Next**

**Vector store:**
10. Select **Amazon OpenSearch Serverless** (AWS manages this automatically)  
    > Note: If you want pure S3 vector storage, select **Amazon Aurora** or keep OpenSearch Serverless — AWS Bedrock KB manages the vector index for you, you just provide S3 as the data source. The vectors are stored in the managed vector store, not in S3 directly.
11. Click **Next → Create knowledge base**

Wait ~5 minutes for creation.

### 6c. Note down the IDs

After creation, on the Knowledge Base detail page:
- **Knowledge base ID** (looks like: `ABCDEF1234`) — copy this
- Click **Data sources** tab → click your data source
- **Data source ID** (looks like: `GHIJKL5678`) — copy this

### 6d. Start the first ingestion

1. On the Knowledge Base page, click **Sync** (or **Start ingestion job**)
2. Wait for status to show **Complete**

---

## 7. Enable Bedrock Model Access

1. Go to **AWS Console → Amazon Bedrock → Model access**
2. Click **Manage model access**
3. Enable these models:
   - ✅ **Amazon Nova Micro** (`us.amazon.nova-micro-v1:0`) — used by the domain classifier inside AgentCore
   - ✅ **Amazon Nova Pro** (`us.amazon.nova-pro-v1:0`) — used by the Action Agent
   - ✅ **Amazon Nova Lite** (`us.amazon.nova-lite-v1:0`) — optional fallback
   - ✅ **Amazon Titan Embeddings V2**
4. Click **Save changes**

Wait ~5 minutes for access to be granted (status shows **Access granted**).

---

## 8. Run Infrastructure Setup Script

This script creates remaining AWS resources automatically.

### 8a. Set up local Python environment

```bash
# From your project root directory
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Mac/Linux)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents
```

### 8b. Run the setup script

```bash
python scripts/setup_aws_infrastructure.py
```

This will:
- Create DynamoDB tables (if not already done)
- Create S3 bucket (if not already done)
- Create IAM roles for AgentCore
- Create EC2 security group
- Save configuration to `config.py` and `deployment_info.json`

**If the script fails on Knowledge Base creation** — that's OK, you already created it manually in Step 6. The script will set `KNOWLEDGE_BASE_ID = 'PLACEHOLDER'` which you'll update in Step 10.

---

## 9. Deploy AgentCore Agent

### 9a. Configure AgentCore

```bash
# Make sure your venv is active
agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
```

This creates `.bedrock_agentcore.yaml` and the Dockerfile.

### 9b. Deploy the agent

```bash
agentcore deploy
```

This will:
1. Build a Docker container using AWS CodeBuild (no local Docker needed)
2. Push to Amazon ECR
3. Deploy to Bedrock AgentCore

**This takes 10–15 minutes.** Watch the output for the Agent ARN.

When complete, you'll see something like:
```
✅ Agent deployed successfully!
Agent ARN: arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/src_agentcore_entrypoint-XXXXXXXX
```

**Copy the Agent ARN** — you need it in Step 10.

### 9c. Test the agent

**PowerShell (Windows) — use a variable to avoid quoting issues:**
```powershell
$p = '{"prompt":"ListEC2instances"}'
agentcore invoke $p
```

> ⚠️ PowerShell splits arguments on spaces inside strings passed to external commands.  
> **Rule:** keep the JSON value free of spaces, OR use the variable approach above.

**More realistic test with a space-free prompt:**
```powershell
$p = '{"prompt":"analyze-EC2-health"}'
agentcore invoke $p
```

**bash / Linux / Mac:**
```bash
agentcore invoke '{"prompt": "List my EC2 instances"}'
```

---

## 10. Update Configuration Values

Open `config.py` in your editor and replace all placeholder values:

```python
# config.py — UPDATE THESE VALUES

# From Step 4 (S3 bucket you created)
S3_BUCKET_NAME = "cloudops-kb-YOURNAME-2024"   # ← your actual bucket name

# From Step 6c (Knowledge Base IDs)
KNOWLEDGE_BASE_ID = "ABCDEF1234"               # ← your KB ID
DATA_SOURCE_ID = "GHIJKL5678"                  # ← your data source ID

# From Step 9b (AgentCore deploy output)
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/src_agentcore_entrypoint-XXXXXXXX"

# These stay the same
AWS_REGION = "us-east-1"
TICKETS_TABLE = "CloudOpsTickets"
USERS_TABLE = "CloudOpsUsers"
TENANTS_TABLE = "CloudOpsTenants"
DEFAULT_TENANT_ID = "demo-tenant-001"
DEFAULT_USER_ID = "demo-user"

# Cross-account role name the agent assumes in each managed target account.
# Leave as the default unless you provisioned the role under a different name.
# For a single-account deployment (the agent manages only its own account),
# you can leave this at the default — it is only used when the agent operates
# across accounts.
CROSS_ACCOUNT_ROLE_NAME = "CloudOpsCrossAccountRole"
```

Also update the Agent ARN in `config.py`:

```python
# config.py
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/src_agentcore_entrypoint-XXXXXXXX"
# ↑ Replace with your actual Agent ARN
```

And in `src/agents/action_agent.py`:

```python
# src/agents/action_agent.py — line ~30
self.agent_arn = os.environ.get(
    "AGENTCORE_AGENT_ARN",
    "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/src_agentcore_entrypoint-XXXXXXXX"
    # ↑ Replace with your actual Agent ARN
)
```

---

## 11. Launch EC2 Instance for Streamlit Apps

### 11a. Create EC2 Key Pair

1. Go to **AWS Console → EC2 → Key Pairs → Create key pair**
2. **Name:** `cloudops-key`
3. **Key pair type:** RSA
4. **Private key file format:** `.pem` (for Mac/Linux) or `.ppk` (for Windows PuTTY)
5. Click **Create key pair** — the file downloads automatically
6. **Save this file securely** — you cannot download it again

On Mac/Linux, set permissions:
```bash
chmod 400 ~/Downloads/cloudops-key.pem
```

### 11b. Create Security Group

1. Go to **AWS Console → EC2 → Security Groups → Create security group**
2. **Security group name:** `cloudops-streamlit-sg`
3. **Description:** `CloudOps Streamlit apps`
4. **VPC:** Select your default VPC

**Inbound rules — Add these 3 rules:**

| Type | Protocol | Port | Source | Description |
|------|----------|------|--------|-------------|
| Custom TCP | TCP | 8501 | 0.0.0.0/0 | Command Center |
| Custom TCP | TCP | 8502 | 0.0.0.0/0 | SRE Portal |
| SSH | TCP | 22 | My IP | SSH access |

> ⚠️ For production, restrict port 8501/8502 to your office IP range instead of 0.0.0.0/0

5. Click **Create security group**

### 11c. Launch EC2 Instance

1. Go to **AWS Console → EC2 → Instances → Launch instances**
2. **Name:** `cloudops-streamlit`
3. **AMI:** Amazon Linux 2023 (free tier eligible)
4. **Instance type:** `t3.medium` (2 vCPU, 4GB RAM — recommended for running 2 Streamlit apps)
   - `t3.small` works but may be slow
5. **Key pair:** Select `cloudops-key`
6. **Network settings:**
   - Click **Edit**
   - **Security group:** Select `cloudops-streamlit-sg`
7. **Storage:** 20 GB gp3 (default is fine)
8. **Advanced details → IAM instance profile:**
   - Click **Create new IAM profile** (opens new tab)
   - Create a role with these policies:
     - `AmazonBedrockFullAccess`
     - `AmazonDynamoDBFullAccess`
     - `AmazonS3FullAccess`
     - `CloudWatchFullAccess`
   - Name it `CloudOpsEC2Role`
   - Go back to launch wizard, refresh and select `CloudOpsEC2Role`
9. Click **Launch instance**

Wait ~2 minutes for the instance to show **Running** status.

**Note down the Public IPv4 address** from the instance details.

---

## 12. Deploy Streamlit Apps to EC2

### 12a. Connect to EC2 via SSH

**On Mac/Linux:**
```bash
ssh -i ~/Downloads/cloudops-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
```

**On Windows (using PowerShell):**
```powershell
ssh -i C:\Users\YourName\Downloads\cloudops-key.pem ec2-user@YOUR_EC2_PUBLIC_IP
```

**On Windows (using PuTTY):**
- Host: `YOUR_EC2_PUBLIC_IP`
- Connection → SSH → Auth → Browse for your `.ppk` file

### 12b. Upload project files to EC2

**Option A: Upload via SCP (from your local machine, NOT inside SSH)**

Open a new terminal on your local machine:
```bash
# Mac/Linux
scp -i ~/Downloads/cloudops-key.pem -r /path/to/your/project/* ec2-user@YOUR_EC2_PUBLIC_IP:/tmp/cloudops/

# Windows PowerShell
scp -i C:\Users\YourName\Downloads\cloudops-key.pem -r C:\path\to\project\* ec2-user@YOUR_EC2_PUBLIC_IP:/tmp/cloudops/
```

**Option B: Upload via S3 (recommended for large projects)**

On your local machine:
```bash
# First upload to S3
python scripts/upload_project_to_s3.py --bucket cloudops-kb-YOURNAME-2024
```

Then on the EC2 instance (inside SSH):
```bash
mkdir -p /opt/cloudops
cd /opt/cloudops
aws s3 cp s3://cloudops-kb-YOURNAME-2024/cloudops-project.zip .
unzip -q cloudops-project.zip
```

### 12c. Run the deployment script

Inside your SSH session on EC2:
```bash
# Move files to the right place (if using SCP option)
sudo mkdir -p /opt/cloudops
sudo cp -r /tmp/cloudops/* /opt/cloudops/
sudo chown -R ec2-user:ec2-user /opt/cloudops

# Run deployment
cd /opt/cloudops
bash scripts/deploy_to_ec2.sh
```

The script will:
1. Install Python, pip, AWS CLI
2. Create a virtual environment
3. Install all dependencies
4. Create systemd services for both Streamlit apps
5. Start both services automatically

### 12d. Verify services are running

```bash
sudo systemctl status cloudops-command-center
sudo systemctl status cloudops-sre-portal
```

Both should show `active (running)`.

### 12e. Access your apps

Open in your browser:
- **Command Center:** `http://YOUR_EC2_PUBLIC_IP:8501`
- **SRE Portal:** `http://YOUR_EC2_PUBLIC_IP:8502`

---

## 13. Verify Everything Works

### Test 1: Command Center loads
1. Open `http://YOUR_EC2_PUBLIC_IP:8501`
2. You should see the sign-in form
3. Enter any email, tenant ID `demo-tenant-001`, role `operator`
4. Click **Sign In**
5. The dashboard should load with metrics

### Test 2: Submit a query
1. In the chat input, type: `List my EC2 instances`
2. The agent should respond (may take 10–20 seconds first time)

### Test 3: SRE Portal loads
1. Open `http://YOUR_EC2_PUBLIC_IP:8502`
2. Select `SRE-OnCall-1` from the sidebar
3. You should see the ticket dashboard

### Test 4: Create and resolve a ticket
1. In Command Center, type: `I need help with a database issue`
2. The system should escalate and create a ticket
3. Go to SRE Portal — the ticket should appear
4. Add a resolution and close it

### Test 5: Learning loop
1. In SRE Portal, sign in as `Admin`
2. Click **Trigger AI Knowledge Sync** in the sidebar
3. Should complete without errors

### Test 6: AgentCore directly
```bash
# On your local machine
agentcore invoke '{"prompt": "analyze EC2 instance i-0123456789abcdef0"}'
```

---

## 14. Troubleshooting

### App won't start on EC2

```bash
# Check logs
sudo journalctl -u cloudops-command-center -n 50
sudo journalctl -u cloudops-sre-portal -n 50

# Common fix: missing dependencies
cd /opt/cloudops
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart cloudops-command-center
```

### "No module named 'src'" error

```bash
# Make sure you're running from the project root
cd /opt/cloudops
# Check the service file WorkingDirectory is correct
sudo cat /etc/systemd/system/cloudops-command-center.service
```

### AWS credentials not working on EC2

The EC2 instance should use the IAM role (no credentials needed). Verify:
```bash
aws sts get-caller-identity
# Should show the EC2 role ARN, not your personal user
```

If it shows your personal user, the IAM instance profile wasn't attached. Go to EC2 Console → select instance → Actions → Security → Modify IAM role → select `CloudOpsEC2Role`.

### Bedrock access denied

```bash
# Test Bedrock access
aws bedrock list-foundation-models --region us-east-1
```

If this fails, check:
1. Model access is enabled (Step 7)
2. IAM role has `AmazonBedrockFullAccess`
3. You're in the right region

### DynamoDB table not found

```bash
# List tables
aws dynamodb list-tables --region us-east-1
```

If tables are missing, run:
```bash
python scripts/setup_aws_infrastructure.py
```

### AgentCore deploy fails

```bash
# Check CodeBuild logs in AWS Console
# Go to: CodeBuild → Build history → find your build → View logs
```

Common issues:
- Missing `bedrock-agentcore` in requirements.txt (already included)
- Wrong entrypoint path — verify `src/agentcore_entrypoint.py` exists
- IAM permissions — ensure your user has `AmazonBedrockFullAccess` and `AmazonEC2ContainerRegistryFullAccess`

### `agentcore invoke` — "Got unexpected extra arguments" on Windows

PowerShell splits on spaces inside strings passed to external commands. Use a variable:
```powershell
# WRONG — PowerShell splits "List my EC2 instances" into separate args
agentcore invoke '{"prompt": "List my EC2 instances"}'

# CORRECT — assign to variable first
$p = '{"prompt":"ListEC2"}'
agentcore invoke $p

# CORRECT — or use --% to stop PowerShell parsing
agentcore invoke --% '{"prompt": "List my EC2 instances"}'
```

### Port not accessible

Check the security group allows inbound on 8501 and 8502:
```bash
# From EC2 instance, verify apps are listening
ss -tlnp | grep -E '8501|8502'
```

If listening but not accessible externally, check the EC2 security group inbound rules in the console.

### Knowledge Base returns no results

1. Verify ingestion completed: Bedrock Console → Knowledge bases → your KB → Data sources → check status
2. Re-trigger sync: click **Sync** button
3. Check S3 bucket has files in the `resolutions/` prefix

---

## Quick Reference: Values to Change

| File | Variable | Where to get the value |
|------|----------|----------------------|
| `config.py` | `S3_BUCKET_NAME` | Step 4 — bucket you created |
| `config.py` | `KNOWLEDGE_BASE_ID` | Step 6c — KB detail page |
| `config.py` | `DATA_SOURCE_ID` | Step 6c — data source detail |
| `config.py` | `AGENT_ARN` | Step 9b — agentcore deploy output |
| `src/agents/action_agent.py` | `self.agent_arn` | Step 9b — same Agent ARN |

---

## Cost Estimate (Monthly)

| Service | Usage | Estimated Cost |
|---------|-------|----------------|
| EC2 t3.medium | 24/7 | ~$30/month |
| DynamoDB | On-demand, low traffic | ~$1–5/month |
| S3 | < 1 GB | < $1/month |
| Bedrock Nova Micro | ~1000 domain classifications | < $1/month |
| Bedrock Nova Pro | ~1000 queries (action agent) | ~$3–10/month |
| Bedrock Knowledge Base | Managed vector store | ~$5–10/month |
| AgentCore | Per invocation | ~$5–15/month |
| **Total** | | **~$42–64/month** |

> Stop the EC2 instance when not in use to save costs.

---

*Last updated: May 2026*
