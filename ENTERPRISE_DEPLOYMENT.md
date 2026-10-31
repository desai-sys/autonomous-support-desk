# Enterprise CloudOps Command Center - Deployment Guide

## Overview

The Enterprise CloudOps Command Center is a comprehensive AI-powered SRE platform with 275+ AWS tools across seven domain groups, multi-tenancy, monitoring, security, and enterprise-grade features.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Enterprise CloudOps Platform                 │
├─────────────────────────────────────────────────────────────────┤
│  Frontend Layer                                                 │
│  ├── Streamlit Command Center (app.py)                         │
│  ├── SRE Resolution Portal (sre_portal.py)                     │
│  └── Admin Dashboard                                            │
├─────────────────────────────────────────────────────────────────┤
│  Orchestration Layer                                            │
│  ├── Agent Gateway (A2A + RAG fallback)                        │
│  ├── Security & RBAC                                           │
│  └── Multi-Tenant Usage Tracking                               │
├─────────────────────────────────────────────────────────────────┤
│  Agent Layer (inside AgentCore container)                       │
│  ├── Nova Micro Domain Router (1-2 of 7 domains)               │
│  └── Nova Pro Action Agent (275+ tools, domain-focused subset) │
├─────────────────────────────────────────────────────────────────┤
│  Knowledge Layer                                                │
│  ├── Bedrock Knowledge Base (RAG fallback)                     │
│  └── Learning Loop                                             │
├─────────────────────────────────────────────────────────────────┤
│  Data Layer                                                     │
│  ├── DynamoDB (Tickets, Users, Tenants, Usage, AuditLog)      │
│  ├── S3 (Knowledge Base Documents)                             │
│  └── OpenSearch Serverless (Vector Search)                     │
├─────────────────────────────────────────────────────────────────┤
│  Monitoring & Security                                          │
│  ├── CloudWatch (Metrics, Logs, Dashboards)                   │
│  ├── GuardDuty (Security Monitoring)                          │
│  └── Audit Logging (DynamoDB)                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Features

### Core Capabilities
- **275+ AWS Tools**: Comprehensive coverage across all major AWS services
- **Domain-Routed Agent**: Nova Micro router selects 1-2 of 7 domains per query
- **Nova Pro Reasoning**: Focused tool subset per prompt keeps reasoning fast
- **Agent-to-Agent Communication**: Scalable A2A architecture
- **Human-in-the-Loop**: Safety controls with confirmation gates

### Enterprise Features
- **Multi-Tenancy**: Tenant isolation with usage tracking and tiered limits
- **RBAC**: Role-based access control with granular permissions
- **Monitoring**: Real-time CloudWatch metrics, SLA tracking, and dashboards
- **Security**: Comprehensive audit logging and compliance
- **Serverless Scaling**: AgentCore and DynamoDB on-demand scale automatically

### Tool Categories (7 domain groups)
- **Compute**: EC2, Lambda, ECS, EKS, Auto Scaling, Batch, SageMaker, App Runner, Lightsail
- **Storage**: S3, EBS, EFS, ECR, Backup, DataSync, Transfer Family
- **Network**: VPC, Route53, CloudFront, ALB/NLB, API Gateway, SNS, SQS, Kinesis, MSK
- **Database**: RDS, DynamoDB, ElastiCache, DocumentDB, Redshift, Neptune, OpenSearch, Glue, Athena
- **Security**: IAM, KMS, Secrets Manager, WAF, GuardDuty, Security Hub, Inspector, Macie, Config
- **Cost & Ops**: CloudWatch, CloudFormation, CodePipeline, Cost Explorer, Systems Manager, Step Functions
- **Deployment**: Lambda, ECS/ECR, EC2 (SSM), App Runner, S3 static sites, CloudFormation/SAM

## Prerequisites

### AWS Account Setup
1. AWS Account with appropriate permissions
2. AWS CLI configured with credentials
3. Bedrock model access enabled (Amazon Nova Micro + Amazon Nova Pro)
4. AgentCore service enabled in your region

### Required AWS Services
- AWS Bedrock (AgentCore Runtime)
- Amazon DynamoDB
- Amazon S3
- Amazon OpenSearch Serverless
- AWS Secrets Manager
- Amazon CloudWatch
- AWS Lambda (for automation)

### Development Environment
- Python 3.10+
- Node.js 18+ (for AgentCore CLI)
- Git
- Docker (optional, for local testing)

## Installation

### 1. Clone Repository
```bash
git clone <repository-url>
cd autonomous-support-desk
```

### 2. Create Virtual Environment
```bash
python -m venv venv

# Windows
./venv/Scripts/activate

# Linux/Mac
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
pip install bedrock-agentcore-starter-toolkit bedrock-agentcore strands-agents
```

### 4. Configure AWS Credentials
```bash
aws configure
# Enter your AWS Access Key ID, Secret Access Key, and region (us-east-1)
```

## Infrastructure Setup

### 1. Create DynamoDB Tables
```bash
# Create required DynamoDB tables
aws dynamodb create-table \
    --table-name CloudOpsTickets \
    --attribute-definitions AttributeName=ticket_id,AttributeType=S \
    --key-schema AttributeName=ticket_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

aws dynamodb create-table \
    --table-name CloudOpsUsers \
    --attribute-definitions AttributeName=user_id,AttributeType=S \
    --key-schema AttributeName=user_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

aws dynamodb create-table \
    --table-name CloudOpsTenants \
    --attribute-definitions AttributeName=tenant_id,AttributeType=S \
    --key-schema AttributeName=tenant_id,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST

aws dynamodb create-table \
    --table-name CloudOpsUsage \
    --attribute-definitions \
        AttributeName=tenant_id,AttributeType=S \
        AttributeName=period,AttributeType=S \
    --key-schema \
        AttributeName=tenant_id,KeyType=HASH \
        AttributeName=period,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST

aws dynamodb create-table \
    --table-name CloudOpsAuditLog \
    --attribute-definitions \
        AttributeName=event_id,AttributeType=S \
        AttributeName=timestamp,AttributeType=S \
    --key-schema \
        AttributeName=event_id,KeyType=HASH \
        AttributeName=timestamp,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST
```

### 2. Create S3 Bucket for Knowledge Base
```bash
aws s3 mb s3://your-cloudops-kb-bucket
```

### 3. Create Bedrock Knowledge Base
```bash
# This will be done through the AWS Console or via the learning loop
# The system will create tenant-specific knowledge bases automatically
```

### 4. Set Up IAM Roles
Create an IAM role for AgentCore with the following policies:
- PowerUserAccess (covers EC2, Lambda, S3, RDS, DynamoDB, ElastiCache, Route53, CloudFront, WAF, GuardDuty, CloudWatch, Secrets Manager, KMS, and all other non-IAM services)
- IAMReadOnlyAccess

> **Note:** A single `PowerUserAccess` policy is used instead of individual service policies to avoid hitting the AWS 10-managed-policy-per-role limit. This grants broad access to all AWS services except IAM write operations.

## Agent Deployment

### 1. Configure AgentCore
```bash
agentcore configure --entrypoint src/agentcore_entrypoint.py --non-interactive
```

### 2. Deploy Agent
```bash
agentcore deploy
```

### 3. Test Agent
```bash
agentcore invoke '{"prompt": "analyze EC2 instance i-0abc123", "tenant_id": "demo-tenant"}'
```

### 4. Update Configuration
Update the agent ARN in `config.py`:
```python
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:YOUR_ACCOUNT:runtime/YOUR_AGENT_ID"
```

## Application Configuration

### 1. Update Configuration Files
All deployment-specific values live in `config.py`. Update them after provisioning:

**config.py**:
```python
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:YOUR_ACCOUNT:runtime/YOUR_AGENT_ID"
KNOWLEDGE_BASE_ID = "YOUR_KB_ID"          # read by rag_service.py and learning_loop.py
DATA_SOURCE_ID = "YOUR_DATA_SOURCE_ID"    # read by learning_loop.py
S3_BUCKET_NAME = "your-cloudops-kb-bucket"
AWS_REGION = "us-east-1"
```

### 2. Set Environment Variables
```bash
export AGENTCORE_AGENT_ARN="arn:aws:bedrock-agentcore:us-east-1:YOUR_ACCOUNT:runtime/YOUR_AGENT_ID"
export CLOUDOPS_KB_BUCKET="your-cloudops-kb-bucket"
export CLOUDOPS_KB_ID="YOUR_KB_ID"
```

## Running the Application

### 1. Start Command Center
```bash
streamlit run app.py
```

### 2. Start SRE Portal (separate terminal)
```bash
streamlit run sre_portal.py --server.port 8502
```

### 3. Access Applications
- Command Center: http://localhost:8501
- SRE Portal: http://localhost:8502

## Multi-Tenancy Setup

### 1. Create Tenants
```python
from src.core.multi_tenant import tenant_manager, TenantTier

# Create enterprise tenant
tenant = tenant_manager.create_tenant(
    tenant_id="acme-corp",
    name="ACME Corporation",
    tier=TenantTier.ENTERPRISE,
    billing_contact="admin@acme.com"
)
```

### 2. Create Users
```python
from src.core.security import security_manager, User, UserRole

# This would typically be done through Cognito
# For demo purposes, users are created on first login
```

## Monitoring Setup

### 1. Create CloudWatch Dashboard
```python
from src.core.monitoring import monitoring_manager

dashboard_name = monitoring_manager.create_dashboard("acme-corp")
print(f"Created dashboard: {dashboard_name}")
```

### 2. Set Up Alerts
Configure CloudWatch alarms for:
- High resolution times (>5 minutes)
- Low success rates (<95%)
- API errors
- Agent failures

### 3. X-Ray Tracing (planned)
X-Ray tracing hooks are stubbed in `src/core/monitoring.py` (`trace_operation`) but not yet wired into the request path. No setup is required today.

## Security Configuration

### 1. Enable Encryption
- DynamoDB: Enable encryption at rest
- S3: Enable server-side encryption
- Secrets Manager: Store sensitive configuration

### 2. Configure VPC (Optional)
For enhanced security, deploy AgentCore in a VPC:
```bash
# Update .bedrock_agentcore.yaml with VPC configuration
```

### 3. Set Up WAF
Configure AWS WAF to protect the Streamlit applications:
```bash
# Create WAF rules for rate limiting and IP blocking
```

## Performance Optimization

The platform is serverless by default, so most scaling is automatic:
- **AgentCore Runtime** scales with invocation volume — no configuration needed
- **DynamoDB on-demand** scales with request load — no capacity planning
- **Domain routing** keeps the Nova Pro tool set small (~15-90 tools), which is the primary latency lever

### Optional / Future
- **ElastiCache (Redis) result caching** for hot RAG results — not yet implemented
- **S3 intelligent tiering** on the knowledge-base bucket to reduce storage cost
- **DynamoDB GSIs** if you add relational-style query patterns to the ticket store

## Maintenance

### 1. Regular Updates
```bash
# Update dependencies
pip install --upgrade -r requirements.txt

# Update AgentCore
agentcore deploy
```

### 2. Backup Strategy
- DynamoDB: Enable point-in-time recovery
- S3: Enable versioning and cross-region replication
- Knowledge Base: Regular exports

### 3. Monitoring
- Review CloudWatch metrics daily
- Check SLA compliance weekly
- Audit security logs monthly

## Troubleshooting

### Common Issues

1. **Agent Deployment Fails**
   - Check IAM permissions
   - Verify AgentCore service availability
   - Review CloudWatch logs

2. **Authentication Issues**
   - Verify Cognito configuration
   - Check user permissions
   - Review audit logs

3. **Performance Issues**
   - Check DynamoDB throttling
   - Review cache hit rates
   - Monitor agent response times

4. **Knowledge Base Issues**
   - Verify S3 permissions
   - Check ingestion job status
   - Review vector search configuration

### Logs and Debugging

- **Application Logs**: CloudWatch Logs
- **Agent Logs**: AgentCore CloudWatch Logs
- **Audit Logs**: DynamoDB CloudOpsAuditLog table
- **Performance Metrics**: CloudWatch Metrics

## Support

For enterprise support:
1. Check the troubleshooting guide
2. Review CloudWatch logs and metrics
3. Contact your AWS support team
4. Engage with the development team

## License

Enterprise License - Contact for licensing terms.