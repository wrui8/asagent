export type Role = "admin" | "user";
export type SessionStatus = "running" | "awaiting_approval" | "done" | "failed";

export interface User {
  id: string;
  username: string;
  role: Role;
  disabled: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
  refresh_token?: string | null;
}

export interface ProfileInfo {
  name: string;
  description: string | null;
  max_permission: number;
  max_cost_cny: number | null;
}

export interface AppVersionInfo {
  latest: string;
  min_supported: string;
  android_url: string;
  notes: string;
}

export interface Session {
  session_id: string;
  profile: string;
  title: string | null;
  owner_id: string | null;
  status: SessionStatus;
  cost: number;
  step: number;
  final_answer: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentEvent {
  seq: number;
  ts: string;
  session_id: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface Approval {
  call_id: string;
  tool: string;
  permission: number;
  args: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | string;
}
