# Product Overview

Autonomous CloudOps Command Center — An agentic AI-powered SRE platform that autonomously detects, diagnoses, and fixes AWS infrastructure alerts in real time, drastically reducing Mean Time to Resolution (MTTR) and on-call burden.

The system combines a Nova Micro domain router, a Nova Pro reasoning agent with 275+ AWS tools organized into seven domain groups, a Bedrock Knowledge Base for historical resolution lookup, and a Human-in-the-Loop (HITL) confirmation layer.

## Core Modules & Business Value

1. **Streamlit Command Center (`app.py`):** Enterprise glassmorphism UI where operators submit alerts and queries. Uses Inter/JetBrains Mono typography with indigo/cyan accent palette. Streams agent responses character-by-character. Renders animated HITL warning boxes when confirmation is required. Shows a live status bar with connection state and UTC clock. Auto-creates DynamoDB tickets on escalation.

2. **Agent Gateway (`src/agents/orchestrator.py`):** The `AgentGateway` class sends every query to the Action Agent via A2A communication (`bedrock-agentcore.invoke_agent_runtime()`). It does no domain classification itself — that happens inside the AgentCore container. The RAG service is used only as a supplementary fallback when the agent responds with `ESCALATE`. Fallback chain: A2A Action Agent → RAG → human escalation. `MasterOrchestrator` and `AdvancedOrchestrator` are backward-compatible aliases for `AgentGateway`.

3. **Domain Router (`src/agents/router.py`):** The `DomainRouter` class makes a single Nova Micro (`us.amazon.nova-micro-v1:0`) LLM call to classify each query into 1-2 of seven domains: compute, storage, network, database, security, ops, deployment. Supports cross-service queries (e.g. "Lambda writing to S3" → `[compute, storage]`). Falls back to `["compute"]` on any classification failure.

4. **CloudOps Action Agent (`src/agentcore_entrypoint.py`):** Deployed as an independent AgentCore runtime. Uses the `DomainRouter` to select 1-2 domain tool groups per prompt, merges and deduplicates the tools, and passes the focused subset to a Nova Pro (`us.amazon.nova-pro-v1:0`) agent for reasoning. Auto-retries with an expanded domain (up to 3 total) when the agent signals no matching tool was found. Returns `ESCALATE` when no matching tool exists. Uses `customer_id` (zero-padded to 33+ chars) as `runtimeSessionId` for native STM memory across HITL turns. Contains 275+ tools wrapped with Strands SDK `@tool` decorators across `src/tools/`.

5. **RAG Service (`src/services/rag_service.py`):** Queries the Bedrock Knowledge Base (`bedrock-agent-runtime.retrieve()`). Only returns results with confidence ≥ 0.6. Falls back to human escalation on low-confidence or no results.

6. **SRE Resolution Portal (`sre_portal.py`):** Separate Streamlit app for human SREs to view and resolve escalated DynamoDB tickets. Resolution text entered here is what trains the AI. Admin role triggers the learning loop sync.

7. **Continuous Learning Loop (`learning_loop.py`):** Scans DynamoDB for closed tickets where `vectorized=False`, uploads resolutions to S3, marks them vectorized, then calls `bedrock-agent.start_ingestion_job()` to re-index the Bedrock Knowledge Base. The system gets smarter with every human-resolved ticket.
