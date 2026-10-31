import { useQuery } from "@tanstack/react-query";
import { Bot, Globe2, LogOut, Menu, Moon, Plus, ScrollText, Ticket } from "lucide-react";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Toaster } from "sonner";
import { api, ApiError } from "./api";
import { AgentAvatar } from "./components/AgentAvatar";
import { CommandPalette, type PaletteItem } from "./components/CommandPalette";
import { Logo } from "./components/Logo";
import { Sidebar } from "./components/Sidebar";
import { ConversationProvider, useConversations } from "./conversations";
import { can, useLocalStorage } from "./lib";
import { Accounts } from "./pages/Accounts";
import { Audit } from "./pages/Audit";
import { CommandCenter } from "./pages/CommandCenter";
import { SignIn } from "./pages/SignIn";
import { Tickets } from "./pages/Tickets";
import type { Me } from "./types";

export function App() {
  const [theme, setTheme] = useLocalStorage<"dark" | "light">("cloudops-theme", "dark");
  useEffect(() => { document.documentElement.classList.toggle("dark", theme === "dark"); }, [theme]);

  const config = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig });
  const me = useQuery({
    queryKey: ["me"], queryFn: api.me,
    retry: (count, e) => !(e instanceof ApiError && e.status === 401) && count < 2,
  });

  const toaster = <Toaster theme={theme} position="top-right" richColors closeButton />;
  if (me.isLoading || config.isLoading) {
    return <div className="grid h-full place-items-center"><AgentAvatar mood="thinking" size={56} /></div>;
  }
  if (!me.data) {
    return <>{toaster}<SignIn mode={config.data?.mode ?? "oidc"} onSignedIn={() => me.refetch()} /></>;
  }
  return (
    <ConversationProvider userKey={`${me.data.tenant}:${me.data.user_id}`}>
      {toaster}
      <Shell me={me.data} theme={theme} toggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")} />
    </ConversationProvider>
  );
}

function Shell({ me, theme, toggleTheme }: { me: Me; theme: "dark" | "light"; toggleTheme: () => void }) {
  const navigate = useNavigate();
  const { create } = useConversations();
  const [navOpen, setNavOpen] = useState(false);
  const items: PaletteItem[] = [
    { id: "new", label: "New conversation", icon: <Plus size={16} />, run: () => { create(); navigate("/"); } },
    { id: "cc", label: "Go to Command Center", icon: <Bot size={16} />, run: () => navigate("/") },
    ...(can(me, "view_tickets") ? [{ id: "tickets", label: "Go to Tickets", icon: <Ticket size={16} />, run: () => navigate("/tickets") }] : []),
    ...(can(me, "manage_accounts") ? [{ id: "accounts", label: "Go to Accounts", icon: <Globe2 size={16} />, run: () => navigate("/accounts") }] : []),
    ...(can(me, "view_audit_logs") ? [{ id: "audit", label: "Go to Audit log", icon: <ScrollText size={16} />, run: () => navigate("/audit") }] : []),
    ...["Which S3 buckets are public?", "Show my AWS costs for the last 7 days", "List IAM users without MFA",
      "Build me a snake and ladder game"].map((p, i) => ({
      id: `ask-${i}`, label: p, hint: "Ask", icon: <Bot size={16} />,
      run: () => { navigate("/"); window.dispatchEvent(new CustomEvent("ask-agent", { detail: p })); },
    })),
    { id: "theme", label: `Switch to ${theme === "dark" ? "light" : "dark"} theme`, icon: <Moon size={16} />, run: toggleTheme },
    { id: "logout", label: "Sign out", icon: <LogOut size={16} />, run: () => { window.location.href = "/auth/logout"; } },
  ];

  return (
    <div className="flex h-full">
      <Sidebar me={me} theme={theme} toggleTheme={toggleTheme} mobileOpen={navOpen} onClose={() => setNavOpen(false)} />
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center justify-between border-b hairline px-4 py-2.5 md:hidden">
          <Logo />
          <button className="rounded-lg p-2 muted hover:bg-black/5 dark:hover:bg-white/5" aria-label="Open menu"
            onClick={() => setNavOpen(true)}><Menu size={20} /></button>
        </div>
        <div className="min-h-0 flex-1">
        <Routes>
          <Route path="/" element={<CommandCenter me={me} />} />
          {can(me, "view_tickets") && <Route path="/tickets" element={<Tickets me={me} />} />}
          {can(me, "manage_accounts") && <Route path="/accounts" element={<Accounts />} />}
          {can(me, "view_audit_logs") && <Route path="/audit" element={<Audit />} />}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </div>
      </main>
      <CommandPalette items={items} />
    </div>
  );
}
