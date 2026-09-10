import type {
  AccessGrant,
  AdminChannels,
  CandidateChannels,
  CandidateEmailConfirmation,
  CandidateChannelName,
  CandidateConsent,
  CandidateMessageList,
  CandidateMessagePreview,
  CandidateMessagePreviewInput,
  CandidateMessageSendInput,
  CandidateMessageSendResult,
  CandidateTelegramConfirm,
  CandidateTelegramInvite,
  ChannelCheckResult,
  ChannelTestResult,
  ConsentResult,
  EmailConfirmResult,
  EmailSetResult,
  IntegrationStatus,
  TelegramChannelStatus,
  TelegramConfirmResult,
  TelegramLinkCode,
  AnalyticsFunnelReport,
  DeliveryInfo,
  NotificationListPayload,
  NotificationPreferences,
  NotificationResolve,
  QueueDiagnostics,
  RedeemOwnerInput,
  Reminder,
  ReminderImportance,
  ReminderListPayload,
  ReminderRecurrence,
  ReminderStatus,
  SetupPreview,
  SetupState,
  UpdateInstallResult,
  UpdateStatus,
  AnalyticsKpiReport,
  AnalyticsQuery,
  AuditEvent,
  CalendarEvent,
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
  EventCreateInput,
  EventHistoryEntry,
  EventListQuery,
  EventUpdateInput,
  HealthResponse,
  Paginated,
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

/** Phase 13: update channel status (state machine + versions, no secrets). */
export async function fetchUpdateStatus(): Promise<UpdateStatus> {
  return request<UpdateStatus>("/updates/status");
}

/** Check the signed channel manifest (server verifies signature + policy). */
export async function checkUpdates(): Promise<UpdateStatus> {
  return request<UpdateStatus>("/updates/check", { method: "POST" });
}

/** Download the package into the server staging (size/SHA256 checked). */
export async function downloadUpdate(): Promise<UpdateStatus> {
  return request<UpdateStatus>("/updates/download", { method: "POST" });
}

/** Queue an install for the Windows engine (existing Phase 12 updater). */
export async function requestUpdateInstall(): Promise<UpdateInstallResult> {
  return request<UpdateInstallResult>("/updates/install", { method: "POST" });
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

// --- Phase 5: calendar events ------------------------------------------------

/** Server-side event listing (calendar, upcoming/overdue, reminders). */
export async function listEvents(params: EventListQuery = {}): Promise<Paginated<CalendarEvent>> {
  const search = new URLSearchParams();
  if (params.from) search.set("from", params.from);
  if (params.to) search.set("to", params.to);
  if (params.owner_id) search.set("owner_id", params.owner_id);
  if (params.candidate_id) search.set("candidate_id", params.candidate_id);
  if (params.type) search.set("type", params.type);
  if (params.status) search.set("status", params.status);
  if (params.remind_from) search.set("remind_from", params.remind_from);
  if (params.remind_to) search.set("remind_to", params.remind_to);
  if (params.sort) search.set("sort", params.sort);
  if (params.direction) search.set("direction", params.direction);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.offset !== undefined) search.set("offset", String(params.offset));
  const suffix = search.toString();
  return request<Paginated<CalendarEvent>>(`/events${suffix ? `?${suffix}` : ""}`);
}

/** One event in the current user's visibility zone. */
export async function getEvent(id: string): Promise<CalendarEvent> {
  return request<CalendarEvent>(`/events/${id}`);
}

/** Create a scheduled event for an accessible candidate. */
export async function createEvent(input: EventCreateInput): Promise<CalendarEvent> {
  return request<CalendarEvent>("/events", { method: "POST", body: input });
}

/** Edit/reschedule/complete/postpone (optimistic concurrency). */
export async function updateEvent(id: string, input: EventUpdateInput): Promise<CalendarEvent> {
  return request<CalendarEvent>(`/events/${id}`, { method: "PATCH", body: input });
}

/** Immutable business history of an event (oldest first, paginated). */
export async function listEventHistory(
  id: string,
  limit = 50,
  offset = 0
): Promise<Paginated<EventHistoryEntry>> {
  return request<Paginated<EventHistoryEntry>>(`/events/${id}/history?limit=${limit}&offset=${offset}`);
}

// --- Analytics (analytics phase) --------------------------------------------

function analyticsSearch(query: AnalyticsQuery): string {
  const search = new URLSearchParams();
  search.set("from", query.from);
  search.set("to", query.to);
  if (query.timezone) search.set("timezone", query.timezone);
  if (query.hr_id) search.set("hr_id", query.hr_id);
  if (query.source) search.set("source", query.source);
  return search.toString();
}

/** Team KPI report for the period (manager/admin only). */
export async function fetchAnalyticsKpi(query: AnalyticsQuery): Promise<AnalyticsKpiReport> {
  return request<AnalyticsKpiReport>(`/analytics/kpi?${analyticsSearch(query)}`);
}

/** Funnel + inter-stage conversions for the period (manager/admin only). */
export async function fetchAnalyticsFunnel(query: AnalyticsQuery): Promise<AnalyticsFunnelReport> {
  return request<AnalyticsFunnelReport>(`/analytics/funnel?${analyticsSearch(query)}`);
}

export interface AnalyticsCsvExport {
  blob: Blob;
  /** Server-provided attachment filename (Content-Disposition). */
  filename: string;
}

/**
 * Download the CSV export for the current report parameters. The export is
 * only successful after a 2xx response arrives; any error (401/403/422/5xx)
 * raises ApiError with the backend detail — the UI shows no false success.
 */
export async function exportAnalyticsCsv(query: AnalyticsQuery): Promise<AnalyticsCsvExport> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/analytics/export?format=csv&${analyticsSearch(query)}`, {
      method: "GET",
      headers: { Accept: "text/csv" },
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "Сеть недоступна: не удалось связаться с сервером.");
  }

  if (response.status === 401) {
    emitUnauthorized();
  }

  if (!response.ok) {
    let rawDetail: unknown = null;
    try {
      const data: unknown = await response.json();
      if (data && typeof data === "object" && "detail" in data) {
        rawDetail = (data as { detail: unknown }).detail;
      }
    } catch {
      // non-JSON error body — keep the generic message
    }
    const detail =
      typeof rawDetail === "string"
        ? rawDetail
        : `Ошибка экспорта (${response.status}).`;
    throw new ApiError(response.status, detail, rawDetail);
  }

  const disposition = response.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  return {
    blob: await response.blob(),
    filename: match?.[1] ?? "analytics.csv",
  };
}

// --- Phase 8: notification center -------------------------------------------

export async function listNotifications(query: {
  unread_only?: boolean;
  limit?: number;
  offset?: number;
}): Promise<NotificationListPayload> {
  const params = new URLSearchParams();
  if (query.unread_only) params.set("unread_only", "true");
  params.set("limit", String(query.limit ?? 20));
  params.set("offset", String(query.offset ?? 0));
  return request<NotificationListPayload>(`/notifications?${params}`);
}

export async function unreadCount(): Promise<{ count: number }> {
  return request<{ count: number }>("/notifications/unread-count");
}

export async function markNotificationsRead(ids: string[]): Promise<void> {
  await request<void>("/notifications/mark-read", { method: "POST", body: { ids } });
}

export async function markAllNotificationsRead(): Promise<void> {
  await request<void>("/notifications/mark-all-read", { method: "POST" });
}

export async function dismissNotifications(ids: string[]): Promise<void> {
  await request<void>("/notifications/dismiss", { method: "POST", body: { ids } });
}

export async function resolveNotification(id: string): Promise<NotificationResolve> {
  return request<NotificationResolve>(`/notifications/${id}/resolve`);
}

export async function notificationDelivery(id: string): Promise<DeliveryInfo> {
  return request<DeliveryInfo>(`/notifications/${id}/delivery`);
}

// --- Phase 8: personal reminders --------------------------------------------

export async function listReminders(query: {
  status?: ReminderStatus;
  limit?: number;
  offset?: number;
}): Promise<ReminderListPayload> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  params.set("limit", String(query.limit ?? 20));
  params.set("offset", String(query.offset ?? 0));
  return request<ReminderListPayload>(`/reminders?${params}`);
}

export async function createReminder(input: {
  title: string;
  note?: string | null;
  candidate_id?: string | null;
  due_at: string;
  timezone: string;
  importance?: ReminderImportance;
  recurrence?: ReminderRecurrence;
  assignee_user_id?: string | null;
}): Promise<Reminder> {
  return request<Reminder>("/reminders", { method: "POST", body: input });
}

export async function updateReminder(
  id: string,
  input: { expected_version: number } & Partial<{
    title: string;
    note: string | null;
    due_at: string;
    timezone: string;
    importance: ReminderImportance;
    recurrence: ReminderRecurrence;
    candidate_id: string | null;
  }>,
): Promise<Reminder> {
  return request<Reminder>(`/reminders/${id}`, { method: "PATCH", body: input });
}

export async function completeReminder(id: string): Promise<Reminder> {
  return request<Reminder>(`/reminders/${id}/complete`, { method: "POST" });
}

export async function cancelReminder(id: string): Promise<Reminder> {
  return request<Reminder>(`/reminders/${id}/cancel`, { method: "POST" });
}

// --- Phase 8: notification preferences --------------------------------------

export async function getPreferences(): Promise<NotificationPreferences> {
  return request<NotificationPreferences>("/notification-preferences");
}

export async function savePreferences(input: {
  timezone: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
  workdays: number[];
  enabled_types: string[];
  enabled_channels: string[];
}): Promise<NotificationPreferences> {
  return request<NotificationPreferences>("/notification-preferences", {
    method: "PUT",
    body: input,
  });
}

export async function listTimezones(): Promise<{ timezones: string[] }> {
  return request<{ timezones: string[] }>("/notification-preferences/timezones");
}

// --- Phase 8: setup wizard and admin queue ----------------------------------

export async function fetchSetupState(): Promise<SetupState> {
  return request<SetupState>("/setup/state");
}

export async function createPilot(input: {
  username: string;
  password: string;
  full_name?: string;
}): Promise<{ user_id: string; username: string }> {
  return request<{ user_id: string; username: string }>("/setup/pilot", {
    method: "POST",
    body: input,
  });
}

export async function listAccessGrants(): Promise<{ items: AccessGrant[] }> {
  return request<{ items: AccessGrant[] }>("/admin/access-grants");
}

export async function grantPilotAccess(userId: string): Promise<AccessGrant> {
  return request<AccessGrant>("/admin/access-grants", {
    method: "POST",
    body: { user_id: userId },
  });
}

export async function revokePilotAccess(userId: string, reason: string): Promise<AccessGrant> {
  return request<AccessGrant>("/admin/access-grants", {
    method: "POST",
    body: { user_id: userId, revoke: true, revoke_reason: reason },
  });
}

export async function fetchQueueDiagnostics(): Promise<QueueDiagnostics> {
  return request<QueueDiagnostics>("/admin/ops/notifications/queue");
}

// --- Phase 9: external integrations (Telegram/SMTP) ---------------------------

export async function getIntegrationStatus(): Promise<IntegrationStatus> {
  return request<IntegrationStatus>("/integrations/status");
}

export async function createTelegramLinkCode(): Promise<TelegramLinkCode> {
  return request<TelegramLinkCode>("/integrations/telegram/link-code", { method: "POST" });
}

export async function confirmTelegramLink(): Promise<TelegramConfirmResult> {
  return request<TelegramConfirmResult>("/integrations/telegram/confirm", { method: "POST" });
}

export async function unlinkTelegram(): Promise<TelegramChannelStatus> {
  return request<TelegramChannelStatus>("/integrations/telegram/unlink", { method: "POST" });
}

export async function updateTelegramConsent(
  optIn: boolean,
  consentGranted: boolean,
): Promise<ConsentResult> {
  return request<ConsentResult>("/integrations/telegram/consent", {
    method: "PUT",
    body: { opt_in: optIn, consent_granted: consentGranted },
  });
}

export async function queueTelegramTest(): Promise<ChannelTestResult> {
  return request<ChannelTestResult>("/integrations/telegram/test", { method: "POST" });
}

export async function setNotificationEmail(email: string): Promise<EmailSetResult> {
  return request<EmailSetResult>("/integrations/email", {
    method: "PUT",
    body: { email },
  });
}

export async function confirmNotificationEmail(token: string): Promise<EmailConfirmResult> {
  return request<EmailConfirmResult>("/integrations/email/confirm", {
    method: "POST",
    body: { token },
  });
}

export async function removeNotificationEmail(): Promise<void> {
  return request<void>("/integrations/email", { method: "DELETE" });
}

export async function updateEmailConsent(
  optIn: boolean,
  consentGranted: boolean,
): Promise<ConsentResult> {
  return request<ConsentResult>("/integrations/email/consent", {
    method: "PUT",
    body: { opt_in: optIn, consent_granted: consentGranted },
  });
}

export async function fetchAdminChannels(): Promise<AdminChannels> {
  return request<AdminChannels>("/admin/integrations/channels");
}

export async function checkTelegramConfig(): Promise<ChannelCheckResult> {
  return request<ChannelCheckResult>("/admin/integrations/telegram/check", { method: "POST" });
}

export async function checkSmtpConfig(): Promise<ChannelCheckResult> {
  return request<ChannelCheckResult>("/admin/integrations/smtp/check", { method: "POST" });
}

export async function queueAdminSmtpTest(): Promise<ChannelTestResult> {
  return request<ChannelTestResult>("/admin/integrations/smtp/test-send", { method: "POST" });
}

// --- Phase 10: one-way candidate messages --------------------------------------

export async function initiateCandidateEmailConfirmation(
  candidateId: string,
): Promise<CandidateEmailConfirmation> {
  return request<CandidateEmailConfirmation>(
    `/candidates/${candidateId}/channels/email/confirmation`,
    { method: "POST" },
  );
}

export async function getCandidateChannels(candidateId: string): Promise<CandidateChannels> {
  return request<CandidateChannels>(`/candidates/${candidateId}/channels`);
}

export async function updateCandidateChannelConsent(
  candidateId: string,
  channel: CandidateChannelName,
  granted: boolean,
): Promise<CandidateConsent> {
  return request<CandidateConsent>(`/candidates/${candidateId}/channels/${channel}/consent`, {
    method: "POST",
    body: { granted },
  });
}

export async function createCandidateTelegramInvite(
  candidateId: string,
): Promise<CandidateTelegramInvite> {
  return request<CandidateTelegramInvite>(`/candidates/${candidateId}/channels/telegram/invite`, {
    method: "POST",
  });
}

export async function confirmCandidateTelegram(
  candidateId: string,
): Promise<CandidateTelegramConfirm> {
  return request<CandidateTelegramConfirm>(
    `/candidates/${candidateId}/channels/telegram/confirm`,
    { method: "POST" },
  );
}

export async function unlinkCandidateTelegram(
  candidateId: string,
): Promise<CandidateTelegramConfirm> {
  return request<CandidateTelegramConfirm>(
    `/candidates/${candidateId}/channels/telegram/unlink`,
    { method: "POST" },
  );
}

export async function listCandidateMessages(
  candidateId: string,
  limit = 20,
  offset = 0,
): Promise<CandidateMessageList> {
  return request<CandidateMessageList>(
    `/candidates/${candidateId}/messages?limit=${limit}&offset=${offset}`,
  );
}

export async function previewCandidateMessage(
  candidateId: string,
  input: CandidateMessagePreviewInput,
): Promise<CandidateMessagePreview> {
  return request<CandidateMessagePreview>(`/candidates/${candidateId}/messages/preview`, {
    method: "POST",
    body: input,
  });
}

export async function sendCandidateMessage(
  candidateId: string,
  input: CandidateMessageSendInput,
): Promise<CandidateMessageSendResult> {
  return request<CandidateMessageSendResult>(`/candidates/${candidateId}/messages/send`, {
    method: "POST",
    body: input,
  });
}

export async function cancelCandidateMessage(
  candidateId: string,
  messageId: string,
): Promise<{ id: string; status: string }> {
  return request<{ id: string; status: string }>(
    `/candidates/${candidateId}/messages/${messageId}/cancel`,
    { method: "POST" },
  );
}

// Phase 11 uses the same authenticated, CSRF-protected, same-origin client.
export { request as documentRequest };

// --- Phase 12: local pilot first-run (Windows installer exchange) ------------

/** First-run screen data: surname/mode collected by the installer plus
 * readiness. The ticket stays in the URL fragment — never sent as a query
 * parameter. */
export async function previewOwnerSetup(ticket: string): Promise<SetupPreview> {
  return request<SetupPreview>("/setup/owner/preview", {
    method: "POST",
    body: { ticket },
  });
}

/** Complete the first run: creates the single pilot owner, applies
 * preferences and opens the authenticated session (cookies set by the
 * backend). */
export async function redeemOwnerSetup(input: RedeemOwnerInput): Promise<CurrentUser> {
  return request<CurrentUser>("/setup/owner/redeem", {
    method: "POST",
    body: input,
  });
}
