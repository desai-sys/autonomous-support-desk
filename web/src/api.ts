import type { Account, AppBundle, ApproveResult, Artifact, AuditEvent, ChatResult, Me, PendingAction, Ticket } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  // Required on state-changing calls (CSRF protection on the server).
  headers.set("X-CloudOps", "1");
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Start a background job and poll until it finishes (agent turns can take minutes). */
async function runJob<T>(start: Promise<{ job_id: string }>, onTick?: (seconds: number) => void): Promise<T> {
  const { job_id } = await start;
  const started = Date.now();
  for (let delay = 800; ; delay = Math.min(delay * 1.3, 3000)) {
    await sleep(delay);
    onTick?.(Math.round((Date.now() - started) / 1000));
    const job = await request<{ status: string; result: T & { error?: string } }>(`/api/jobs/${job_id}`);
    if (job.status === "succeeded") return job.result;
    if (job.status === "failed") throw new ApiError(500, job.result?.error ?? "The request failed.");
    if (Date.now() - started > 20 * 60 * 1000) throw new ApiError(504, "Timed out waiting for the agent.");
  }
}

export const api = {
  authConfig: () => request<{ mode: "oidc" | "local"; environment: string }>("/auth/config"),
  devLogin: (email: string, tenant: string, role: string) => post("/auth/dev-login", { email, tenant, role }),
  me: () => request<Me>("/api/me"),

  chat: (
    body: {
      conversation_id: string; message: string; mode: "agent" | "knowledge"; accounts: string[];
      region: string; artifact: Artifact | null; history: string[];
    },
    onTick?: (s: number) => void,
  ) => runJob<ChatResult>(post("/api/chat", body), onTick),
  pending: (conversationId: string) => request<PendingAction[]>(`/api/conversations/${conversationId}/actions`),
  approve: (actionId: string, conversationId: string, onTick?: (s: number) => void) =>
    runJob<ApproveResult>(post(`/api/actions/${actionId}/approve`, { conversation_id: conversationId }), onTick),
  reject: (actionId: string) => post(`/api/actions/${actionId}/reject`),
  appBundle: (appId: string, version: number) => request<AppBundle>(`/api/apps/${appId}/versions/${version}`),
  upload: (file: File, target: string, options: Record<string, string>) => {
    const form = new FormData();
    form.append("file", file);
    form.append("target", target);
    form.append("options", JSON.stringify(options));
    return request<Artifact>("/api/uploads", { method: "POST", body: form });
  },

  accounts: () => request<Account[]>("/api/accounts"),
  addAccount: (account_id: string, name: string) => post<Account>("/api/accounts", { account_id, name }),
  removeAccount: (id: string) => request(`/api/accounts/${id}`, { method: "DELETE" }),
  testAccount: (id: string) => post<{ ok: boolean; response: string }>(`/api/accounts/${id}/test`),
  accountSetup: () => request<{ command: string }>("/api/accounts/setup"),

  tickets: () => request<Ticket[]>("/api/tickets"),
  closeTicket: (id: string, resolution: string) => post(`/api/tickets/${id}/close`, { resolution }),
  syncKnowledge: () => post<{ uploaded: number; failed: number }>("/api/knowledge/sync"),
  audit: () => request<AuditEvent[]>("/api/audit"),
};
