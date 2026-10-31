import { clsx, type ClassValue } from "clsx";
import { useEffect, useState } from "react";
import type { Account, Me } from "./types";

export const cn = (...classes: ClassValue[]) => clsx(classes);

export const uid = () =>
  (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/-/g, "");

export const can = (me: Me | undefined, permission: string) => !!me?.permissions.includes(permission);

export function accountLabel(id: string | null | undefined, accounts: Account[] = []) {
  if (!id) return "Platform account";
  const name = accounts.find((a) => a.account_id === id)?.name;
  return name && name !== id ? `${name} · ${id}` : id;
}

export function timeAgo(input: number | string) {
  const t = typeof input === "number" ? input : Date.parse(input);
  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Stable pastel colour per string (account chips, avatars). */
export function hue(text: string) {
  let h = 0;
  for (const ch of text) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 60%)`;
}

export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* storage may be unavailable */
    }
  }, [key, value]);
  return [value, setValue] as const;
}

export type Risk = "high" | "medium" | "low";

export function riskOf(tool: string): Risk {
  if (/(delete|terminate|purge|remove|deregister|rotate|rollback)/.test(tool)) return "high";
  if (/(deploy|launch|create|modify|update|scale|run_|call_aws_api|invoke|put_|start|stop|reboot|restart|enable|disable|pause|resume)/.test(tool))
    return "medium";
  return "low";
}

export const ROLE_COLORS: Record<string, string> = {
  admin: "bg-fuchsia-500/15 text-fuchsia-400 border-fuchsia-500/30",
  sre: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  operator: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  viewer: "bg-slate-500/15 text-slate-400 border-slate-500/30",
};

/** Registered 12-digit ids typed in free text (ids inside ARNs are ignored). */
export function accountIdsIn(text: string) {
  return [...text.matchAll(/(?<![:\d])(\d{12})(?![\d:])/g)].map((m) => m[1]);
}
