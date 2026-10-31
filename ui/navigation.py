"""Which console pages each role can open."""

from typing import Callable, List, NamedTuple, Optional

from src.core.security import Permission, User
from ui.views import accounts, audit, command_center, tickets


class PageSpec(NamedTuple):
    title: str
    icon: str
    url_path: str
    render: Callable[[User], None]
    permission: Optional[Permission]


PAGES = [
    PageSpec("Command Center", "🤖", "command-center", command_center.render, None),
    PageSpec("Tickets", "🎫", "tickets", tickets.render, Permission.VIEW_TICKETS),
    PageSpec("Accounts", "🌐", "accounts", accounts.render, Permission.MANAGE_ACCOUNTS),
    PageSpec("Audit log", "📜", "audit", audit.render, Permission.VIEW_AUDIT_LOGS),
]


def pages_for(user: User) -> List[PageSpec]:
    return [p for p in PAGES if p.permission is None or user.can(p.permission)]
