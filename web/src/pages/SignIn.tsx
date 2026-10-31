import { ArrowRight, ShieldCheck, Sparkles, Zap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "../api";
import { AgentAvatar } from "../components/AgentAvatar";
import { Logo } from "../components/Logo";

export function SignIn({ mode, onSignedIn }: { mode: "oidc" | "local"; onSignedIn: () => void }) {
  const [email, setEmail] = useState("dev@example.com");
  const [tenant, setTenant] = useState("default");
  const [role, setRole] = useState("admin");
  const [busy, setBusy] = useState(false);

  const devLogin = async () => {
    setBusy(true);
    try {
      await api.devLogin(email, tenant, role);
      onSignedIn();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-full place-items-center p-6">
      <div className="grid w-full max-w-5xl items-center gap-10 md:grid-cols-2">
        <div className="animate-rise">
          <Logo />
          <h1 className="mt-8 text-4xl font-bold leading-tight tracking-tight md:text-5xl">
            Your AWS, <span className="gradient-text">on autopilot</span> —<br />with you in control.
          </h1>
          <p className="mt-4 max-w-md text-base muted">
            Chat with an agent that investigates, fixes, deploys and even builds apps across your AWS accounts.
            Nothing changes until you approve it.
          </p>
          <ul className="mt-8 space-y-3 text-sm">
            {[
              [Zap, "285 AWS tools across compute, network, data, security and cost"],
              [ShieldCheck, "Every change waits for your one-click approval"],
              [Sparkles, "Describe an app — it writes the code and ships it"],
            ].map(([Icon, text], i) => {
              const I = Icon as typeof Zap;
              return (
                <li key={i} className="flex items-center gap-3">
                  <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-500/15 text-brand-400"><I size={16} /></span>
                  {text as string}
                </li>
              );
            })}
          </ul>
        </div>

        <div className="panel animate-rise p-8 shadow-2xl shadow-brand-500/10" style={{ animationDelay: "80ms" }}>
          <div className="flex items-center gap-4">
            <AgentAvatar mood="happy" size={56} />
            <div>
              <div className="text-lg font-semibold">Welcome back</div>
              <div className="text-sm muted">Sign in to the Command Center</div>
            </div>
          </div>
          {mode === "oidc" ? (
            <a href="/auth/login" className="btn btn-primary mt-8 w-full py-3 text-base">
              Sign in with your organisation account <ArrowRight size={18} />
            </a>
          ) : (
            <form className="mt-6 space-y-3" onSubmit={(e) => { e.preventDefault(); devLogin(); }}>
              <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-400">
                Development sign-in — any identity is accepted. Never enabled on a deployed stack.
              </div>
              <label className="block text-xs font-medium muted">Email
                <input className="input mt-1" value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-xs font-medium muted">Tenant
                  <input className="input mt-1" value={tenant} onChange={(e) => setTenant(e.target.value)} />
                </label>
                <label className="block text-xs font-medium muted">Role
                  <select className="input mt-1" value={role} onChange={(e) => setRole(e.target.value)}>
                    {["viewer", "operator", "sre", "admin"].map((r) => <option key={r}>{r}</option>)}
                  </select>
                </label>
              </div>
              <button className="btn btn-primary w-full py-3" disabled={busy}>Sign in <ArrowRight size={16} /></button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
