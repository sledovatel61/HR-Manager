/** Health check payload returned by the backend (see app/schemas.py). */
export type CheckStatus = "ok" | "error";

export interface DatabaseHealth {
  status: CheckStatus;
  latency_ms: number | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  service: string;
  version: string;
  environment: string;
  checks: Record<string, DatabaseHealth>;
}

/** The environment the backend is running in, as reported by /health. */
export type BackendEnvironment = "development" | "test" | "production";

/** Application roles (see backend app/models.py UserRole). */
export type UserRole = "hr" | "manager" | "admin";

export const ROLE_LABELS: Record<UserRole, string> = {
  hr: "HR",
  manager: "Руководитель",
  admin: "Администратор",
};

/** Public representation of a user (no password data). */
export interface User {
  id: string;
  username: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  locked_until: string | null;
  last_login_at: string | null;
  created_at: string;
}

/** GET /auth/me payload: the current user plus the session CSRF token. */
export interface CurrentUser {
  user: User;
  csrf_token: string;
}

/** One audit trail entry (see backend AuditEventOut). */
export interface AuditEvent {
  id: string;
  action: string;
  user_id: string | null;
  actor_user_id: string | null;
  username: string | null;
  ip_address: string | null;
  user_agent: string | null;
  details: string | null;
  created_at: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
