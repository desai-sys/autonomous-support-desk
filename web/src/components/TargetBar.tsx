import { Check, ChevronDown, Globe2, MapPin } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { accountLabel, cn, hue } from "../lib";
import type { Account } from "../types";

function useClickAway(onAway: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onAway(); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onAway]);
  return ref;
}

/** Which accounts and region the next request targets. */
export function TargetBar({ accounts, selected, onSelected, region, regions, onRegion }: {
  accounts: Account[]; selected: string[]; onSelected: (ids: string[]) => void;
  region: string; regions: string[]; onRegion: (r: string) => void;
}) {
  const [open, setOpen] = useState<"accounts" | "region" | null>(null);
  const ref = useClickAway(() => setOpen(null));
  const toggle = (id: string) => onSelected(selected.includes(id) ? selected.filter((a) => a !== id) : [...selected, id]);

  return (
    <div ref={ref} className="relative flex flex-wrap items-center gap-2">
      <button className="chip hover:border-brand-500/60" onClick={() => setOpen(open === "accounts" ? null : "accounts")}>
        <Globe2 size={13} className="text-brand-400" />
        {selected.length === 0 ? "Platform account" : selected.length === 1 ? accountLabel(selected[0], accounts) : `${selected.length} accounts`}
        <ChevronDown size={13} className="muted" />
      </button>
      <button className="chip hover:border-brand-500/60" onClick={() => setOpen(open === "region" ? null : "region")}>
        <MapPin size={13} className="text-accent-400" /> {region} <ChevronDown size={13} className="muted" />
      </button>
      {selected.length > 1 && <span className="text-xs muted">runs in each account</span>}

      {open === "accounts" && (
        <div className="panel absolute left-0 top-9 z-30 w-80 animate-rise p-2 shadow-xl">
          <button onClick={() => onSelected([])}
            className={cn("flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5")}>
            <span className="h-2.5 w-2.5 rounded-full bg-brand-500" />
            <span className="flex-1">Platform account</span>
            {selected.length === 0 && <Check size={15} className="text-brand-400" />}
          </button>
          {accounts.map((a) => (
            <button key={a.account_id} onClick={() => toggle(a.account_id)}
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5">
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: hue(a.account_id) }} />
              <span className="flex-1 truncate">{a.name || a.account_id}<span className="ml-2 font-mono text-xs muted">{a.account_id}</span></span>
              {selected.includes(a.account_id) && <Check size={15} className="text-brand-400" />}
            </button>
          ))}
          {!accounts.length && <div className="px-3 py-3 text-xs muted">No other accounts registered yet. Admins add them on the Accounts page.</div>}
        </div>
      )}
      {open === "region" && (
        <div className="panel absolute left-0 top-9 z-30 grid max-h-72 w-64 animate-rise grid-cols-1 overflow-y-auto p-2 shadow-xl scrollbar-thin md:left-40">
          {regions.map((r) => (
            <button key={r} onClick={() => { onRegion(r); setOpen(null); }}
              className="flex items-center justify-between rounded-lg px-3 py-1.5 text-left font-mono text-sm hover:bg-black/5 dark:hover:bg-white/5">
              {r} {r === region && <Check size={14} className="text-brand-400" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
