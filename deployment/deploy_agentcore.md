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

**1. In every target account**, deploy `deployment/cross_account_role.yaml`
(or roll it out to a whole AWS Organization as a StackSet). It creates the role the
agent assumes, trusting only the AgentCore runtime role, plus the service roles the
deploy tools use (`cloudops-lambda-execution-role`, `cloudops-codebuild-role`,
`cloudops-ecs-execution-role`, `CloudOpsEC2SSMRole`):

```bash
aws cloudformation deploy \
  --template-file deployment/cross_account_role.yaml \
  --stack-name cloudops-cross-account \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides AgentRuntimeRoleArn=<agentcore-runtime-role-arn> AccessLevel=PowerUser
```

Use `AccessLevel=ReadOnly` for accounts the agent should only inspect.

**2. In the platform account**, allow the AgentCore runtime role to assume those
roles and to read uploaded code:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "sts:AssumeRole",
     "Resource": "arn:aws:iam::*:role/CloudOpsCrossAccountRole"},
    {"Effect": "Allow", "Action": ["s3:GetObject"],
     "Resource": "arn:aws:s3:::cloudops-artifacts-<UNIQUE_ID>/uploads/*"}
  ]
}
```

**3. Optionally list the accounts** in `config.py` `MANAGED_ACCOUNTS`
(`{"111122223333": "prod", ...}`) so they appear by name in the Command Center's
**Target Accounts** picker. Any other 12-digit ID can be added from the sidebar.

For a single-account deployment (the agent only manages its own account), no
extra roles are needed; the runtime role just needs the step 2 S3 permission if you
use code upload.

**Selecting a target account per request:** the Command Center sends the accounts
picked in the sidebar as `account_ids` on every turn, so a confirmation such as
"yes, go ahead" runs in the same account the plan was made for (an empty list means
the runtime's own account). Other callers can pass `account_id` / `accounts` /
`account_ids` in the invoke payload; when none of those keys is present, 12-digit IDs
in the latest user message (`user_message`, not the conversation history) are used.
Each account runs under its own assumed-role credentials; multiple accounts fan out
and the results are aggregated into one report (with an `accounts` list and a
`per_account` breakdown in the response). The runtime injects an `ACCOUNT CONTEXT`
note into the agent's system prompt for each targeted account, so the agent states
the account ID it acted on and will not claim to have acted on a different account.

---

## Code Upload (zip deployments)

The Command Center's **📦 Deploy code (.zip)** menu uploads a zip to
`config.ARTIFACT_BUCKET` (created on first upload: private, encrypted, uploads
expire after 7 days) and passes its `s3://` URI to the agent as `artifact` in the
invoke payload. The agent inspects the zip, proposes a plan, and after confirmation
deploys it with:

| Target | Tool | Notes |
|---|---|---|
| Lambda | `deploy_artifact_to_lambda` | Runtime and handler auto-detected; >50 MB packages are re-staged in the target account |
| EC2 (existing) | `deploy_artifact_to_ec2` | Instance must be SSM-managed Linux; it downloads the zip via a 1-hour presigned URL, extracts it and optionally runs a command |
| EC2 (new) | `launch_ec2_for_artifact` | Amazon Linux 2023 with the `CloudOpsEC2SSMRole` instance profile |
| ECS Fargate | `deploy_artifact_to_ecs` | Zip needs a root `Dockerfile`; container listens on port 80 |
| S3 website | `deploy_artifact_to_static_site` | Zip needs a root `index.html`; bucket is made public |

The zip is always read with platform-account credentials and the resources are
created with the target account's assumed role, so one upload can be deployed to any
managed account. The process hosting the Command Center needs `s3:CreateBucket`,
`s3:PutBucketPublicAccessBlock`, `s3:PutLifecycleConfiguration` and `s3:PutObject`
on the artifact bucket.

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
