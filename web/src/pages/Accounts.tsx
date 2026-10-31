import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Globe2, Plug, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import { cn, hue } from "../lib";
import { PageHeader } from "./PageHeader";

export function Accounts() {
  const queryClient = useQueryClient();
  const accounts = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const setup = useQuery({ queryKey: ["account-setup"], queryFn: api.accountSetup });
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [tests, setTests] = useState<Record<string, { ok: boolean; response: string } | "running">>({});

  const add = useMutation({
    mutationFn: () => api.addAccount(id, name),
    onSuccess: () => { setId(""); setName(""); toast.success("Account registered"); queryClient.invalidateQueries({ queryKey: ["accounts"] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  const remove = useMutation({
    mutationFn: (accountId: string) => api.removeAccount(accountId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["accounts"] }),
  });
  const test = async (accountId: string) => {
    setTests((t) => ({ ...t, [accountId]: "running" }));
    try {
      const result = await api.testAccount(accountId);
      setTests((t) => ({ ...t, [accountId]: result }));
    } catch (e) {
      setTests((t) => ({ ...t, [accountId]: { ok: false, response: (e as Error).message } }));
    }
  };

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <PageHeader icon={<Globe2 />} title="Accounts" subtitle="The agent only acts in accounts registered here, plus the platform account." />

        <div className="panel p-5">
          <div className="text-sm font-semibold">Connect an account</div>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm muted">
            <li>Deploy the cross-account role in the target account (or as a StackSet across your organization):</li>
          </ol>
          <div className="relative mt-2">
            <pre className="overflow-x-auto rounded-xl bg-[#0b0c16] p-3 pr-12 font-mono text-xs text-slate-200 scrollbar-thin">{setup.data?.command ?? "…"}</pre>
            <button className="absolute right-2 top-2 rounded-lg p-1.5 text-slate-400 hover:bg-white/10" aria-label="Copy command"
              onClick={() => { navigator.clipboard?.writeText(setup.data?.command ?? ""); toast.success("Copied"); }}>
              <Copy size={14} />
            </button>
          </div>
          <ol start={2} className="mt-3 list-decimal pl-5 text-sm muted"><li>Register it below, then test access.</li></ol>
          <form className="mt-4 flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <input className="input max-w-56 font-mono" placeholder="123456789012" value={id} onChange={(e) => setId(e.target.value)} />
            <input className="input max-w-56" placeholder="Name, e.g. prod" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn btn-primary" disabled={!id || add.isPending}><Plus size={15} /> Add account</button>
          </form>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          {(accounts.data ?? []).map((a) => {
            const result = tests[a.account_id];
            return (
              <div key={a.account_id} className="panel animate-rise p-5">
                <div className="flex items-start gap-3">
                  <div className="grid h-10 w-10 place-items-center rounded-xl text-sm font-bold text-white" style={{ background: hue(a.account_id) }}>
                    {(a.name || a.account_id)[0].toUpperCase()}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold">{a.name || a.account_id}</div>
                    <div className="font-mono text-xs muted">{a.account_id}</div>
                    <div className="mt-1 text-xs muted">{a.source === "console" ? `Added by ${a.added_by} · ${a.added_at?.slice(0, 10)}` : "From MANAGED_ACCOUNTS"}</div>
                  </div>
                </div>
                {result && result !== "running" && (
                  <div className={cn("mt-3 rounded-xl px-3 py-2 text-xs", result.ok ? "bg-emerald-500/10 text-emerald-400" : "bg-rose-500/10 text-rose-400")}>
                    {result.response}
                  </div>
                )}
                <div className="mt-4 flex gap-2">
                  <button className="btn btn-ghost" disabled={result === "running"} onClick={() => test(a.account_id)}>
                    <Plug size={14} /> {result === "running" ? "Testing…" : "Test access"}
                  </button>
                  {a.source === "console" && (
                    <button className="btn btn-ghost text-rose-400" onClick={() => remove.mutate(a.account_id)}><Trash2 size={14} /> Remove</button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {accounts.data && !accounts.data.length && <div className="text-center text-sm muted">No accounts registered yet.</div>}
      </div>
    </div>
  );
}
