# Project Structure

```
agent_main.py              AgentCore entry point
config.py                  All settings from environment variables (no hard-coded IDs)
src/agentcore_entrypoint.py  Agent: router -> Strands agent per account; modes chat / execute / check_account
src/agents/                router.py (Nova Micro), gateway.py (console -> agent client)
src/core/                  approvals.py, accounts.py, aws_clients.py, security.py, usage.py, monitoring.py
src/services/              ticket_service.py, rag_service.py, artifact_service.py
src/jobs/learning_loop.py  Hourly knowledge-base sync (scheduled ECS task)
src/tools/                 @tool functions in seven domains; import boto3 from src.core.aws_clients
console/                   FastAPI console server: server.py, auth.py (OIDC/dev), api.py (/api/*), jobs.py
web/                       React + TypeScript + Tailwind app (pages/, components/); built into console/static
infra/                     foundation.yaml, app.yaml, cross_account_role.yaml
scripts/                   deploy.py, destroy.py
docs/                      DEPLOYMENT.md, ARCHITECTURE.md, architecture.py (diagram)
tests/                     pytest + moto; tables are created from infra/foundation.yaml
```

## Conventions
- Tool names that only read state start with list_/analyze_/get_/describe_/check_/inspect_/
  search_/detect_/validate_/lookup_/prepare_; anything else is treated as mutating and gated.
- Tools must create AWS clients via `from src.core.aws_clients import boto3` so cross-account works.
- New settings go in config.py (env var) and both CloudFormation stacks where needed.
- New DynamoDB tables/indexes go in infra/foundation.yaml (tests read it).
