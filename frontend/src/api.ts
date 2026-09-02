import type {
  CurrentUser,
  HealthResponse,
  Paginated,
  AuditEvent,
  User,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

/** Error raised for non-2xx responses, carrying the backend's detail message. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Read the XSRF/CSRF cookie set alongside the session (double-submit). */
export function readCsrfCookie(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)hrm_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  // State-changing requests carry the CSRF token from the cookie. The backend
  // also reads it from this X-CSRF-Token header (double-submit pattern).
  if (method !== "GET" && method !== "HEAD") {
    const token = readCsrfCookie();
    if (token) {
      headers["X-CSRF-Token"] = token;
    }
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "Сеть недоступна: не удалось связаться с сервером.");
  }

  if (response.status === 204 || response.headers.get("content-length") === "0") {
    return undefined as T;
  }

  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `Ошибка запроса (${response.status}).`;
    throw new ApiError(response.status, detail);
  }

  return data as T;
}

/** Fetch the backend health report, or null when the backend is unreachable. */
export async function fetchHealth(): Promise<HealthResponse | null> {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as HealthResponse;
  } catch {
    return null;
  }
}

// --- Authentication ---------------------------------------------------------

export async function login(username: string, password: string): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/login", {
    method: "POST",
    body: { username, password },
  });
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/auth/me");
}

export async function logout(): Promise<void> {
  await request<void>("/auth/logout", { method: "POST" });
}

// --- Admin: users -----------------------------------------------------------

export async function listUsers(): Promise<Paginated<User>> {
  return request<Paginated<User>>("/admin/users?limit=200");
}

export async function createUser(input: {
  username: string;
  full_name: string;
  role: string;
  password: string;
}): Promise<User> {
  return request<User>("/admin/users", { method: "POST", body: input });
}

// --- Admin: audit log -------------------------------------------------------

export async function listAuditEvents(limit = 50): Promise<Paginated<AuditEvent>> {
  return request<Paginated<AuditEvent>>(`/admin/audit?limit=${limit}`);
}
