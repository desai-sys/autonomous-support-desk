import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronRight, Clock, FileCode2, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api";
import { accountLabel, cn, riskOf, timeAgo } from "../lib";
import type { Account, Me, PendingAction } from "../types";

const RISK_STYLE = {
  high: { label: "High impact", cls: "border-rose-500/40 bg-rose-500/10 text-rose-400", icon: ShieldAlert },
  medium: { label: "Changes resources", cls: "border-amber-500/40 bg-amber-500/10 text-amber-400", icon: AlertTriangle },
  low: { label: "Low impact", cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400", icon: Check },
};

function AppPreview({ appId, version }: { appId: string; version: number }) {
  const bundle = useQuery({ queryKey: ["bundle", appId, version], queryFn: () => api.appBundle(appId, version) });
  const [openFile, setOpenFile] = useState<string | null>(null);
  if (bundle.isLoading) return <div className="skeleton mt-3 h-24 rounded-xl" />;
  if (bundle.error || !bundle.data) return <div className="mt-3 text-xs text-rose-400">Could not load the app for review.</div>;
  const { manifest, files, template } = bundle.data;
  const all: [string, string][] = [["template.yaml (CloudFormation)", template], ...Object.entries(files)];
  return (
    <div className="mt-3 rounded-xl border hairline">
      <div className="border-b hairline px-3 py-2 text-sm">
        <span className="font-semibold">{manifest.app_id}</span> <span className="chip ml-1 py-0">v{manifest.version}</span>
        <p className="mt-1 text-xs muted">{manifest.description}</p>
        {manifest.warnings.map((w) => <p key={w} className="mt-1 text-xs text-amber-400">⚠ {w}</p>)}
      </div>
      {all.map(([name, content]) => (
        <div key={name} className="border-b hairline last:border-0">
          <button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5"
            onClick={() => setOpenFile(openFile === name ? null : name)}>
            <ChevronRight size={14} className={cn("transition", openFile === name && "rotate-90")} />
            <FileCode2 size={14} className="text-brand-400" />
            <span className="font-mono text-xs">{name}</span>
            <span className="ml-auto text-[10px] muted">{content.split("\n").length} lines</span>
          </button>
          {openFile === name && (
            <pre className="max-h-96 overflow-auto border-t hairline bg-[#0b0c16] p-3 font-mono text-[11.5px] leading-relaxed text-slate-200 scrollbar-thin">{content}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

export function ApprovalCard({ action, me, accounts, busy, onApprove, onReject }: {
  action: PendingAction; me: Me; accounts: Account[]; busy: boolean; onApprove: () => void; onReject: () => void;
}) {
  const risk = RISK_STYLE[riskOf(action.tool_name)];
  const RiskIcon = risk.icon;
  const canApprove = me.permissions.includes("approve_changes");
  const ownRequest = me.require_separate_approver && action.requested_by === me.user_id;
  const minutesLeft = Math.max(0, Math.round((action.expires_at * 1000 - Date.now()) / 60000));
  const isApp = action.tool_name === "deploy_generated_app";

  return (
    <div className="panel animate-rise overflow-hidden border-l-4 border-l-amber-400 shadow-lg shadow-amber-500/5">
      <div className="flex flex-wrap items-center gap-2 px-4 pt-4">
        <span className={cn("chip", risk.cls)}><RiskIcon size={12} /> {risk.label}</span>
        <span className="font-mono text-sm font-semibold">{action.tool_name}</span>
        <span className="ml-auto flex items-center gap-1 text-xs muted"><Clock size={12} /> expires in {minutesLeft}m</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 px-4 text-xs">
        <span className="chip">🌐 {accountLabel(action.account_id, accounts)}</span>
        <span className="chip font-mono">📍 {action.region || me.default_region}</span>
        <span className="chip">👤 {action.requested_by} · {timeAgo(action.created_at)}</span>
      </div>
      <div className="px-4">
        <div className="mt-3 overflow-hidden rounded-xl border hairline">
          <table className="w-full text-xs">
            <tbody>
              {Object.entries(action.tool_input).map(([k, v]) => (
                <tr key={k} className="border-b hairline last:border-0">
                  <td className="w-40 bg-black/5 px-3 py-1.5 font-mono muted dark:bg-white/5">{k}</td>
                  <td className="break-all px-3 py-1.5 font-mono">{typeof v === "string" ? (v.length > 400 ? `${v.slice(0, 400)}…` : v) : JSON.stringify(v)}</td>
                </tr>
              ))}
              {!Object.keys(action.tool_input).length && <tr><td className="px-3 py-2 muted">No parameters</td></tr>}
            </tbody>
          </table>
        </div>
        {isApp && <AppPreview appId={String(action.tool_input.app_id)} version={Number(action.tool_input.version) || 0} />}
      </div>
      <div className="mt-4 flex items-center gap-2 border-t hairline bg-black/[0.02] px-4 py-3 dark:bg-white/[0.02]">
        <span className="mr-auto text-xs muted">
          {ownRequest ? "Another person must approve changes you requested." : canApprove ? "Nothing runs until you approve." : "Your role can't approve changes."}
        </span>
        <button className="btn btn-ghost" disabled={!canApprove || busy} onClick={onReject}><X size={15} /> Reject</button>
        <button className="btn btn-success" disabled={!canApprove || ownRequest || busy} onClick={onApprove}>
          <Check size={15} /> {busy ? "Running…" : "Approve & run"}
        </button>
      </div>
    </div>
  );
}
