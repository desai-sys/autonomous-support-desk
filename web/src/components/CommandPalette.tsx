import { Command, CornerDownLeft, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "../lib";

export interface PaletteItem {
  id: string;
  label: string;
  hint?: string;
  icon?: ReactNode;
  run: () => void;
}

/** Ctrl/⌘+K quick actions. */
export function CommandPalette({ items }: { items: PaletteItem[] }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") setOpen(false);
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("open-palette", onOpen);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("open-palette", onOpen); };
  }, []);

  useEffect(() => {
    if (open) { setQuery(""); setIndex(0); setTimeout(() => input.current?.focus(), 10); }
  }, [open]);

  const shown = useMemo(
    () => items.filter((i) => `${i.label} ${i.hint ?? ""}`.toLowerCase().includes(query.toLowerCase())),
    [items, query],
  );
  if (!open) return null;

  const choose = (item?: PaletteItem) => { if (item) { setOpen(false); item.run(); } };
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 p-4 pt-[14vh] backdrop-blur-sm" onMouseDown={() => setOpen(false)}>
      <div className="panel w-full max-w-xl animate-rise overflow-hidden shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b hairline px-4 py-3">
          <Search size={18} className="muted" />
          <input ref={input} value={query} placeholder="Type a command or search…"
            className="w-full bg-transparent text-sm outline-none"
            onChange={(e) => { setQuery(e.target.value); setIndex(0); }}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(i + 1, shown.length - 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
              if (e.key === "Enter") choose(shown[index]);
            }} />
          <kbd className="chip font-mono text-[10px]"><Command size={10} />K</kbd>
        </div>
        <ul className="max-h-80 overflow-y-auto p-2 scrollbar-thin">
          {shown.map((item, i) => (
            <li key={item.id}>
              <button onMouseEnter={() => setIndex(i)} onClick={() => choose(item)}
                className={cn("flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm",
                  i === index ? "bg-brand-500/15 text-brand-300" : "hover:bg-black/5 dark:hover:bg-white/5")}>
                <span className="muted">{item.icon}</span>
                <span className="flex-1 font-medium">{item.label}</span>
                {item.hint && <span className="text-xs muted">{item.hint}</span>}
                {i === index && <CornerDownLeft size={14} className="muted" />}
              </button>
            </li>
          ))}
          {!shown.length && <li className="px-3 py-6 text-center text-sm muted">No matching commands</li>}
        </ul>
      </div>
    </div>
  );
}
