import { useQuery, useQueryClient } from "@tanstack/react-query";
import confetti from "canvas-confetti";
import { ArrowUp, BookOpen, Bot, Boxes, Gamepad2, Paperclip, Search, ShieldCheck, Sparkles, Ticket, Wallet, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import { AgentAvatar, type AgentMood } from "../components/AgentAvatar";
import { ApprovalCard } from "../components/ApprovalCard";
import { Markdown } from "../components/Markdown";
import { TargetBar } from "../components/TargetBar";
import { UploadDialog } from "../components/UploadDialog";
import { WorkingIndicator } from "../components/WorkingIndicator";
import { useConversations } from "../conversations";
import { accountIdsIn, can, cn, uid, useLocalStorage } from "../lib";
import type { Artifact, Me, Message } from "../types";

const SUGGESTIONS = [
  { icon: ShieldCheck, text: "Which of my S3 buckets are public?", tag: "Security" },
  { icon: Wallet, text: "Show my AWS costs for the last 7 days by service", tag: "Cost" },
  { icon: Search, text: "Why is my EC2 instance unhealthy?", tag: "Troubleshoot" },
  { icon: Boxes, text: "Create an EC2 instance for a small web server", tag: "Provision" },
  { icon: Gamepad2, text: "Build me a simple snake and ladder game", tag: "Build" },
  { icon: Sparkles, text: "Build a URL shortener API with a DynamoDB table", tag: "Build" },
];

function celebrate() {
  const colors = ["#6366f1", "#06b6d4", "#a855f7", "#10b981"];
  confetti({ particleCount: 110, spread: 75, origin: { y: 0.75 }, colors, disableForReducedMotion: true });
  setTimeout(() => confetti({ particleCount: 60, angle: 60, spread: 55, origin: { x: 0, y: 0.8 }, colors, disableForReducedMotion: true }), 180);
  setTimeout(() => confetti({ particleCount: 60, angle: 120, spread: 55, origin: { x: 1, y: 0.8 }, colors, disableForReducedMotion: true }), 300);
}

export function CommandCenter({ me }: { me: Me }) {
  const { active, update } = useConversations();
  const queryClient = useQueryClient();
  const [input, setInput] = useState("");
  const [mode, setMode] = useLocalStorage<"agent" | "knowledge">("cloudops-mode", "agent");
  const [targets, setTargets] = useLocalStorage<string[]>("cloudops-targets", []);
  const [region, setRegion] = useLocalStorage<string>("cloudops-region", me.default_region);
  const [working, setWorking] = useState<null | { kind: "chat" | "approve"; seconds: number }>(null);
  const [mood, setMood] = useState<AgentMood>("idle");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);

  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const pending = useQuery({
    queryKey: ["pending", active.id], queryFn: () => api.pending(active.id), refetchInterval: 20_000,
    enabled: mode === "agent",
  });
  const registered = accounts.data ?? [];
  const validTargets = targets.filter((t) => registered.some((a) => a.account_id === t));

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [active.messages.length, working, pending.data?.length]);

  useEffect(() => {
    if (!working) setMood(pending.data?.length ? "waiting" : "idle");
  }, [pending.data?.length, working]);

  const add = useCallback((message: Omit<Message, "id" | "at">) => {
    update(active.id, (c) => ({
      ...c,
      title: c.messages.length || message.role !== "user" ? c.title : message.text.slice(0, 48),
      messages: [...c.messages, { ...message, id: uid(), at: Date.now() }],
    }));
  }, [active.id, update]);

  const send = useCallback(async (text: string, artifactOverride?: Artifact) => {
    const message = text.trim();
    if (!message || working) return;
    setInput("");
    const history = active.messages.slice(-8).map((m) => `${m.role === "user" ? "User" : "Assistant"}: ${m.text.slice(0, 2000)}`);
    // Registered account ids typed in the message become sticky targets for follow-ups.
    const typed = accountIdsIn(message).filter((id) => registered.some((a) => a.account_id === id));
    const unknown = accountIdsIn(message).filter((id) => !registered.some((a) => a.account_id === id));
    const runTargets = [...new Set([...validTargets, ...typed, ...unknown])];
    if (typed.length) setTargets([...new Set([...validTargets, ...typed])]);

    add({ role: "user", text: message });
    setWorking({ kind: "chat", seconds: 0 });
    setMood("thinking");
    try {
      const result = await api.chat({
        conversation_id: active.id, message, mode, accounts: runTargets, region,
        artifact: mode === "agent" ? artifactOverride ?? active.artifact : null, history,
      }, (s) => setWorking({ kind: "chat", seconds: s }));
      let reply = result.text;
      if (result.ticket_id) reply += `\n\n🎫 I've opened ticket \`${result.ticket_id}\` so an SRE can follow up.`;
      add({ role: "assistant", text: reply, meta: result.metadata, kind: result.escalate ? "info" : "chat" });
      setMood(result.pending_actions.length ? "waiting" : result.escalate ? "sad" : "happy");
      if (result.pending_actions.length) toast.info(`${result.pending_actions.length} change(s) need your approval`);
    } catch (e) {
      add({ role: "assistant", text: `⚠️ ${(e as Error).message}`, kind: "error" });
      setMood("sad");
    } finally {
      setWorking(null);
      queryClient.invalidateQueries({ queryKey: ["pending", active.id] });
    }
  }, [active, add, mode, queryClient, region, registered, setTargets, validTargets, working]);

  useEffect(() => {
    const onAsk = (e: Event) => send((e as CustomEvent<string>).detail);
    window.addEventListener("ask-agent", onAsk);
    return () => window.removeEventListener("ask-agent", onAsk);
  }, [send]);

  const approve = async (actionId: string) => {
    setBusyAction(actionId);
    setWorking({ kind: "approve", seconds: 0 });
    setMood("thinking");
    try {
      const result = await api.approve(actionId, active.id, (s) => setWorking({ kind: "approve", seconds: s }));
      const ok = result.results.length > 0 && result.results.every((r) => r.status === "succeeded");
      add({ role: "assistant", text: result.response || "Done.", kind: ok ? "result" : "error", meta: { results: result.results } });
      if (ok) { celebrate(); setMood("happy"); toast.success("Change applied"); } else { setMood("sad"); toast.error("The change did not complete"); }
    } catch (e) {
      add({ role: "assistant", text: `⚠️ ${(e as Error).message}`, kind: "error" });
      setMood("sad");
    } finally {
      setBusyAction(null);
      setWorking(null);
      queryClient.invalidateQueries({ queryKey: ["pending", active.id] });
    }
  };

  const reject = async (actionId: string, summary: string) => {
    try {
      await api.reject(actionId);
      add({ role: "assistant", text: `Rejected \`${summary}\`. Nothing was changed.`, kind: "info" });
    } catch (e) {
      toast.error((e as Error).message);
    }
    queryClient.invalidateQueries({ queryKey: ["pending", active.id] });
  };

  const empty = active.messages.length === 0;
  const actions = mode === "agent" ? pending.data ?? [] : [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b hairline px-6 py-3">
        <div className="flex rounded-xl panel-2 p-1 text-sm">
          {([["agent", Bot, "Agent"], ["knowledge", BookOpen, "Knowledge base"]] as const).map(([key, Icon, label]) => (
            <button key={key} onClick={() => setMode(key)}
              className={cn("flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-medium transition",
                mode === key ? "bg-[var(--panel)] shadow-sm" : "muted hover:text-[var(--text)]")}>
              <Icon size={15} /> {label}
            </button>
          ))}
        </div>
        {mode === "agent" && (
          <TargetBar accounts={registered} selected={validTargets} onSelected={setTargets}
            region={region} regions={me.regions} onRegion={setRegion} />
        )}
        {!can(me, "propose_changes") && mode === "agent" && (
          <span className="chip border-slate-500/30 text-xs muted">Read-only role</span>
        )}
      </header>

      <div ref={scroller} className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="mx-auto max-w-4xl space-y-5 px-6 py-8">
          {empty && (
            <div className="animate-rise pt-6 text-center">
              <div className="mx-auto w-fit"><AgentAvatar mood={mood} size={72} /></div>
              <h1 className="mt-5 text-3xl font-bold tracking-tight">
                Hi {me.email.split("@")[0]}, <span className="gradient-text">what shall we do?</span>
              </h1>
              <p className="mx-auto mt-2 max-w-lg text-sm muted">
                {mode === "agent"
                  ? "Ask about your AWS resources, request a change, deploy code or describe an app to build. Changes always wait for your approval."
                  : "Search what your team learned from past incidents."}
              </p>
              {mode === "agent" && (
                <div className="mx-auto mt-8 grid max-w-3xl gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {SUGGESTIONS.map(({ icon: Icon, text, tag }, i) => (
                    <button key={text} onClick={() => send(text)}
                      className="panel group animate-rise p-4 text-left transition hover:-translate-y-0.5 hover:border-brand-500/50 hover:shadow-lg hover:shadow-brand-500/10"
                      style={{ animationDelay: `${i * 50}ms` }}>
                      <div className="flex items-center justify-between">
                        <Icon size={18} className="text-brand-400" />
                        <span className="text-[10px] font-semibold uppercase tracking-wider muted">{tag}</span>
                      </div>
                      <div className="mt-3 text-sm font-medium leading-snug">{text}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {active.messages.map((m) => (
            <div key={m.id} className={cn("flex animate-rise gap-3", m.role === "user" && "justify-end")}>
              {m.role === "assistant" && <AgentAvatar mood={m.kind === "error" ? "sad" : m.kind === "result" ? "happy" : "idle"} size={36} />}
              <div className={cn("max-w-[85%] rounded-2xl px-4 py-3",
                m.role === "user"
                  ? "bg-gradient-to-br from-brand-500 to-brand-600 text-white shadow-lg shadow-brand-500/20"
                  : cn("panel", m.kind === "error" && "border-rose-500/40", m.kind === "result" && "border-emerald-500/40"))}>
                {m.role === "user" ? <div className="whitespace-pre-wrap text-sm">{m.text}</div> : <Markdown text={m.text} />}
                {m.role === "assistant" && m.meta && can(me, "view_audit_logs") && Object.keys(m.meta).length > 0 && (
                  <details className="mt-2 text-xs muted">
                    <summary className="cursor-pointer select-none">Details</summary>
                    <pre className="mt-1 overflow-x-auto font-mono text-[11px]">{JSON.stringify(m.meta, null, 2)}</pre>
                  </details>
                )}
              </div>
            </div>
          ))}

          {working && <WorkingIndicator kind={working.kind} seconds={working.seconds} />}

          {actions.length > 0 && (
            <div className="space-y-3 pt-2">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <AgentAvatar mood="waiting" size={28} />
                {actions.length} change{actions.length > 1 ? "s" : ""} waiting for your approval
              </div>
              {actions.map((a) => (
                <ApprovalCard key={a.action_id} action={a} me={me} accounts={registered} busy={busyAction === a.action_id}
                  onApprove={() => approve(a.action_id)} onReject={() => reject(a.action_id, a.summary)} />
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="border-t hairline bg-[var(--bg)]/80 px-6 pb-5 pt-3 backdrop-blur">
        <div className="mx-auto max-w-4xl">
          {mode === "agent" && active.artifact && (
            <div className="mb-2 flex w-fit items-center gap-2 rounded-xl border border-brand-500/30 bg-brand-500/10 px-3 py-1.5 text-xs">
              <Paperclip size={13} className="text-brand-400" />
              <span className="font-semibold">{active.artifact.filename}</span>
              <span className="muted">{(active.artifact.size_bytes / 1024).toFixed(0)} KB · {active.artifact.file_count} files · {me.deploy_targets[active.artifact.target]}</span>
              <button aria-label="Detach upload" className="muted hover:text-rose-400"
                onClick={() => update(active.id, (c) => ({ ...c, artifact: null }))}><X size={13} /></button>
            </div>
          )}
          <div className="panel flex items-end gap-2 p-2 shadow-xl shadow-black/5 focus-within:border-brand-500/60">
            {mode === "agent" && can(me, "upload_code") && (
              <button className="rounded-xl p-2.5 muted transition hover:bg-black/5 hover:text-brand-400 dark:hover:bg-white/5"
                title="Deploy code (.zip)" aria-label="Deploy code" onClick={() => setUploadOpen(true)}>
                <Paperclip size={19} />
              </button>
            )}
            <textarea ref={textarea} rows={1} value={input} disabled={!!working}
              placeholder={mode === "agent" ? "Ask anything about your AWS… (Shift+Enter for a new line)" : "Search past resolutions…"}
              className="max-h-48 min-h-[44px] flex-1 resize-none bg-transparent px-2 py-2.5 text-sm outline-none"
              onChange={(e) => { setInput(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${e.target.scrollHeight}px`; }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }} />
            <button className="btn btn-primary h-11 w-11 rounded-xl p-0" disabled={!input.trim() || !!working}
              onClick={() => send(input)} aria-label="Send"><ArrowUp size={18} /></button>
          </div>
          <div className="mt-2 flex items-center justify-between px-1 text-[11px] muted">
            <span>{mode === "agent" ? "Changes are queued for approval · nothing runs without you" : "Read-only search of past resolutions"}</span>
            {can(me, "view_tickets") && <a href="/tickets" className="flex items-center gap-1 hover:text-brand-400"><Ticket size={11} /> Tickets</a>}
          </div>
        </div>
      </div>

      <UploadDialog me={me} open={uploadOpen} onClose={() => setUploadOpen(false)}
        onUploaded={(artifact, prompt) => { update(active.id, (c) => ({ ...c, artifact })); send(prompt, artifact); }} />
    </div>
  );
}
