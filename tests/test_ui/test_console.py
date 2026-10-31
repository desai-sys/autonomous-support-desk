"""Console behaviour through Streamlit's AppTest (development sign-in, moto tables)."""
import pathlib
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from src.agents.gateway import ChatResult
from src.core.security import User, UserRole

APP = str(pathlib.Path(__file__).resolve().parents[2] / "ui" / "app.py")


@pytest.fixture
def console(tables):
    """Factory: an AppTest signed in with the given role; the agent is stubbed."""
    sent = {"chat": [], "approve": [], "reject": []}

    def chat(self, text, user, session_id, **kw):
        sent["chat"].append({"text": text, "session": session_id, **kw})
        return ChatResult(text="Queued a stop.", resolution_type="action", metadata={})

    def approve(self, action_ids, user, session_id):
        sent["approve"].append(action_ids)
        return {"response": "✅ stop_ec2_instance: succeeded", "results": []}

    patches = [patch("src.agents.gateway.AgentGateway.chat", chat),
               patch("src.agents.gateway.AgentGateway.approve", approve)]
    for p in patches:
        p.start()

    def make(role: UserRole):
        at = AppTest.from_file(APP, default_timeout=30)
        at.session_state["local_user"] = User.build("alice", "alice@example.com", role, "t1")
        at.run()
        assert not at.exception, at.exception
        return at

    make.sent = sent
    yield make
    for p in patches:
        p.stop()


def page_titles(at):
    return [t.value for t in at.title]


def test_sign_in_page_when_anonymous(tables):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert "Development sign-in" in at.warning[0].value


@pytest.mark.parametrize("role,visible", [
    (UserRole.VIEWER, {"Command Center", "Tickets"}),
    (UserRole.OPERATOR, {"Command Center", "Tickets"}),
    (UserRole.SRE, {"Command Center", "Tickets", "Audit log"}),
    (UserRole.ADMIN, {"Command Center", "Tickets", "Accounts", "Audit log"}),
])
def test_pages_follow_role(console, role, visible):
    from ui.navigation import pages_for
    assert {p.title for p in pages_for(User.build("u", "u@x", role, "t1"))} == visible
    at = console(role)
    assert "🛡️ Command Center" in page_titles(at)
    # Upload is only offered to roles that may deploy code.
    has_upload = any(getattr(b, "label", "") == "Upload & send to agent" for b in at.button)
    assert has_upload == (role != UserRole.VIEWER)


def test_message_is_sent_with_targets_and_region(console):
    at = console(UserRole.OPERATOR)
    at.selectbox(key="target_region").set_value("eu-west-1").run()
    at.chat_input[0].set_value("stop i-1").run()
    assert not at.exception, at.exception
    [msg] = console.sent["chat"]
    assert msg["text"] == "stop i-1" and msg["region"] == "eu-west-1" and msg["accounts"] == []
    assert any("Queued a stop." in m.value for m in at.markdown)


def _queue_action(at, requested_by="alice"):
    from src.core.approvals import ActionStore
    return ActionStore().create(tenant_id="t1", session_id=at.session_state["conversation_id"],
                                requested_by=requested_by, account_id="", region="us-east-1",
                                tool_name="stop_ec2_instance", tool_input={"instance_id": "i-1"})


def test_pending_action_can_be_approved(console):
    at = console(UserRole.OPERATOR)
    action = _queue_action(at)
    at.run()
    assert any("awaiting approval" in s.value for s in at.subheader)
    [b for b in at.button if b.label == "✅ Approve"][0].click().run()
    assert console.sent["approve"] == [[action["action_id"]]]


def test_pending_action_can_be_rejected(console):
    at = console(UserRole.SRE)
    action = _queue_action(at)
    at.run()
    [b for b in at.button if b.label == "Reject"][0].click().run()
    from src.core.approvals import ActionStore
    assert ActionStore().get(action["action_id"])["status"] == "rejected"
    assert not any("awaiting approval" in s.value for s in at.subheader)


def test_viewer_cannot_approve(console):
    at = console(UserRole.VIEWER)
    _queue_action(at, requested_by="someone")
    at.run()
    assert [b for b in at.button if b.label == "✅ Approve"][0].disabled


def test_separate_approver(console, monkeypatch):
    import config
    monkeypatch.setattr(config, "REQUIRE_SEPARATE_APPROVER", True)
    at = console(UserRole.ADMIN)
    _queue_action(at, requested_by="alice")
    at.run()
    assert [b for b in at.button if b.label == "✅ Approve"][0].disabled
