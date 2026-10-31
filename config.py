"""
CloudOps Platform Configuration
Generated on: 2026-06-28T14:41:23.437337
"""

# AWS Configuration
AWS_REGION = "us-east-1"
UNIQUE_ID = "6d55a7d0"

# S3 Configuration
S3_BUCKET_NAME = "cloudops-kb-6d55a7d0"

# Knowledge Base Configuration
KNOWLEDGE_BASE_ID = "PLACEHOLDER"
DATA_SOURCE_ID = "PLACEHOLDER"

# IAM Roles
KB_ROLE_ARN = "arn:aws:iam::741448928336:role/CloudOpsKnowledgeBaseRole-6d55a7d0"
AGENT_ROLE_ARN = "arn:aws:iam::741448928336:role/CloudOpsAgentCoreRole-6d55a7d0"

# EC2 Configuration
SECURITY_GROUP_ID = "sg-06d24da880413e8db"

# DynamoDB Tables
TICKETS_TABLE = "CloudOpsTickets"
USERS_TABLE = "CloudOpsUsers"
TENANTS_TABLE = "CloudOpsTenants"
USAGE_TABLE = "CloudOpsUsage"
AUDIT_TABLE = "CloudOpsAuditLog"

# Default Configuration
DEFAULT_TENANT_ID = "demo-tenant-001"
DEFAULT_USER_ID = "demo-user"

# Cross-Account Configuration
# Name of the IAM role the agent assumes in each TARGET account to perform
# actions there. This role must exist in every account you want the agent to
# manage, must trust the AgentCore runtime role as principal, and must grant
# the permissions the tools need (e.g. PowerUserAccess). See
# DEPLOYMENT_AND_FEATURES.md "Multi-account" for the exact trust policy.
CROSS_ACCOUNT_ROLE_NAME = "CloudOpsCrossAccountRole"

# Agent Configuration (update after deployment)
AGENT_ARN = "arn:aws:bedrock-agentcore:us-east-1:058264401886:runtime/src_agentcore_entrypoint-hlvOBNH0hy"

# Application Configuration
STREAMLIT_PORT_COMMAND_CENTER = 8501
STREAMLIT_PORT_SRE_PORTAL = 8502




