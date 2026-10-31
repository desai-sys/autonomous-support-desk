import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import confetti from "canvas-confetti";
import { CheckCircle2, ChevronDown, RefreshCw, Ticket as TicketIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import { can, cn, timeAgo } from "../lib";
import type { Me, Ticket } from "../types";
import { PageHeader } from "./PageHeader";

const URGENCY = {
  critical: "border-rose-500/40 bg-rose-500/10 text-rose-400",
  high: "border-orange-500/40 bg-orange-500/10 text-orange-400",
  medium: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  low: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
};

function TicketRow({ ticket, me }: { ticket: Ticket; me: Me }) {
  const [open, setOpen] = useState(ticket.urgency === "critical");
  const [resolution, setResolution] = useState("");
  const queryClient = useQueryClient();
  const close = useMutation({
    mutationFn: () => api.closeTicket(ticket.ticket_id, resolution),
    onSuccess: () => {
      confetti({ particleCount: 70, spread: 60, origin: { y: 0.7 }, disableForReducedMotion: true });
      toast.success("Ticket closed — the agent will learn from it");
      queryClient.invalidateQueries({ queryKey: ["tickets"] });
    },
    onError: (e) => toast.error((e as Error).message),
  });
  return (
    <div className="panel animate-rise overflow-hidden">
      <button className="flex w-full items-center gap-3 px-5 py-4 text-left" onClick={() => setOpen(!open)}>
        <span className={cn("chip capitalize", URGENCY[ticket.urgency])}>{ticket.urgency}</span>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{ticket.query_text}</span>
        <span className="hidden text-xs muted sm:block">{ticket.category} · {timeAgo(ticket.created_at)}</span>
        <ChevronDown size={16} className={cn("muted transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t hairline px-5 py-4 text-sm">
          <div className="text-xs font-semibold uppercase tracking-wider muted">Request</div>
          <p className="mt-1 whitespace-pre-wrap">{ticket.query_text}</p>
          {ticket.agent_context && (
            <>
              <div className="mt-4 text-xs font-semibold uppercase tracking-wider muted">Agent response</div>
              <p className="mt-1 max-h-48 overflow-y-auto whitespace-pre-wrap text-xs muted scrollbar-thin">{ticket.agent_context}</p>
            </>
          )}
          <div className="mt-2 text-xs muted">#{ticket.ticket_id} · opened by {ticket.created_by || "unknown"}</div>
          {can(me, "resolve_tickets") && (
            <div className="mt-4">
              <textarea className="input min-h-24" placeholder="What fixed it? This teaches the agent for next time."
                value={resolution} onChange={(e) => setResolution(e.target.value)} />
              <div className="mt-2 flex justify-end">
                <button className="btn btn-primary" disabled={!resolution.trim() || close.isPending} onClick={() => close.mutate()}>
                  <CheckCircle2 size={15} /> Close ticket
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function Tickets({ me }: { me: Me }) {
  const tickets = useQuery({ queryKey: ["tickets"], queryFn: api.tickets });
  const sync = useMutation({
    mutationFn: api.syncKnowledge,
    onSuccess: (r) => toast.success(`Added ${r.uploaded} resolution(s) to the knowledge base`),
    onError: (e) => toast.error((e as Error).message),
  });
  const items = tickets.data ?? [];
  const count = (u: string) => items.filter((t) => t.urgency === u).length;

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <PageHeader icon={<TicketIcon />} title="Tickets"
          subtitle="Requests the agent escalated. Resolutions feed the knowledge base every hour."
          actions={can(me, "trigger_learning") && (
            <button className="btn btn-ghost" disabled={sync.isPending} onClick={() => sync.mutate()}>
              <RefreshCw size={15} className={cn(sync.isPending && "animate-spin")} /> Sync knowledge now
            </button>
          )} />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {[["Open", items.length, "text-brand-400"], ["Critical", count("critical"), "text-rose-400"],
            ["High", count("high"), "text-orange-400"], ["Medium", count("medium"), "text-amber-400"]].map(([label, n, cls]) => (
            <div key={label as string} className="panel p-4">
              <div className="text-xs font-semibold uppercase tracking-wider muted">{label}</div>
              <div className={cn("mt-1 text-3xl font-bold", cls as string)}>{n as number}</div>
            </div>
          ))}
        </div>
        {tickets.isLoading && <div className="skeleton h-20 rounded-2xl" />}
        {!tickets.isLoading && !items.length && (
          <div className="panel p-10 text-center">
            <CheckCircle2 className="mx-auto text-emerald-400" size={36} />
            <div className="mt-3 font-semibold">All clear</div>
            <div className="text-sm muted">No open tickets. The agent is handling everything.</div>
          </div>
        )}
        <div className="space-y-3">{items.map((t) => <TicketRow key={t.ticket_id} ticket={t} me={me} />)}</div>
      </div>
    </div>
  );
}
