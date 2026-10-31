"""Console JSON API (/api/*). Every route requires a signed-in user; permissions follow their role."""

import json
import os
from functools import lru_cache
from typing import Dict, List, Optional

import boto3
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

import config
from console.auth import require_user
from console.jobs import JobStore
from src.agents.gateway import AgentGateway
from src.core.accounts import AccountRegistry
from src.core.security import Permission, User, security_manager
from src.services.app_bundles import AppBundleStore, BundleError
from src.services.artifact_service import ArtifactService, ArtifactValidationError
from src.services.ticket_service import TicketService

router = APIRouter(prefix="/api")

REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "sa-east-1",
]
DEPLOY_TARGETS = {
    "auto": "Let the agent recommend",
    "lambda": "AWS Lambda",
    "ec2": "Amazon EC2 — existing instance",
    "ec2_new": "Amazon EC2 — new instance",
    "ecs": "Amazon ECS Fargate (needs a Dockerfile)",
    "s3_static_site": "S3 static website (needs index.html)",
}


# ── Shared services (created lazily, once per process) ───────────────────────

@lru_cache
def gateway() -> AgentGateway:
    return AgentGateway()


@lru_cache
def jobs() -> JobStore:
    return JobStore()


@lru_cache
def tickets() -> TicketService:
    return TicketService()


@lru_cache
def artifacts() -> ArtifactService:
    return ArtifactService()


@lru_cache
def registry() -> AccountRegistry:
    return AccountRegistry()


@lru_cache
def bundles() -> AppBundleStore:
    return AppBundleStore(boto3.client("s3", region_name=config.AWS_REGION))


def need(user: User, permission: Permission) -> None:
    if not user.can(permission):
        raise HTTPException(status_code=403, detail="Your role does not allow this.")


# ── Session ───────────────────────────────────────────────────────────────────

@router.get("/me")
def me(user: User = Depends(require_user)):
    return {
        "user_id": user.user_id, "email": user.email, "role": user.role.value,
        "tenant": user.tenant_id, "permissions": [p.value for p in user.permissions],
        "environment": config.ENVIRONMENT, "auth_mode": config.AUTH_MODE,
        "default_region": config.AWS_REGION, "regions": REGIONS, "deploy_targets": DEPLOY_TARGETS,
        "require_separate_approver": config.REQUIRE_SEPARATE_APPROVER,
        "max_upload_mb": config.MAX_ARTIFACT_MB,
    }


# ── Chat and approvals (slow work runs as jobs) ──────────────────────────────

class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=8, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    mode: str = "agent"                      # agent | knowledge
    accounts: List[str] = []
    region: Optional[str] = None
    artifact: Optional[dict] = None
    history: List[str] = []


def _chat(user: User, body: ChatRequest) -> dict:
    if body.mode == "knowledge":
        res = gateway().query_knowledge_base(body.message, user)
        result = {"text": res.text, "resolution_type": "knowledge", "escalate": res.escalate,
                  "pending_actions": [], "metadata": {}}
    else:
        res = gateway().chat(body.message, user, body.conversation_id, history=body.history[-8:],
                             accounts=body.accounts, region=body.region, artifact=body.artifact)
        result = {"text": res.text, "resolution_type": res.resolution_type, "escalate": res.escalate,
                  "pending_actions": res.pending_actions, "metadata": res.metadata}
    if result["escalate"] and user.can(Permission.VIEW_TICKETS):
        try:
            ticket = tickets().create(
                tenant_id=user.tenant_id, query_text=body.message, created_by=user.user_id,
                category="KnowledgeGap" if body.mode == "knowledge" else "Escalated", context=result["text"])
            result["ticket_id"] = ticket["ticket_id"]
        except Exception as e:
            result["ticket_error"] = str(e)
    return result


@router.post("/chat")
def chat(body: ChatRequest, user: User = Depends(require_user)):
    need(user, Permission.USE_AGENT)
    if body.artifact and not user.can(Permission.UPLOAD_CODE):
        raise HTTPException(status_code=403, detail="Your role cannot deploy code.")
    return {"job_id": jobs().submit(user.user_id, "chat", lambda: _chat(user, body))}


@router.get("/jobs/{job_id}")
def job(job_id: str, user: User = Depends(require_user)):
    found = jobs().get(job_id, user.user_id)
    if not found:
        raise HTTPException(status_code=404, detail="Job not found.")
    return found


@router.get("/conversations/{conversation_id}/actions")
def pending_actions(conversation_id: str, user: User = Depends(require_user)):
    return gateway().pending_actions(user, conversation_id)


class Decision(BaseModel):
    conversation_id: str


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, body: Decision, user: User = Depends(require_user)):
    need(user, Permission.APPROVE_CHANGES)
    return {"job_id": jobs().submit(user.user_id, "approve",
                                    lambda: gateway().approve([action_id], user, body.conversation_id))}


@router.post("/actions/{action_id}/reject")
def reject(action_id: str, user: User = Depends(require_user)):
    need(user, Permission.APPROVE_CHANGES)
    if not gateway().reject(action_id, user):
        raise HTTPException(status_code=409, detail="The action is no longer pending.")
    return {"ok": True}


@router.get("/apps/{app_id}/versions/{version}")
def app_bundle(app_id: str, version: int, user: User = Depends(require_user)):
    try:
        manifest, files, template = bundles().load(user.tenant_id, app_id, version)
    except BundleError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"manifest": manifest, "files": files, "template": template}


@router.post("/uploads")
async def upload(file: UploadFile = File(...), target: str = Form("auto"), options: str = Form("{}"),
                 user: User = Depends(require_user)):
    need(user, Permission.UPLOAD_CODE)
    if target not in DEPLOY_TARGETS:
        raise HTTPException(status_code=400, detail="Unknown deployment target.")
    data = await file.read(config.MAX_ARTIFACT_MB * 1024 * 1024 + 1)
    try:
        ref = artifacts().upload(data, file.filename or "upload.zip", user.tenant_id, user.user_id)
    except ArtifactValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    ref.update(target=target, options={k: v for k, v in json.loads(options or "{}").items() if v})
    security_manager.log_audit_event(user.user_id, user.tenant_id, "artifact_uploaded", ref["uri"],
                                     "SUCCESS", details={"size_bytes": ref["size_bytes"], "target": target})
    return ref


# ── Accounts ──────────────────────────────────────────────────────────────────

@router.get("/accounts")
def list_accounts(user: User = Depends(require_user)):
    return sorted(registry().list().values(), key=lambda a: (a.get("name") or "", a["account_id"]))


class NewAccount(BaseModel):
    account_id: str
    name: str = ""


@router.post("/accounts")
def add_account(body: NewAccount, user: User = Depends(require_user)):
    need(user, Permission.MANAGE_ACCOUNTS)
    try:
        item = registry().add(body.account_id, body.name, user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    security_manager.log_audit_event(user.user_id, user.tenant_id, "account_added", item["account_id"],
                                     "SUCCESS", details={"name": item["name"]})
    return item


@router.delete("/accounts/{account_id}")
def remove_account(account_id: str, user: User = Depends(require_user)):
    need(user, Permission.MANAGE_ACCOUNTS)
    registry().remove(account_id)
    security_manager.log_audit_event(user.user_id, user.tenant_id, "account_removed", account_id, "SUCCESS")
    return {"ok": True}


@router.post("/accounts/{account_id}/test")
def test_account(account_id: str, user: User = Depends(require_user)):
    need(user, Permission.MANAGE_ACCOUNTS)
    return gateway().check_account(account_id, user)


@router.get("/accounts/setup")
def account_setup(user: User = Depends(require_user)):
    need(user, Permission.MANAGE_ACCOUNTS)
    role_arn = os.environ.get("AGENT_RUNTIME_ROLE_ARN", "<agent-runtime-role-arn>")
    external_id = config.CROSS_ACCOUNT_EXTERNAL_ID or "<external-id>"
    return {"command": (
        "aws cloudformation deploy --template-file infra/cross_account_role.yaml "
        "--stack-name cloudops-cross-account --capabilities CAPABILITY_NAMED_IAM "
        f"--parameter-overrides AgentRuntimeRoleArn={role_arn} ExternalId={external_id}"
    )}


# ── Tickets, knowledge base, audit ────────────────────────────────────────────

@router.get("/tickets")
def list_tickets(user: User = Depends(require_user)):
    need(user, Permission.VIEW_TICKETS)
    return tickets().list_open(user.tenant_id)


class Resolution(BaseModel):
    resolution: str = Field(min_length=1, max_length=8000)


@router.post("/tickets/{ticket_id}/close")
def close_ticket(ticket_id: str, body: Resolution, user: User = Depends(require_user)):
    need(user, Permission.RESOLVE_TICKETS)
    try:
        ok = tickets().close(ticket_id, tenant_id=user.tenant_id, resolution=body.resolution,
                             resolved_by=user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="Ticket not found.")
    security_manager.log_audit_event(user.user_id, user.tenant_id, "ticket_closed", ticket_id, "SUCCESS")
    return {"ok": True}


@router.post("/knowledge/sync")
def knowledge_sync(user: User = Depends(require_user)):
    need(user, Permission.TRIGGER_LEARNING)
    from src.jobs.learning_loop import sync_closed_tickets
    return sync_closed_tickets()


@router.get("/audit")
def audit(limit: int = 300, user: User = Depends(require_user)) -> List[Dict]:
    need(user, Permission.VIEW_AUDIT_LOGS)
    return security_manager.get_audit_logs(user.tenant_id, limit=min(max(limit, 1), 500))
