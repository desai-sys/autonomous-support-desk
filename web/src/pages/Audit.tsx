import { useQuery } from "@tanstack/react-query";
import { ScrollText, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "../api";
import { cn } from "../lib";
import { PageHeader } from "./PageHeader";

const RESULT = {
  SUCCESS: "text-emerald-400", SUCCEEDED: "text-emerald-400", PENDING: "text-amber-400",
  FAILED: "text-rose-400", FAILURE: "text-rose-400", DENIED: "text-rose-400",
} as Record<string, string>;

export function Audit() {
  const events = useQuery({ queryKey: ["audit"], queryFn: api.audit, refetchInterval: 30_000 });
  const [filter, setFilter] = useState("");
  const rows = useMemo(() => (events.data ?? []).filter((e) =>
    !filter || `${e.action} ${e.user_id} ${e.resource}`.toLowerCase().includes(filter.toLowerCase())), [events.data, filter]);

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-6xl space-y-6 px-6 py-8">
        <PageHeader icon={<ScrollText />} title="Audit log" subtitle="Who asked for, approved and ran what."
          actions={
            <label className="relative w-72">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 muted" />
              <input className="input pl-9" placeholder="Filter events…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            </label>
          } />
        <div className="panel overflow-hidden">
          <table className="w-full text-sm">
            <thead className="panel-2 text-left text-xs uppercase tracking-wider muted">
              <tr><th className="px-4 py-3">Time (UTC)</th><th className="px-4 py-3">User</th><th className="px-4 py-3">Event</th>
                <th className="px-4 py-3">Resource</th><th className="px-4 py-3">Result</th></tr>
            </thead>
            <tbody>
              {rows.map((e, i) => (
                <tr key={i} className="border-t hairline hover:bg-black/[0.02] dark:hover:bg-white/[0.02]">
                  <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs muted">{e.timestamp.slice(0, 19).replace("T", " ")}</td>
                  <td className="px-4 py-2.5">{e.user_id}</td>
                  <td className="px-4 py-2.5"><span className="chip py-0 font-mono text-[11px]">{e.action}</span></td>
                  <td className="max-w-md truncate px-4 py-2.5 font-mono text-xs" title={e.resource}>{e.resource}</td>
                  <td className={cn("px-4 py-2.5 text-xs font-semibold", RESULT[e.result] ?? "muted")}>{e.result}</td>
                </tr>
              ))}
              {!rows.length && <tr><td colSpan={5} className="px-4 py-10 text-center muted">{events.isLoading ? "Loading…" : "No events"}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
