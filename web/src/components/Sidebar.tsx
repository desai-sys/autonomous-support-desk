import { Bot, Command, LogOut, MessageSquare, Moon, Plus, ScrollText, Sun, Ticket, Trash2, Globe2 } from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";
import { useConversations } from "../conversations";
import { can, cn, ROLE_COLORS, timeAgo } from "../lib";
import type { Me } from "../types";
import { Logo } from "./Logo";

export function Sidebar({ me, theme, toggleTheme, mobileOpen, onClose }: {
  me: Me; theme: "dark" | "light"; toggleTheme: () => void; mobileOpen: boolean; onClose: () => void;
}) {
  const { conversations, active, select, create, remove } = useConversations();
  const navigate = useNavigate();
  const nav = [
    { to: "/", label: "Command Center", icon: Bot, show: true },
    { to: "/tickets", label: "Tickets", icon: Ticket, show: can(me, "view_tickets") },
    { to: "/accounts", label: "Accounts", icon: Globe2, show: can(me, "manage_accounts") },
    { to: "/audit", label: "Audit log", icon: ScrollText, show: can(me, "view_audit_logs") },
  ].filter((n) => n.show);

  const go = (to: string) => { navigate(to); onClose(); };
  return (
    <>
    {mobileOpen && <div className="fixed inset-0 z-30 bg-black/50 backdrop-blur-sm md:hidden" onClick={onClose} />}
    <aside className={cn("w-72 shrink-0 flex-col border-r hairline md:flex md:bg-[var(--panel)]/70 md:backdrop-blur",
      mobileOpen ? "fixed inset-y-0 left-0 z-40 flex animate-rise bg-[var(--panel)]" : "hidden")}>
      <div className="px-5 pb-4 pt-5"><Logo /></div>

      <div className="px-3">
        <button className="btn btn-primary w-full" onClick={() => { create(); go("/"); }}>
          <Plus size={16} /> New conversation
        </button>
      </div>

      <nav className="mt-4 space-y-0.5 px-3">
        {nav.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end onClick={onClose}
            className={({ isActive }) => cn("flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition",
              isActive ? "bg-brand-500/15 text-brand-400" : "muted hover:bg-black/5 hover:text-[var(--text)] dark:hover:bg-white/5")}>
            <Icon size={17} /> {label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-5 px-5 text-[11px] font-semibold uppercase tracking-wider muted">Recent</div>
      <div className="mt-1.5 flex-1 space-y-0.5 overflow-y-auto px-3 pb-3 scrollbar-thin">
        {conversations.filter((c) => c.messages.length || c.id === active.id).map((c) => (
          <div key={c.id} className={cn("group flex items-center gap-2 rounded-xl px-3 py-2 text-sm",
            c.id === active.id ? "panel-2" : "hover:bg-black/5 dark:hover:bg-white/5")}>
            <button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => { select(c.id); go("/"); }}>
              <MessageSquare size={14} className="shrink-0 muted" />
              <span className="truncate">{c.title}</span>
            </button>
            <span className="text-[10px] muted group-hover:hidden">{timeAgo(c.updatedAt)}</span>
            <button className="hidden rounded p-0.5 muted hover:text-rose-400 group-hover:block" aria-label="Delete conversation"
              onClick={() => remove(c.id)}><Trash2 size={13} /></button>
          </div>
        ))}
      </div>

      <div className="border-t hairline p-3">
        <button className="mb-2 flex w-full items-center justify-between rounded-xl px-3 py-2 text-xs muted hover:bg-black/5 dark:hover:bg-white/5"
          onClick={() => window.dispatchEvent(new Event("open-palette"))}>
          <span className="flex items-center gap-2"><Command size={14} /> Quick actions</span>
          <kbd className="chip font-mono text-[10px]">Ctrl K</kbd>
        </button>
        <div className="flex items-center gap-3 rounded-xl panel-2 p-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gradient-to-br from-brand-500 to-accent-500 text-sm font-bold text-white">
            {me.email[0]?.toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold">{me.email}</div>
            <div className="mt-0.5 flex items-center gap-1.5">
              <span className={cn("chip px-2 py-0 text-[10px] capitalize", ROLE_COLORS[me.role])}>{me.role}</span>
              <span className="truncate text-[11px] muted">{me.tenant}</span>
            </div>
          </div>
          <button className="rounded-lg p-1.5 muted hover:bg-black/10 dark:hover:bg-white/10" onClick={toggleTheme}
            aria-label="Toggle theme" title="Toggle theme">
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <a className="rounded-lg p-1.5 muted hover:bg-black/10 dark:hover:bg-white/10" href="/auth/logout" aria-label="Sign out" title="Sign out">
            <LogOut size={16} />
          </a>
        </div>
      </div>
    </aside>
    </>
  );
}
