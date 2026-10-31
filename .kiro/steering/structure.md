# Project Structure

## Root Directory
```
.
├── .kiro/                          # Kiro IDE configuration (steering, specs, hooks, settings)
├── .bedrock_agentcore/             # Auto-generated AgentCore config
│   └── src_agentcore_entrypoint/
│       └── Dockerfile              # ARM64 container definition
├── .bedrock_agentcore.yaml         # AgentCore deployment config
├── venv/                           # Python virtual environment (do not modify, do not commit)
├── src/                            # Application source code
│   ├── agentcore_entrypoint.py     # Multi-domain agent: DomainRouter + Nova Pro + BedrockAgentCoreApp
│   ├── agents/
│   │   ├── orchestrator.py         # AgentGateway — thin A2A layer + RAG fallback chain
│   │   ├── router.py               # DomainRouter — Nova Micro classifier (1-2 of 7 domains)
│   │   └── action_agent.py         # DEPRECATED wrapper, kept for tests only
│   ├── core/
│   │   ├── monitoring.py           # CloudWatch metrics, SLA tracking, @monitor_performance
│   │   ├── multi_tenant.py         # Tenant tiers, usage limits, billing tracking
│   │   └── security.py             # RBAC (UserRole/Permission), audit logging
│   ├── models/
│   │   ├── query.py                # Query and Resolution models + pad_session_id()
│   │   ├── ticket.py               # Ticket and TicketUpdate models
│   │   └── metrics.py              # Dashboard metrics model
│   ├── services/
│   │   ├── dynamo_service.py       # DynamoDB CRUD for CloudOpsTickets
│   │   └── rag_service.py          # Bedrock Knowledge Base retrieval
│   └── tools/                      # 275+ @tool functions across 7 domain modules
│       ├── compute_tools.py        # EC2, Lambda, Auto Scaling, ECS
│       ├── storage_tools.py        # S3, EBS, EFS
│       ├── network_tools.py        # VPC, Security Groups, Route53, CloudFront, ALB, API GW
│       ├── database_tools.py       # RDS, DynamoDB, ElastiCache, DocumentDB
│       ├── security_tools.py       # IAM, Secrets Manager, KMS, WAF, GuardDuty
│       ├── deployment_tools.py     # Lambda/ECS/EC2/App Runner/S3/CFN deploy + rollback
│       └── aws_tools.py            # All remaining AWS services (150+ tools) + search/self-description
├── tests/                          # pytest suite mirroring src/ structure
├── deployment/
│   └── deploy_agentcore.md         # AgentCore deployment guide
├── app.py                          # Streamlit Command Center UI
├── sre_portal.py                   # SRE Human-in-the-Loop resolution portal
├── learning_loop.py                # DynamoDB → S3 → Bedrock KB sync
├── config.py                       # Centralized deployment configuration
├── generate_architecture_diagram.py  # Generates cloudops_architecture.png (requires Graphviz)
├── requirements.txt
└── README.md
```

## Conventions

### Virtual Environment
- Location: `venv/` in project root
- Python version: 3.10+
- Never commit `venv/` to version control
- Activate before running: `./venv/Scripts/activate` (Windows)

### Configuration
- AWS credentials via `aws configure` or environment variables
- No `.env` file — deployment-specific values live in `config.py` (`AGENT_ARN`, `KNOWLEDGE_BASE_ID`,
  `DATA_SOURCE_ID`, `S3_BUCKET_NAME`, `AWS_REGION`, `TICKETS_TABLE`)
- The deprecated `src/agents/action_agent.py` reads `AGENTCORE_AGENT_ARN` from the environment

### Data and Persistence
- DynamoDB table `CloudOpsTickets` is the primary ticket store (AWS-managed)
- `CloudOpsUsers`, `CloudOpsTenants`, `CloudOpsUsage`, `CloudOpsAuditLog` support multi-tenancy and audit
- S3 bucket stores resolution documents for Bedrock KB ingestion
- All data is stored in AWS managed services (no local data directories)

### Agent Tools
- 275+ SRE tools live in `src/tools/` and are imported by `src/agentcore_entrypoint.py`, which groups
  them into seven domains and loads only the 1-2 domains the `DomainRouter` selects per prompt
- All tools run inside the AgentCore container

### Testing
- Tests live in `tests/`, mirroring `src/` structure (`test_agents/`, `test_models/`, `test_services/`)
- Use pytest as the testing framework

### Documentation
- `README.md` — root-level system overview, architecture, and flow (source of truth)
- `AGENT_CAPABILITIES.md` — sample queries for every tool, grouped by domain
- `DEPLOYMENT_GUIDE.md` — from-scratch AWS setup and deployment
- `MIGRATION_GUIDE.md` — redeploying to a different AWS account (per-account variables + commands)
- `deployment/deploy_agentcore.md` — AgentCore-specific deployment steps
- `ENTERPRISE_DEPLOYMENT.md`, `ENTERPRISE_FEATURES_SUMMARY.md`, `TECHNICAL_BLOG.md` — supporting docs
