export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-accent-500 shadow-lg shadow-brand-500/30">
        <svg viewBox="0 0 24 24" className="h-5 w-5 text-white" fill="currentColor" aria-hidden>
          <path d="M7 18a5 5 0 0 1-.7-9.95A6.5 6.5 0 0 1 18.8 9.6 4.2 4.2 0 0 1 18 18H7z" />
        </svg>
      </div>
      {!compact && (
        <div className="leading-tight">
          <div className="text-[15px] font-bold tracking-tight">CloudOps</div>
          <div className="text-[11px] muted">Command Center</div>
        </div>
      )}
    </div>
  );
}
