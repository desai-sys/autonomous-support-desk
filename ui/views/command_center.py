"""
Command Center: chat with the agent, target accounts/regions, upload code, and
approve or reject the changes the agent proposes.
"""

import json
import re
import uuid
from datetime import datetime, timezone

import streamlit as st

import config
from src.core.security import Permission, User
from src.services.artifact_service import ArtifactValidationError
from ui.common import account_label, artifacts, gateway, managed_accounts, tickets

REGIONS = [
    "us-east-1", "us-east-2", "us-west-1", "us-west-2", "ca-central-1",
    "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-north-1",
    "ap-south-1", "ap-southeast-1", "ap-southeast-2", "ap-northeast-1", "sa-east-1",
]
DEPLOY_TARGETS = {
    "auto": "Let the agent recommend",
    "lambda": "AWS Lambda",
    "ec2": "Amazon EC2 — existing instance",
    "ec2_new": "Amazon EC2 — launch a new instance",
    "ecs": "Amazon ECS Fargate (zip has a Dockerfile)",
    "s3_static_site": "S3 static website (zip has index.html)",
}
# Bare 12-digit ids typed in chat; ids inside ARNs (":123456789012:") are ignored.
BARE_ACCOUNT_ID_RE = re.compile(r"(?<![:\d])(\d{12})(?![\d:])")
_PREFIX_RE = re.compile(r"^\[.*?\]\s*\(\d+ms\)\s*\n+")


def _new_conversation() -> None:
    st.session_state.conversation_id = uuid.uuid4().hex
    st.session_state.messages = []
    st.session_state.pop("artifact", None)


def build_deploy_prompt(filename: str, target: str, options: dict) -> str:
    """Turn the upload form into the chat message that asks the agent to deploy."""
    details = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in options.items())
    if target == "auto":
        text = f"I uploaded `{filename}`. Inspect it and recommend where to deploy and run it."
    elif target == "ec2_new":
        text = f"Launch a new EC2 instance and deploy and run the uploaded `{filename}` on it."
    else:
        text = f"Deploy and run the uploaded `{filename}` on {DEPLOY_TARGETS[target]}."
    if details:
        text += f" ({details})"
    return text


def _sidebar(user: User) -> bool:
    """Render chat settings; returns True in knowledge-base mode."""
    with st.sidebar:
        mode = st.radio("Mode", ["🤖 Agent", "📚 Knowledge base"], key="chat_mode", horizontal=True,
                        help="Agent: inspects and (after approval) changes AWS resources. "
                             "Knowledge base: searches past ticket resolutions only.")
        kb_mode = mode.endswith("Knowledge base")
        if not kb_mode:
            accounts = managed_accounts()
            st.session_state.setdefault("target_accounts", [])
            # Selection changes requested elsewhere are applied before the widget renders.
            if "pending_targets" in st.session_state:
                st.session_state.target_accounts = st.session_state.pop("pending_targets")
            st.session_state.target_accounts = [a for a in st.session_state.target_accounts
                                                if a in accounts]
            st.multiselect(
                "Target accounts", list(accounts), key="target_accounts", format_func=account_label,
                placeholder="Platform account",
                help="Empty = the platform's own account. Several accounts run the request in "
                     "each one. Admins register accounts on the Accounts page.",
            )
            st.selectbox("Region", REGIONS, key="target_region",
                         index=REGIONS.index(config.AWS_REGION) if config.AWS_REGION in REGIONS else 0)
            if not user.can(Permission.PROPOSE_CHANGES):
                st.info("Your role is read-only: the agent can investigate but not change anything.")
        if st.button("➕ New conversation", use_container_width=True):
            _new_conversation()
            st.rerun()
    return kb_mode


def _upload_toolbar(user: User) -> None:
    if not user.can(Permission.UPLOAD_CODE):
        return
    col_upload, col_status = st.columns([1, 3])
    with col_upload.popover("📦 Deploy code (.zip)", use_container_width=True):
        st.markdown("**Upload code and choose where to run it**")
        upload = st.file_uploader("Code archive", type=["zip"], key="artifact_upload",
                                  help=f"Up to {config.MAX_ARTIFACT_MB} MB. Deployed into the "
                                       "target account(s) and region selected in the sidebar.")
        target = st.selectbox("Run it on", list(DEPLOY_TARGETS), format_func=DEPLOY_TARGETS.get)
        options = {}
        if target == "lambda":
            options["function_name"] = st.text_input("Function name", placeholder="auto-generated if empty")
            options["runtime"] = st.selectbox(
                "Runtime", ["auto-detect", "python3.12", "python3.13", "nodejs20.x", "nodejs22.x"])
            options["handler"] = st.text_input("Handler", placeholder="auto-detect, e.g. app.lambda_handler")
        elif target == "ec2":
            options["instance_id"] = st.text_input("Instance ID", placeholder="i-0abc123def4567890")
        elif target == "ec2_new":
            options["instance_type"] = st.selectbox("Instance type",
                                                    ["t3.micro", "t3.small", "t3.medium", "t4g.small"])
        elif target == "ecs":
            options["service_name"] = st.text_input("Service name", placeholder="auto-generated if empty")
        elif target == "s3_static_site":
            options["bucket_name"] = st.text_input("Bucket name", placeholder="auto-generated if empty")
        if target in ("ec2", "ec2_new"):
            options["run_command"] = st.text_input(
                "Run command (optional)", placeholder="pip3 install -r requirements.txt && python3 app.py")
        options = {k: v.strip() for k, v in options.items() if v and v.strip() and v != "auto-detect"}

        if st.button("Upload & send to agent", type="primary", use_container_width=True,
                     disabled=upload is None):
            if target == "ec2" and "instance_id" not in options:
                st.error("Enter the instance ID, or choose “launch a new instance”.")
                return
            try:
                with st.spinner("Uploading..."):
                    ref = artifacts().upload(upload.getvalue(), upload.name, user.tenant_id, user.user_id)
            except ArtifactValidationError as e:
                st.error(str(e))
                return
            except Exception as e:
                st.error(f"Upload failed: {e}")
                return
            ref.update(target=target, options=options)
            st.session_state.artifact = ref
            st.session_state.queued_prompt = build_deploy_prompt(upload.name, target, options)
            st.rerun()

    artifact = st.session_state.get("artifact")
    if artifact:
        c_info, c_clear = col_status.columns([6, 1])
        c_info.info(f"📎 **{artifact['filename']}** · {artifact['size_bytes'] / 1024:.0f} KB · "
                    f"{artifact['file_count']} files · {DEPLOY_TARGETS[artifact['target']]}")
        if c_clear.button("✖", help="Detach this upload from the conversation"):
            del st.session_state["artifact"]
            st.rerun()


def _action_cards(user: User) -> None:
    pending = gateway().pending_actions(user, st.session_state.conversation_id)
    if not pending:
        return
    can_approve = user.can(Permission.APPROVE_CHANGES)
    st.subheader(f"⏳ {len(pending)} change(s) awaiting approval")
    for action in sorted(pending, key=lambda a: a["created_at"]):
        with st.container(border=True):
            st.markdown(f"**`{action['tool_name']}`** · {account_label(action['account_id'])}"
                        f" · {action['region'] or config.AWS_REGION}")
            st.code(json.dumps(action["tool_input"], indent=2), language="json")
            expires = datetime.fromtimestamp(action["expires_at"], tz=timezone.utc)
            st.caption(f"Requested by {action['requested_by']} · expires {expires:%H:%M} UTC · "
                       f"id {action['action_id']}")
            own = config.REQUIRE_SEPARATE_APPROVER and action["requested_by"] == user.user_id
            c_ok, c_no, _ = st.columns([1, 1, 4])
            approve = c_ok.button("✅ Approve", key=f"approve-{action['action_id']}", type="primary",
                                  disabled=not can_approve or own)
            reject = c_no.button("Reject", key=f"reject-{action['action_id']}", disabled=not can_approve)
            if own:
                st.caption("Another person must approve changes you requested.")
            if approve:
                with st.spinner(f"Running {action['tool_name']}..."):
                    result = gateway().approve([action["action_id"]], user,
                                               st.session_state.conversation_id)
                st.session_state.messages.append({
                    "role": "assistant", "content": result.get("response", "No result."),
                    "metadata": {"results": result.get("results", [])},
                })
                st.rerun()
            if reject:
                gateway().reject(action["action_id"], user)
                st.session_state.messages.append({
                    "role": "assistant", "content": f"Rejected `{action['summary']}`."})
                st.rerun()


def _history() -> list:
    prior = st.session_state.messages[-8:]
    return [f"{'User' if m['role'] == 'user' else 'Assistant'}: {_PREFIX_RE.sub('', m['content'])[:2000]}"
            for m in prior]


def _handle_message(user: User, text: str, kb_mode: bool) -> None:
    history = _history()
    st.session_state.messages.append({"role": "user", "content": text})
    with st.chat_message("user"):
        st.markdown(text)

    if not kb_mode:
        # Registered account ids typed in the message become sticky targets, so
        # a follow-up still runs in the account the plan was made for.
        accounts = managed_accounts()
        typed = [a for a in BARE_ACCOUNT_ID_RE.findall(text)
                 if a in accounts and a not in st.session_state.target_accounts]
        unknown = [a for a in BARE_ACCOUNT_ID_RE.findall(text) if a not in accounts]
        targets = st.session_state.target_accounts + typed + unknown
        if typed:
            st.session_state.pending_targets = st.session_state.target_accounts + typed

    with st.chat_message("assistant"), st.spinner("Working..."):
        if kb_mode:
            res = gateway().query_knowledge_base(text, user)
            content, escalate, meta = res.text, res.escalate, {"mode": "knowledge_base"}
            tag = "KB"
        else:
            res = gateway().chat(text, user, st.session_state.conversation_id, history=history,
                                 accounts=targets, region=st.session_state.get("target_region"),
                                 artifact=st.session_state.get("artifact"))
            content, escalate, meta = res.text, res.escalate, res.metadata
            tag = res.resolution_type.upper()
        st.markdown(content)

    if escalate and user.can(Permission.VIEW_TICKETS):
        try:
            ticket = tickets().create(tenant_id=user.tenant_id, query_text=text,
                                      category="KnowledgeGap" if kb_mode else "Escalated",
                                      created_by=user.user_id, context=content)
            content += f"\n\n🎫 Ticket `{ticket['ticket_id']}` created for an SRE."
        except Exception as e:
            content += f"\n\n⚠️ Could not create a ticket: {e}"

    took = meta.get("execution_time_ms")
    prefix = f"[{tag}] ({took:.0f}ms)\n\n" if isinstance(took, (int, float)) else ""
    st.session_state.messages.append({"role": "assistant", "content": prefix + content,
                                      "metadata": meta})
    st.rerun()


def render(user: User) -> None:
    if "conversation_id" not in st.session_state:
        _new_conversation()
    kb_mode = _sidebar(user)

    st.title("🛡️ Command Center")
    targets = st.session_state.get("target_accounts", [])
    st.caption(
        "Ask about or act on your AWS resources. Changes are queued for approval before anything "
        f"runs. · Accounts: {', '.join(account_label(a) for a in targets) or 'platform account'}"
        f" · Region: {st.session_state.get('target_region', config.AWS_REGION)}"
    )
    if not kb_mode:
        _upload_toolbar(user)

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("metadata") and user.can(Permission.VIEW_AUDIT_LOGS):
                with st.expander("Details"):
                    st.json(msg["metadata"])

    if not kb_mode:
        _action_cards(user)

    placeholder = ("Search past resolutions — e.g. 'how did we fix RDS connection timeouts?'"
                   if kb_mode else "Ask anything — e.g. 'which S3 buckets are public?'")
    text = st.chat_input(placeholder) or st.session_state.pop("queued_prompt", None)
    if text:
        _handle_message(user, text.strip()[:2000], kb_mode)
