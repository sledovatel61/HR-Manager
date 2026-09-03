import type {
  Candidate,
  CandidateCreateInput,
  CandidateInteraction,
  CandidateInteractionCreateInput,
  CandidateListQuery,
  CandidateTransfer,
  CandidateTransferInput,
  CandidateTransferResult,
  CandidateUpdateInput,
  CurrentUser,
  DuplicateCandidateDetail,
  HealthResponse,
  Paginated,
  AuditEvent,
  User,
  UserListItems,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

/** Error raised for non-2xx responses, carrying the backend's detail message. */
export class ApiError extends Error {
  readonly status: number;
  /** The backend's structured `detail` payload, when it was JSON. */
  readonly rawDetail: unknown;

  constructor(status: number, message: string, rawDetail: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.rawDetail = rawDetail;
  }
}

/**
 * Raised by candidate create/update when the backend answers 409 because a
 * normalized phone/email already exists. Carries the matching candidates so
 * the UI can offer the "create anyway" confirmation flow.
 */
export class DuplicateCandidateError extends ApiError {
  readonly duplicates: Candidate[];

  constructor(detail: DuplicateCandidateDetail) {
    super(409, detail.message, detail);
    this.name = "DuplicateCandidateError";
    this.duplicates = detail.duplicates;
  }
}

/** Read the XSRF/CSRF cookie set alongside the session (double-submit). */
export function readCsrfCookie(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)hrm_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

// --- Session-expiry notifications -------------------------------------------

type UnauthorizedListener = () => void;

const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Subscribe to 401 responses (other than login) — the shell returns to the
 * login screen when the session expires mid-flight. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

function emitUnauthorized(): void {
  for (const listener of unauthorizedListeners) {
    listener();
  }
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

  // Session expiry mid-flight: notify the shell so it returns to the login
  // screen with a clear message (login itself is exempt).
  if (response.status === 401 && path !== "/auth/login") {
    emitUnauthorized();
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
    const rawDetail =
      data && typeof data === "object" && "detail" in data
        ? (data as { detail: unknown }).detail
        : null;
    const detail =
      typeof rawDetail === "string"
        ? rawDetail
        : rawDetail && typeof rawDetail === "object" && "message" in rawDetail
          ? String((rawDetail as { message: unknown }).message)
          : `Ошибка запроса (${response.status}).`;
    throw new ApiError(response.status, detail, rawDetail);
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

// --- Candidates database ----------------------------------------------------

function candidateQuery(params: CandidateListQuery): string {
  const search = new URLSearchParams();
  if (params.query) search.set("query", params.query);
  if (params.stage) search.set("stage", params.stage);
  if (params.source) search.set("source", params.source);
  if (params.owner_id) search.set("owner_id", params.owner_id);
  if (params.include_deleted) search.set("include_deleted", "true");
  if (params.sort) search.set("sort", params.sort);
  if (params.direction) search.set("direction", params.direction);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const suffix = search.toString();
  return suffix ? `?${suffix}` : "";
}

export async function listCandidates(
  query: CandidateListQuery = {}
): Promise<Paginated<Candidate>> {
  return request<Paginated<Candidate>>(`/candidates${candidateQuery(query)}`);
}

export async function getCandidate(id: string): Promise<Candidate> {
  return request<Candidate>(`/candidates/${id}`);
}

/**
 * Create a candidate. When a normalized phone/email already exists the
 * backend answers 409 with the matching candidates; this is re-thrown as a
 * typed `DuplicateCandidateError` (call again with `confirm_duplicate: true`
 * to create the exact copy anyway).
 */
export async function createCandidate(input: CandidateCreateInput): Promise<Candidate> {
  try {
    return await request<Candidate>("/candidates", { method: "POST", body: input });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      throw new DuplicateCandidateError(error.rawDetail as DuplicateCandidateDetail);
    }
    throw error;
  }
}

/** Update candidate fields (stage changes are audited server-side). */
export async function updateCandidate(
  id: string,
  input: CandidateUpdateInput
): Promise<Candidate> {
  try {
    return await request<Candidate>(`/candidates/${id}`, {
      method: "PATCH",
      body: input,
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) {
      throw new DuplicateCandidateError(error.rawDetail as DuplicateCandidateDetail);
    }
    throw error;
  }
}

/** Soft-delete a candidate (recoverable via restoreCandidate). */
export async function deleteCandidate(id: string): Promise<Candidate> {
  return request<Candidate>(`/candidates/${id}`, { method: "DELETE" });
}

/** Restore a soft-deleted candidate. */
export async function restoreCandidate(id: string): Promise<Candidate> {
  return request<Candidate>(`/candidates/${id}/restore`, { method: "POST" });
}

/** Interaction history for a candidate (newest first, paginated). */
export async function listCandidateInteractions(
  id: string,
  limit = 50,
  offset = 0
): Promise<Paginated<CandidateInteraction>> {
  return request<Paginated<CandidateInteraction>>(
    `/candidates/${id}/interactions?limit=${limit}&offset=${offset}`
  );
}

/** Append an interaction history entry. */
export async function createCandidateInteraction(
  id: string,
  input: CandidateInteractionCreateInput
): Promise<CandidateInteraction> {
  return request<CandidateInteraction>(`/candidates/${id}/interactions`, {
    method: "POST",
    body: input,
  });
}

// --- Phase 4: HR directory & ownership transfers ----------------------------

/** Active HR users for owner/transfer pickers (minimal safe fields). */
export async function listHrUsers(): Promise<UserListItems> {
  return request<UserListItems>("/admin/users/hr");
}

/**
 * Transfer candidate responsibility to another HR. The backend performs the
 * ownership change and the immutable history record atomically.
 */
export async function transferCandidate(
  id: string,
  input: CandidateTransferInput
): Promise<CandidateTransferResult> {
  return request<CandidateTransferResult>(`/candidates/${id}/transfer`, {
    method: "POST",
    body: input,
  });
}

/** Ownership-transfer history (paginated, oldest first). */
export async function listCandidateTransfers(
  id: string,
  limit = 50,
  offset = 0
): Promise<Paginated<CandidateTransfer>> {
  return request<Paginated<CandidateTransfer>>(
    `/candidates/${id}/transfers?limit=${limit}&offset=${offset}`
  );
}
