# Deploy CloudOps SRE Agent to AWS Bedrock AgentCore

## Prerequisites

```bash
pip install bedrock-agentcore-starter-toolkit
pip install bedrock-agentcore strands-agents boto3
```

Ensure your AWS credentials are configured:
```bash
aws configure
```

---

## Step 1: Install the AgentCore toolkit

```bash
pip install bedrock-agentcore-starter-toolkit
```

---

## Step 2: Configure the agent

From the project root (where `.bedrock_agentcore.yaml` lives):

```bash
agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
```

This sets up the deployment configuration pointing to the entrypoint that wraps all 275+ SRE tools across seven domain groups.

---

## Step 3: Test locally before deploying

Start the local dev server:
```bash
agentcore dev
```

In a second terminal, invoke it:
```bash
agentcore invoke --dev '{"prompt": "SSH is down on i-0abc123"}'
```

Expected response:
```json
{
  "response": "I'll analyze the security group for i-0abc123 first..."
}
```

---

## Step 4: Deploy to AgentCore Runtime

```bash
agentcore launch
```

This will:
- Build a container image from your code
- Push it to ECR
- Deploy it to AgentCore Runtime
- Return an Agent ARN

Note the Agent ARN from the output — it looks like:
```
arn:aws:bedrock-agentcore:us-east-1:<account>:agent/cloudops-sre-agent
```

---

## Step 5: Set the Agent ARN in your application

Set the environment variable so the action agent can find the deployed runtime:

```bash
export AGENTCORE_AGENT_ARN="arn:aws:bedrock-agentcore:us-east-1:<account>:agent/cloudops-sre-agent"
```

Or add it to your App Runner environment variables in the console.

---

## Step 6: Test the deployed agent

```bash
agentcore invoke '{"prompt": "Check unused EBS volumes"}'
```

---

## Step 7: Check status and manage

```bash
# Check deployment status
agentcore status

# Stop active session (frees resources)
agentcore stop-session

# Preview what destroy would remove
agentcore destroy --dry-run

# Tear down all resources
agentcore destroy
```

---

## IAM Permissions Required

The AgentCore execution role needs:
- `ec2:Describe*`, `ec2:Authorize*`, `ec2:Modify*`, `ec2:Create*`, `ec2:Delete*`, `ec2:Revoke*`
- `lambda:GetFunctionConcurrency`, `lambda:PutFunctionConcurrency`
- `s3:GetPublicAccessBlock`, `s3:PutPublicAccessBlock`
- `autoscaling:Describe*`, `autoscaling:SetDesiredCapacity`
- `rds:Describe*`, `rds:RebootDBInstance`
- `elasticloadbalancing:DescribeTargetHealth`
- `ssm:SendCommand`
- `wafv2:ListWebACLs`, `wafv2:GetSampledRequests`
- `dms:Describe*`, `dms:StartReplicationTask`, `dms:ReloadTables`
- `iam:PutRolePolicy`
- `sagemaker:List*`, `sagemaker:Delete*`
- `bedrock-agent:DeleteKnowledgeBase`
- `opensearchserverless:ListCollections`, `opensearchserverless:DeleteCollection`

---

## Cross-Account Operation (optional)

If the agent should manage AWS resources in accounts other than the one it runs
in, it assumes an IAM role in each target account. The role name is configured
in `config.py` via `CROSS_ACCOUNT_ROLE_NAME` (default `CloudOpsCrossAccountRole`).

Requirements:
- In **every target account**, create an IAM role named to match
  `CROSS_ACCOUNT_ROLE_NAME`. It must trust the AgentCore runtime role as
  principal and grant the permissions the tools need (e.g. `PowerUserAccess`).
- The **AgentCore runtime role** must be allowed to call `sts:AssumeRole` on
  those target-account roles.

For a single-account deployment (the agent only manages its own account), leave
`CROSS_ACCOUNT_ROLE_NAME` at the default and no extra roles are needed.

**Selecting a target account per request:** pass `account_id` / `accounts` (or
`account_ids`) in the invoke payload, or mention one or more 12-digit account
IDs in the prompt. Each account runs under its own assumed-role credentials;
multiple accounts fan out and the results are aggregated into one report (with
an `accounts` list and a `per_account` breakdown in the response). The runtime
injects an `ACCOUNT CONTEXT` note into the agent's system prompt for each
targeted account, so the agent states the account ID it acted on and will not
claim to have acted on a different account.

---

## Architecture After Deployment

```
Streamlit UI (App Runner)
    │
    ▼
Agent Gateway
    │
    ▼
ActionAgent.resolve()
    │  boto3 bedrock-agentcore-runtime.invoke_agent()
    ▼
AgentCore Runtime
    │  Strands Agent + 25 @tool functions
    ▼
Real AWS APIs
```
