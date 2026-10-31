export type Role = "viewer" | "operator" | "sre" | "admin";

export interface Me {
  user_id: string;
  email: string;
  role: Role;
  tenant: string;
  permissions: string[];
  environment: string;
  auth_mode: "oidc" | "local";
  default_region: string;
  regions: string[];
  deploy_targets: Record<string, string>;
  require_separate_approver: boolean;
  max_upload_mb: number;
}

export interface Account {
  account_id: string;
  name?: string;
  source: "config" | "console";
  added_by?: string;
  added_at?: string;
}

export interface PendingAction {
  action_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  summary: string;
  account_id: string;
  region: string;
  requested_by: string;
  created_at: string;
  expires_at: number;
  status: string;
}

export interface Artifact {
  uri: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  file_count: number;
  target: string;
  options: Record<string, string>;
}

export interface ChatResult {
  text: string;
  resolution_type: string;
  escalate: boolean;
  pending_actions: PendingAction[];
  metadata: Record<string, unknown>;
  ticket_id?: string;
  ticket_error?: string;
}

export interface ActionResult {
  action_id: string;
  summary?: string;
  account_id?: string | null;
  region?: string | null;
  status: "succeeded" | "failed" | "error";
  result?: string;
  error?: string;
}

export interface ApproveResult {
  response: string;
  results: ActionResult[];
  error?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  at: number;
  kind?: "chat" | "result" | "error" | "info";
  meta?: Record<string, unknown>;
  ticketId?: string;
}

export interface Ticket {
  ticket_id: string;
  tenant_id: string;
  query_text: string;
  category: string;
  urgency: "critical" | "high" | "medium" | "low";
  status: string;
  created_by?: string;
  created_at: string;
  agent_context?: string;
}

export interface AuditEvent {
  timestamp: string;
  user_id: string;
  action: string;
  resource: string;
  result: string;
  details?: Record<string, unknown>;
}

export interface AppBundle {
  manifest: { app_id: string; version: number; description: string; files: string[]; warnings: string[]; created_by: string };
  files: Record<string, string>;
  template: string;
}
