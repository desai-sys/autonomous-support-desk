import type { ReactNode } from "react";

export function PageHeader({ icon, title, subtitle, actions }: {
  icon: ReactNode; title: string; subtitle: string; actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex items-center gap-4">
        <div className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-brand-500/20 to-accent-500/20 text-brand-400">{icon}</div>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
          <p className="text-sm muted">{subtitle}</p>
        </div>
      </div>
      {actions}
    </div>
  );
}
