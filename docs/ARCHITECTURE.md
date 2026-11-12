# Architecture

![Architecture](architecture.png)

For a narrative walkthrough of why each part exists, see [BLOG.md](BLOG.md); for how the agent is
measured, see [EVALUATION.md](EVALUATION.md).

The diagram uses the official AWS Architecture Icons and the standard AWS group styles
(AWS Cloud, account, Region, VPC, Availability Zone, subnets), with numbered steps explained
in its legend. It's generated from [`architecture.py`](architecture.py) as
[`architecture.svg`](architecture.svg) (scalable, for slides and docs) and `architecture.png`.
Regenerate both with `python docs/architecture.py` (needs the dev requirements and
`playwright install chromium` for the PNG).

## Components

| Component | Runs on | Code | Responsibility |
|---|---|---|---|
| Console | ECS Fargate behind ALB + CloudFront | `console/` (FastAPI), `web/` (React) | Sign-in (Cognito OIDC, signed session cookie), chat, account and region targeting, code upload, approval cards, tickets, account registry, audit log |
| Gateway | Inside the console process | `src/agents/gateway.py` | Calls the agent (`InvokeAgentRuntime`); falls back to the knowledge base when the agent escalates; rejects actions and lists pending ones |
| Agent | Bedrock AgentCore Runtime (Python 3.12, code deployment) | `agent_main.py`, `src/agentcore_entrypoint.py` | Routes each prompt to 1–2 tool domains, runs a Strands agent per target account, queues changes, and executes approved ones |
| Domain router | Inside the agent | `src/agents/router.py` | One Nova Micro call classifies the prompt into domains (compute, storage, network, database, security, ops, deployment) |
| Tools | Inside the agent | `src/tools/` | 290 boto3 tools; their clients are account-aware through `src/core/aws_clients.py`. Every domain also has `list_aws_operations` and `describe_aws_operation` (exact current operation names and parameters, read from the installed SDK's API definitions, so the model doesn't rely on possibly outdated memory), `query_aws_api` (any read-only AWS operation, secret-returning calls blocked) and `call_aws_api` (any other AWS operation, always approval-gated), so the agent is never stuck for lack of a purpose-built tool |
| Approval engine | Inside the agent | `src/core/approvals.py` | Intercepts mutating tool calls, stores them, and executes approved ones exactly once |
| Account registry | DynamoDB `accounts` | `src/core/accounts.py` | Allow-list of accounts the agent may assume roles in |
| Learning loop | Scheduled ECS task (hourly) | `src/jobs/learning_loop.py` | Writes resolved tickets, with tenant metadata, to the knowledge bucket and starts ingestion |
| Knowledge base | Bedrock KB on S3 Vectors | `src/services/rag_service.py` | Tenant-filtered retrieval over past resolutions |

## Agent pattern

The agent follows a **router → specialist → gated tools** pattern.

1. **Route.** Nova Micro picks 1–2 of eight domains (compute, storage, network, database,
   security, ops, deployment, build), so the model sees about 30–65 tool schemas instead of 290. An attached code upload always adds
   `deployment`. If the reply shows a needed tool was missing, the agent retries
   once with one extra domain.
2. **Reason.** A Strands `Agent` (Nova Pro) runs with the merged tools, a
   domain prompt, and context notes. These cover the target account, the
   selected region, any uploaded artifact, and read-only mode for viewers.
3. **Act through a gate.** Read-only tools (names starting with `list_`,
   `analyze_`, `get_`, `describe_`, `check_`, `inspect_`, `search_`, `detect_`,
   `validate_`, `lookup_`, or `prepare_`) run immediately. Any other tool call
   is intercepted by `ApprovalGate`, a Strands `BeforeToolCallEvent` hook. The
   hook records the call as a pending action and cancels it, and the model is
   told the call was queued, not that it ran.
4. **Work across accounts in one run.** Every tool the agent sees is wrapped by
   `AccountScopedTool` (`src/core/account_scope.py`), which adds an optional `account` argument
   (`platform`, or a registered account's name or id). Each call runs inside `use_account(...)`,
   so every boto3 client it creates uses that account's assumed-role credentials. The system
   prompt lists the platform and registered accounts by name; the console's picker sets the
   default for calls that omit `account`. One turn can therefore compare accounts or prepare a
   change that spans them, such as peering a VPC in one account with a VPC in another. The approval gate records each queued call's own account, and the prompt forbids
   stating anything about an account that wasn't queried in that turn.

### Approval flow

```mermaid
sequenceDiagram
    actor Op as Operator
    participant C as Console
    participant A as Agent (AgentCore)
    participant M as Nova Pro
    participant D as DynamoDB actions
    participant T as Target account

    Op->>C: "stop i-0abc in prod"
    C->>A: invoke {mode: chat, account_ids: [prod], user, session}
    A->>M: prompt + tools (prod context)
    M->>A: call describe_* (read-only)
    A->>T: AssumeRole(ExternalId) + Describe
    M->>A: call stop_ec2_instance(i-0abc)
    A->>D: put pending action (tool, args, account, region, requester, TTL)
    A-->>M: "NOT EXECUTED YET: queued as action 7f3c…"
    M->>A: explanation + risk + rollback
    A->>C: response + pending_actions
    C->>Op: approval card (tool, args, account, region)
    Op->>C: Approve
    C->>A: invoke {mode: execute, action_ids: [7f3c…], user}
    A->>D: conditional update pending → executing (role, tenant, expiry, 4-eyes checks)
    A->>T: stop_ec2_instance(i-0abc) with stored args, in stored account
    A->>D: executing → succeeded / failed + result
    A->>C: result summary (audited)
```

Why it is safe:
- **No LLM in the execution step.** The stored call is replayed exactly as it
  was recorded.
- **Runs once.** A DynamoDB conditional write moves each action from
  `pending` to `executing`, so it can run only one time. Approvals expire after
  `ACTION_TTL_SECONDS` (one hour).
- **Checks on the approver.** The approver's role must be operator, SRE or
  admin, and their tenant must match. With `REQUIRE_SEPARATE_APPROVER`, the
  approver must also differ from the requester.
- **Fails closed.** If the queue write fails, the tool still does not run.
- **Prompt injection can't bypass it.** Instructions hidden in tool output can
  at most get a change *queued*, never executed.

## App builder

"Build me a URL shortener" is routed to the `build` domain. The agent:

1. Writes the code (under `src/`, standard library + AWS SDK only) and a CloudFormation template
   that takes `ArtifactBucket`/`ArtifactKey` parameters for the Lambda code location.
2. Writes the app one file per call with `prepare_app_file`, sets the template with
   `prepare_app_template`, then calls `prepare_app_bundle` (all read-only for the gate). Small,
   plain-text tool calls avoid the malformed tool calls that large JSON-in-JSON inputs provoke;
   if one still happens, the agent retries once. The bundle is validated: safe paths, size
   limits, at most 40 resources, no IAM users/access keys/Organizations/SSO/account-level
   resources, no administrator policies or `Action: "*"`. CloudFormation validates the template
   in the target account. The bundle is stored as an immutable version under
   `s3://<artifacts>/apps/<tenant>/<app_id>/v<N>/` (manifest, files, template, code.zip).
   `BUILD_MODEL_ID` (`--build-model`) can give this domain a stronger model than day-to-day
   operations use.
3. Calls `deploy_generated_app(app_id, version)`, which the gate queues. The console's approval
   card renders every file and the template from the stored version, so what you review is
   exactly what will be deployed.
4. On approval, the code zip is copied to the target account's staging bucket, the stack
   `cloudops-app-<app_id>` is created or updated (`CAPABILITY_IAM`, `CAPABILITY_AUTO_EXPAND`; never
   named IAM), and every URL output is called as a smoke test. The result includes the outputs
   and the smoke-test responses.

Changes produce a new version (`get_generated_app` loads the latest version into the draft → rewrite changed files → `prepare_app_bundle` → deploy). `delete_generated_app`
(also gated) removes the stack.

## Cross-account access

```
Agent runtime role (platform account)
   └── sts:AssumeRole + ExternalId ──► CloudOpsCrossAccountRole (each managed account)
                                          PowerUserAccess + scoped IAM, or ReadOnlyAccess
```

- The console's picker sends the operator's default account(s) on every turn, so follow-ups stay
  in the right place; accounts can also be named in the message ("compare prod and staging") and
  the agent passes them per tool call. Conversation history is never scanned for account ids.
- Independent changes across accounts appear as numbered approval cards; **Approve all in order**
  runs them top to bottom and stops at the first failure, leaving later steps pending. Approved
  steps can't pass outputs to each other, so when a step needs an id that only an earlier step
  produces, the agent queues the earlier step alone and proposes the next one once it has run.
- Workflows whose steps must happen together are single tools. `create_vpc_peering` is one
  approval that checks both VPCs in their own accounts, refuses overlapping CIDRs, requests the
  peering from the requester account (with the peer's owner id and region), accepts it from the
  accepter account, waits for `active`, and adds routes for the peer CIDR on both sides.
- Before a call is queued, `src/core/proposal_checks.py` refuses proposals that are known to fail,
  and tells the model what to do instead. Peering through `call_aws_api` is redirected to
  `create_vpc_peering`, and a VPC whose CIDR overlaps another VPC proposed in the same request is
  refused, with a free range suggested. Refused calls never become approval cards.
- Only accounts in the registry (plus the platform account) are used. Anything
  else is refused, with a message explaining how to register it.
- Assumed-role credentials are cached per account until 5 minutes before they
  expire.
- Uploaded code stays in the platform account's artifact bucket. The deploy
  tools read it with platform credentials, then create resources with the
  target account's credentials. EC2 instances download it through a one-hour
  presigned URL, so no cross-account bucket policy is needed.

## Data model

| Table | Key | Indexes / TTL | Contents |
|---|---|---|---|
| `<app>-actions` | `action_id` | GSI `by-session` (session_id); TTL `expires_at` | Pending, executing, succeeded, failed and rejected actions with tool, args, account, region, requester, approver, result |
| `<app>-audit` | `tenant_id`, `sk` = `<iso time>#<id>` | — (retained on delete) | Every security-relevant event, queried newest first per tenant |
| `<app>-tickets` | `ticket_id` | GSI `by-tenant` (tenant_id, created_at); sparse GSI `pending-vectorization` | Escalations and their resolutions (retained on delete) |
| `<app>-accounts` | `account_id` | — | Registered managed accounts |
| `<app>-usage` | `tenant_id`, `period` (YYYY-MM-DD) | TTL `expires_at` | Daily request counters for the per-tenant limit |
| `<app>-jobs` | `job_id` | TTL `expires_at` (24 h) | Console background jobs (agent turns, approvals) and their results, polled by the browser |

## Agent request contract

```jsonc
// chat
{"mode": "chat", "session_id": "...", "tenant_id": "...",
 "user": {"id": "...", "email": "...", "role": "operator"},
 "prompt": "<history + message>", "user_message": "<message>", "history": [],
 "account_ids": ["111122223333"], "region": "eu-west-1",
 "artifact": {"uri": "s3://.../app.zip", "filename": "app.zip", "target": "lambda", "options": {}}}
// → {"response", "domain", "accounts" (labels of accounts actually used), "refused_accounts", "pending_actions",
//    "escalate", "tools_available", "usage" ({"input", "output"} tokens), "execution_time_ms"}

// execute approved actions (in order; stop_on_failure leaves later ones pending after a failure)
{"mode": "execute", "action_ids": ["7f3c...", "9a1e..."], "stop_on_failure": true, "tenant_id": "...", "user": {...}}
// → {"response", "results": [{"action_id", "summary", "account_id", "region", "status", "result" | "error"}]}

// admin: verify a managed account's role
{"mode": "check_account", "account_id": "111122223333", "user": {"role": "admin", ...}}
```

Only the console's task role is allowed to invoke the runtime (IAM). The
console vouches for `user` from the signed Cognito session.

## Design decisions

| Decision | Why |
|---|---|
| AgentCore code deployment (S3 zip, Python 3.12) instead of a container | No image build for the agent; `deploy.py` installs Linux ARM64 wheels locally on any operating system |
| React single-page app + FastAPI JSON API in one container | Full control over design and interaction; the API reuses the Python services; one service, one sign-in, pages chosen by role |
| Server-side OIDC (Authlib) with a signed, HTTP-only session cookie | Cognito-hosted sign-in, MFA and password reset; no tokens in the browser; CSRF blocked by SameSite plus a required custom header |
| Slow requests (agent turns, approvals) run as background jobs stored in DynamoDB; the browser polls | CloudFront closes requests after 60 s; any console task can answer a poll, so tasks stay stateless |
| CloudFront in front of a CloudFront-only ALB | HTTPS without owning a domain, WAF, and an origin protected by both prefix list and secret header |
| Metrics as CloudWatch EMF log lines | No extra API calls on the request path |
| Approval gate as a Strands hook | Enforced in code for every tool, whatever the prompt says |
| API knowledge from the SDK's bundled models, refreshed weekly | The model looks up parameters instead of guessing; a weekly workflow bumps boto3/botocore, relocks and tests, so new AWS APIs arrive without manual work |
| Learning loop as a scheduled ECS task reusing the console image | No extra package to build |
