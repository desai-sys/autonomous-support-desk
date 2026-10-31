# Product Overview

CloudOps Command Center: operators chat with an AI agent that inspects and operates their AWS
accounts. It investigates with 285 AWS tools, deploys uploaded code (Lambda, EC2, ECS Fargate,
S3 websites), works across many accounts, and escalates what it can't handle to human SREs whose
resolutions train a knowledge base.

Core principle: the agent can propose changes but never perform them on its own. Every mutating
tool call is queued (src/core/approvals.py) and runs exactly once, with its stored arguments, only
after a person approves it in the console.

See README.md, docs/ARCHITECTURE.md and docs/DEPLOYMENT.md.
