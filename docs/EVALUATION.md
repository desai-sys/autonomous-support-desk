# Evaluating the agent

An operations agent is only useful in production if you can show two things: it **can't do
harm on its own**, and it **does the right thing often enough** to save people time. CloudOps
measures these separately, because they need different kinds of evidence.

| Layer | Question | Evidence | Where |
|---|---|---|---|
| Guarantees | Can the agent ever change AWS without a person approving the exact call? | Deterministic tests that drive a real Strands agent with scripted (adversarial) models | `tests/` (run in CI) |
| Behaviour | How often does it choose the right tools, accounts and changes, and answer truthfully? | Scenario evaluation against the real Bedrock models | `evals/` (run on demand in your account) |
| Production | Is it healthy and affordable right now? | Metrics, alarms and the audit log | CloudWatch dashboard `<app>-<env>` |

---

## 1. Guarantees (deterministic, every commit)

These hold regardless of what the model says, because they're enforced in code. Each is tested
with a real Strands `Agent` driven by a scripted model that tries to misbehave:

| Guarantee | How it's enforced | Tests |
|---|---|---|
| A mutating tool never runs during a chat turn | `ApprovalGate` (`BeforeToolCallEvent` hook) cancels every non-read-only call and stores it as a pending action | `tests/test_core/test_approvals.py` |
| An approved action runs at most once, with the stored arguments, in the stored account | Conditional DynamoDB transition `pending → executing`; execution replays the stored call, no LLM involved; a run cut short is marked `interrupted`, never retried | `test_approved_action_runs_exactly_once…`, `test_execution_runs_in_the_recorded_account` |
| Viewers can't change anything; approvers must have the right role and tenant | Read-only tool sets for viewers; role/tenant checks at execution | `test_viewer_chat_is_read_only`, `test_execution_is_refused` |
| A second person approves (default; can be turned off for single-user setups) | `REQUIRE_SEPARATE_APPROVER` | `test_separate_approver_required` |
| The model's runtime cannot change resources | Planner IAM role is read-only and only assumes read-only roles; the executor has no model | `tests/test_infra.py`, `test_each_runtime_only_handles_its_own_requests` |
| An approval can't be used against the platform itself | IAM denies on the executor; proposals naming platform resources are refused | `tests/test_infra.py`, `test_platform_resources_cannot_be_changed` |
| A caller can't claim someone else's role or tenant | Console signs each request; runtimes verify and fail closed | `test_unsigned_or_altered_requests_are_rejected`, `test_a_missing_signing_key_fails_closed_outside_dev` |
| Open-ended reads can't have side effects or return secrets | `query_aws_api` allows metadata operations only, redacts secrets, and isn't given to viewers | `tests/test_tools/test_read_api_guard.py` |
| The account allow-list still holds when a change runs | Re-checked at execution | `test_execution_rechecks_the_account_and_tenant` |
| Lists are complete, or say they aren't | Auto-pagination with a reported cap | `tests/test_core/test_pagination.py` |
| Lost work ends in a clear state | Job heartbeat and recovery; interrupted actions | `tests/test_core/test_recovery.py` |
| Every tool runs without a programming error or an invalid API parameter | All 285 tools run against simulated AWS | `tests/test_tools/test_all_tools_smoke.py` |
| If the approval queue is down, nothing runs | The gate fails closed | `test_queue_failure_never_executes` |
| Expired or rejected actions never run | TTL + status checks | `test_expired_and_rejected_actions_do_not_run` |
| Calls only reach registered accounts | `AccountDirectory` refuses unknown accounts | `tests/test_core/test_account_scope.py` |
| Dependent cross-account steps stop at the first failure | `stop_on_failure` leaves later steps pending | `test_stop_on_failure_leaves_later_steps_pending` |
| Generated apps can't create IAM users, admin policies or `Action: "*"` | Bundle validation before a draft is stored | `tests/test_tools/test_app_builder.py` |
| Tenants can't see each other's tickets, drafts or knowledge | Tenant-scoped keys and knowledge-base filters | `tests/test_services/test_services.py` |

## 2. Behaviour (scenario evaluation)

`evals/run_evals.py` runs the scenarios in `evals/scenarios.yaml` through the **real** pipeline:
the Nova Micro router, the Nova Pro (or `--build-model`) agent, the approval gate and per-call
account scoping. Every AWS tool is swapped for a stub with the real name and schema that
returns fixture data, and changes are queued in memory, so **a run reads and changes nothing in
AWS; only Bedrock is called.**

### Metrics

| Metric | What it checks |
|---|---|
| **Task success rate** | Share of scenarios where every check passed |
| Routing accuracy | The router picked a domain that contains the needed tools |
| Tool selection | The agent called (or queued) an appropriate tool |
| Forbidden tool avoidance | It didn't call tools it shouldn't have |
| Action proposal accuracy | The expected change was queued for approval |
| Parameter accuracy | The queued change carries the right arguments (instance id, type, port, …) |
| Account targeting | Reads and queued changes landed in the right accounts |
| Read-only adherence | Questions didn't produce changes |
| Permission adherence | A viewer's request produced no changes |
| Prompt-injection resistance | Instructions hidden in tool output didn't turn into destructive proposals |
| Groundedness (accounts) | The answer only talks about accounts the agent actually queried |
| Answer quality (key facts) | The answer contains the facts from the tool results |
| App builder: app produced and passed checks | The pipeline produced an app version that passed every blocking check and queued its deployment |
| App builder: right blueprint | It chose the expected blueprint, and added storage only when the app saves data |
| Unsafe executions = 0 | No mutating tool ran during the turn (expected to be 100% by construction) |
| Latency p50 / p95 | Wall-clock seconds per scenario |
| Tokens per scenario | Input/output tokens (the agent also emits these as CloudWatch metrics) |

### Scenarios

21 scenarios in five categories: **investigate** (instances, public buckets, costs, MFA),
**change** (stop, launch, snapshot, resize), **cross-account** (compare two accounts, peer VPCs
across accounts, an unregistered account), **safety** (viewer changing or building, prompt
injection, opening SSH to the internet, an out-of-scope request) and **build** (a game, an app
that saves shared data, a scheduled job, changing an existing app, deleting one).

The build scenarios run the real build pipeline: the model plans and writes each file, and the
platform checks and repairs it. The generated app is kept in memory instead of S3 and is not
deployed. They are the way to find out how reliably your model builds apps, and whether a
different `--build-model` does better.

### Run it

It needs only Bedrock model access (no other AWS permissions):

```bash
python evals/run_evals.py --region us-east-1
```

```bash
python evals/run_evals.py --region us-east-1 --repeat 3
```

Results go to `evals/results/report.md` (a summary table plus per-scenario pass/fail) and
`report.json` (full trajectories: every tool call, queued action and answer). Models are
non-deterministic, so use `--repeat` to estimate stability, and compare models by changing
`AGENT_MODEL_ID` / `BUILD_MODEL_ID`. CI validates the scenario file on every commit
(`--offline`).

To use it as a release gate (as the deploy workflow does), add `--gate`: the run exits
non-zero unless every safety metric is 100%, and `--min-success 0.8` also requires a minimum
task success rate.

**Status:** the harness and scenarios are in place and tested, but no results from real models
are recorded here yet. Run the command above in your account to produce your baseline, and
re-run it whenever you change the model, prompts or tools.

### Extending it

Add a case to `evals/scenarios.yaml` with a prompt, the role, selected accounts, any fixtures,
and the `expect` checks (see the comments at the top of the file). Good sources are tickets the
agent escalated and approvals that people rejected.

## 3. Production signals

| Signal | Where |
|---|---|
| Requests, errors, p90 latency (agent chat/execute) | Dashboard `<app>-<env>`, metric namespace `CloudOps` |
| Approved actions succeeded/failed | Dashboard; alarm `FailedActionsAlarm` |
| Model tokens by model | `ModelInputTokens` / `ModelOutputTokens` metrics |
| Escalations | Tickets page; each one is a candidate evaluation scenario |
| Rejected proposals | Audit log (`action_rejected`) — a direct measure of unwanted suggestions |
| Every proposal, approval and execution | Audit log, per tenant |
