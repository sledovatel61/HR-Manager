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
  // Phase 12 (pilot first-run): the installer working mode is display
  // metadata only (NOT RBAC); the bootstrap marker drives the first-run UI.
  work_role?: "hr" | "manager" | "admin" | null;
  password_is_bootstrap?: boolean;
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
  candidate_id: string | null;
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

// --- Candidates database (PHASE 3) ------------------------------------------

/**
 * Funnel stages — single vocabulary from PRODUCT_SPEC §5, mirrored by the
 * backend `CandidateStage` enum. `started` («вышел») sits between `hired`
 * and `probation` (the design prototype does not include it yet).
 */
export type CandidateStage =
  | "new"
  | "contacted"
  | "reached"
  | "interview_scheduled"
  | "interview_done"
  | "offer"
  | "hired"
  | "started"
  | "probation"
  | "fired"
  | "rejected";

export const CANDIDATE_STAGE_ORDER: readonly CandidateStage[] = [
  "new",
  "contacted",
  "reached",
  "interview_scheduled",
  "interview_done",
  "offer",
  "hired",
  "started",
  "probation",
  "fired",
  "rejected",
];

export const STAGE_LABELS: Record<CandidateStage, string> = {
  new: "Новый",
  contacted: "Контакт",
  reached: "Дозвон",
  interview_scheduled: "Собеседование назначено",
  interview_done: "Собеседование проведено",
  offer: "Оффер",
  hired: "Оформлен",
  started: "Вышел",
  probation: "Испытательный срок",
  fired: "Уволен",
  rejected: "Отказ",
};

/** Semantic tone used to style stage chips without relying on color alone. */
export type StageTone =
  | "neutral"
  | "info"
  | "teal"
  | "violet"
  | "indigo"
  | "amber"
  | "success"
  | "danger";

export const STAGE_TONE: Record<CandidateStage, StageTone> = {
  new: "neutral",
  contacted: "info",
  reached: "teal",
  interview_scheduled: "violet",
  interview_done: "indigo",
  offer: "amber",
  hired: "success",
  started: "indigo",
  probation: "teal",
  fired: "danger",
  rejected: "neutral",
};

/** Candidate acquisition sources (closed set shared with the backend). */
export type CandidateSource =
  | "site"
  | "referral"
  | "hh_manual"
  | "university"
  | "event"
  | "agency"
  | "inbound_call";

export const SOURCE_LABELS: Record<CandidateSource, string> = {
  site: "Сайт компании",
  referral: "Рекомендация сотрудника",
  hh_manual: "Внешний портал (ручной ввод)",
  university: "Вуз / стажировка",
  event: "Карьерное мероприятие",
  agency: "Кадровое агентство",
  inbound_call: "Входящий звонок",
};

/** Interaction history entry types (transfer arrives in a later phase). */
export type CandidateInteractionType =
  | "call"
  | "email"
  | "meeting"
  | "note"
  | "status_change";

/** Candidate as returned by GET /candidates… (see backend CandidateOut). */
export interface Candidate {
  id: string;
  full_name: string;
  phone: string | null;
  email: string | null;
  source: CandidateSource;
  position: string;
  owner_user_id: string;
  owner_username: string;
  stage: CandidateStage;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  deleted_by_user_id: string | null;
  is_deleted: boolean;
}

/** One interaction history entry (see backend InteractionOut). */
export interface CandidateInteraction {
  id: string;
  candidate_id: string;
  author_user_id: string;
  author_username: string;
  type: CandidateInteractionType;
  comment: string;
  created_at: string;
}

/** GET /candidates query parameters (server-side search/filter/sort/page). */
export interface CandidateListQuery {
  query?: string;
  stage?: CandidateStage;
  source?: CandidateSource;
  owner_id?: string;
  /** Scope the listing to soft-deleted candidates (the trash view). */
  include_deleted?: boolean;
  sort?: "created_at" | "updated_at" | "full_name" | "stage";
  direction?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

/** POST /candidates payload (confirm_duplicate allows an exact copy on 409). */
export interface CandidateCreateInput {
  full_name: string;
  phone?: string | null;
  email?: string | null;
  source?: CandidateSource;
  position?: string;
  owner_user_id?: string;
  confirm_duplicate?: boolean;
}

/** PATCH /candidates/{id} payload — all fields optional. */
export type CandidateUpdateInput = Partial<
  Omit<CandidateCreateInput, "full_name"> & { full_name?: string }
> & { stage?: CandidateStage };

/** POST /candidates/{id}/interactions payload. */
export interface CandidateInteractionCreateInput {
  type: CandidateInteractionType;
  comment: string;
}

/** 409 duplicate response body (backend DuplicateCandidateDetail). */
export interface DuplicateCandidateDetail {
  message: string;
  duplicates: Candidate[];
}

// --- Phase 4: transfer history & HR directory ---------------------------------

/** Minimal safe user card for owner/HR pickers (backend UserListItem). */
export interface UserListItem {
  id: string;
  username: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
}

export interface UserListItems {
  items: UserListItem[];
  total: number;
}

/** One immutable ownership-transfer record (backend TransferOut). */
export interface CandidateTransfer {
  id: string;
  candidate_id: string;
  initiator_user_id: string;
  initiator_username: string;
  from_user_id: string;
  from_username: string;
  to_user_id: string;
  to_username: string;
  reason: string;
  created_at: string;
}

/** POST /candidates/{id}/transfer payload. */
export interface CandidateTransferInput {
  new_owner_user_id: string;
  reason: string;
}

/** POST /candidates/{id}/transfer response (record + refreshed candidate). */
export interface CandidateTransferResult {
  transfer: CandidateTransfer;
  candidate: Candidate;
}

// --- Phase 5: calendar events ------------------------------------------------

/** Closed event-type vocabulary (backend EventType). */
export type CalendarEventType = "call" | "interview" | "reminder";

export const EVENT_TYPE_LABELS: Record<CalendarEventType, string> = {
  call: "Звонок",
  interview: "Собеседование",
  reminder: "Напоминание",
};

/** Closed event-status vocabulary (backend EventStatus). */
export type CalendarEventStatus = "scheduled" | "completed" | "postponed";

export const EVENT_STATUS_LABELS: Record<CalendarEventStatus, string> = {
  scheduled: "Запланировано",
  completed: "Выполнено",
  postponed: "Отложено",
};

/** Immutable business-history kinds (backend EventHistoryKind). */
export type EventHistoryKind =
  | "created"
  | "updated"
  | "rescheduled"
  | "completed"
  | "postponed"
  | "assignee_changed";

export const EVENT_HISTORY_KIND_LABELS: Record<EventHistoryKind, string> = {
  created: "Создано",
  updated: "Изменено",
  rescheduled: "Перенесено",
  completed: "Выполнено",
  postponed: "Отложено",
  assignee_changed: "Смена исполнителя",
};

/** A calendar event (backend EventOut). All timestamps are UTC ISO 8601. */
export interface CalendarEvent {
  id: string;
  candidate_id: string;
  candidate_full_name: string;
  type: CalendarEventType;
  title: string;
  note: string | null;
  status: CalendarEventStatus;
  starts_at: string;
  ends_at: string | null;
  remind_at: string | null;
  completed_at: string | null;
  author_user_id: string;
  author_username: string;
  assignee_user_id: string;
  assignee_username: string;
  /** Optimistic-concurrency counter: PATCH must send the current value. */
  version: number;
  created_at: string;
  updated_at: string;
}

/** One immutable business-history entry (backend EventHistoryOut). */
export interface EventHistoryEntry {
  id: string;
  event_id: string;
  changed_by_user_id: string;
  changed_by_username: string;
  kind: EventHistoryKind;
  status_old: string | null;
  status_new: string | null;
  starts_at_old: string | null;
  starts_at_new: string | null;
  ends_at_old: string | null;
  ends_at_new: string | null;
  remind_at_old: string | null;
  remind_at_new: string | null;
  assignee_user_id_old: string | null;
  assignee_user_id_new: string | null;
  title_changed: boolean;
  note_changed: boolean;
  created_at: string;
}

/** GET /events query parameters (server-side filters). */
export interface EventListQuery {
  from?: string;
  to?: string;
  owner_id?: string;
  candidate_id?: string;
  type?: CalendarEventType;
  status?: CalendarEventStatus;
  /** Filter by the reminder moment (remind_at or reminder starts_at). */
  remind_from?: string;
  remind_to?: string;
  sort?: "starts_at" | "created_at" | "updated_at";
  direction?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

/** POST /events payload. */
export interface EventCreateInput {
  candidate_id: string;
  type: CalendarEventType;
  title: string;
  note?: string | null;
  starts_at: string;
  ends_at?: string | null;
  remind_at?: string | null;
  assignee_user_id?: string | null;
}

/** PATCH /events/{id} payload (expected_version is REQUIRED). */
export interface EventUpdateInput {
  expected_version: number;
  title?: string;
  note?: string | null;
  starts_at?: string;
  ends_at?: string | null;
  remind_at?: string | null;
  status?: CalendarEventStatus;
  assignee_user_id?: string | null;
}

// --- Analytics (analytics phase) ---------------------------------------------

/** Period presets; custom shows explicit from/to date inputs. */
export type AnalyticsPreset = "day" | "week" | "month" | "quarter" | "custom";

/** Views of the analytics section; period/filters survive switching. */
export type AnalyticsView = "kpi" | "funnel" | "breakdowns";

export const ANALYTICS_PRESET_LABELS: Record<AnalyticsPreset, string> = {
  day: "День",
  week: "Неделя",
  month: "Месяц",
  quarter: "Квартал",
  custom: "Произвольный",
};

export interface AnalyticsPeriod {
  from: string;
  to: string;
  timezone: string;
}

export interface AnalyticsFilters {
  hr_id: string | null;
  source: CandidateSource | null;
}

export interface AnalyticsKpis {
  created_candidates: number;
  processed_candidates: number;
  calls: number;
  reached: number;
  interviews_scheduled: number;
  interviews_done: number;
  offers: number;
  hired: number;
  dismissed: number;
  terminated: number;
}

export interface AnalyticsConversion {
  from_stage: string;
  to_stage: string;
  numerator: number;
  denominator: number;
  rate: number | null;
}

export interface AnalyticsSourceRow {
  source: CandidateSource;
  created: number;
  hired: number;
  dismissed: number;
  terminated: number;
}

export interface AnalyticsHrRow {
  hr_id: string;
  username: string;
  created: number;
  processed: number;
  hired: number;
  dismissed: number;
  terminated: number;
}

export interface AnalyticsKpiReport {
  period: AnalyticsPeriod;
  filters: AnalyticsFilters;
  scope: "team";
  kpis: AnalyticsKpis;
  conversions: AnalyticsConversion[];
  by_source: AnalyticsSourceRow[];
  by_hr: AnalyticsHrRow[];
}

export interface AnalyticsFunnelStage {
  stage: CandidateStage;
  reached: number;
}

export interface AnalyticsFunnelReport {
  period: AnalyticsPeriod;
  filters: AnalyticsFilters;
  stages: AnalyticsFunnelStage[];
  conversions: AnalyticsConversion[];
}

/** Query parameters shared by /analytics/kpi, /analytics/funnel, /analytics/export. */
export interface AnalyticsQuery {
  from: string;
  to: string;
  timezone?: string;
  hr_id?: string;
  source?: CandidateSource;
}

/** Russian labels of the ten KPIs (definition tooltips in KPI_DEFINITIONS). */
export const KPI_LABELS: Record<keyof AnalyticsKpis, string> = {
  created_candidates: "Создано кандидатов",
  processed_candidates: "В работе",
  calls: "Звонки",
  reached: "Дозвоны",
  interviews_scheduled: "Интервью назначено",
  interviews_done: "Интервью проведено",
  offers: "Офферы",
  hired: "Наймы",
  dismissed: "Отказы",
  terminated: "Увольнения",
};

/** Metric definitions shown as tooltips/screen-reader text (server-side fact
 * definitions — the UI never recomputes them). */
export const KPI_DEFINITIONS: Record<keyof AnalyticsKpis, string> = {
  created_candidates:
    "Уникальные кандидаты, созданные в периоде (включая позже удалённых — это исторический факт).",
  processed_candidates:
    "Уникальные кандидаты с деловой активностью в периоде: взаимодействие, смена этапа, передача или событие создано/завершено.",
  calls: "Записи взаимодействий типа «звонок», созданные в периоде.",
  reached: "Уникальные кандидаты, переведённые на этап «Дозвон» в периоде.",
  interviews_scheduled: "События-интервью, созданные в периоде (уникальные события).",
  interviews_done: "События-интервью, завершённые в периоде (уникальные события).",
  offers: "Уникальные кандидаты, впервые переведённые на этап «Оффер» в периоде.",
  hired: "Уникальные кандидаты, переведённые на этапы «Оформлен»/«Вышел» в периоде.",
  dismissed:
    "Уникальные кандидаты с переходом на этап «Отказ» в периоде (историческое событие, не текущий статус).",
  terminated:
    "Уникальные кандидаты с зарегистрированным увольнением (дата + причина) в периоде. Статус «Уволен» без даты не учитывается.",
};

// --- Phase 8: notifications, reminders, preferences, pilot setup -------------

export type NotificationPriority = "low" | "normal" | "high";
export type NotificationSource = "system" | "rule" | "reminder" | "manual";

/** One in-app notification (GET /notifications). */
export interface AppNotification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  priority: NotificationPriority;
  source: NotificationSource;
  object_type: string | null;
  object_id: string | null;
  created_at: string;
  read_at: string | null;
  dismissed_at: string | null;
}

export interface NotificationListPayload {
  items: AppNotification[];
  total: number;
  limit: number;
  offset: number;
  unread_count: number;
}

export interface NotificationResolve {
  allowed: boolean;
  object_type: string | null;
  object_id: string | null;
}

export interface DeliveryAttempt {
  attempt_no: number;
  started_at: string;
  finished_at: string;
  outcome: string;
  error_code: string | null;
  error_class: string | null;
}

export interface DeliveryInfo {
  id: string;
  channel: string;
  status: string;
  notification_type: string;
  scheduled_at: string | null;
  scheduled_at_effective: string | null;
  queued_at: string;
  delivered_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
  attempts: number;
  next_attempt_at: string | null;
  error_class: string | null;
  attempts_history: DeliveryAttempt[];
}

export type ReminderRecurrence = "none" | "daily" | "workdays" | "weekly";
export type ReminderStatus = "active" | "completed" | "cancelled";
export type ReminderImportance = "low" | "normal" | "high";

export interface Reminder {
  id: string;
  owner_user_id: string;
  owner_username: string;
  assignee_user_id: string;
  assignee_username: string;
  title: string;
  note: string | null;
  candidate_id: string | null;
  event_id: string | null;
  due_at: string;
  timezone: string;
  importance: ReminderImportance;
  recurrence: ReminderRecurrence;
  status: ReminderStatus;
  completed_at: string | null;
  occurrence: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface ReminderListPayload {
  items: Reminder[];
  total: number;
  limit: number;
  offset: number;
}

export interface NotificationPreferences {
  timezone: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
  workdays: number[];
  enabled_types: string[];
  enabled_channels: string[];
  initialized: boolean;
}

export interface QueueDiagnostics {
  counts: Record<string, number>;
  oldest_queued_at: string | null;
  stuck_sending: number;
  worker: { alive: boolean; last_seen_at?: string; processed_total?: number; failed_total?: number };
}

export interface SetupState {
  pilot_exists: boolean;
  pilot_grant_active: boolean;
  preferences_initialized: boolean;
  worker_alive: boolean;
  channels: Record<string, string>;
}

export interface AccessGrant {
  id: string;
  user_id: string;
  username: string;
  scope: string;
  granted_by_username: string | null;
  granted_at: string;
  revoked_at: string | null;
  revoke_reason: string | null;
}

// --- Phase 9: external integrations (Telegram/SMTP) ---------------------------

export type ChannelState =
  | "not_configured"
  | "pending"
  | "works"
  | "temporarily_unavailable"
  | "revoked";

export interface TelegramChannelStatus {
  state: ChannelState;
  configured: boolean;
  linked: boolean;
  masked_chat_id: string | null;
  pending_confirmation: boolean;
  opt_in: boolean;
  consent_at: string | null;
  linked_at: string | null;
}

export interface EmailChannelStatus {
  state: ChannelState;
  configured: boolean;
  verified: boolean;
  address_masked: string | null;
  pending_email_masked: string | null;
  pending_confirmation: boolean;
  opt_in: boolean;
  consent_at: string | null;
}

export interface IntegrationStatus {
  telegram: TelegramChannelStatus;
  email: EmailChannelStatus;
}

export interface TelegramLinkCode {
  deep_link: string;
  expires_at: string;
}

export interface TelegramConfirmResult {
  linked: boolean;
  state: ChannelState;
}

export interface ConsentResult {
  channel: string;
  opt_in: boolean;
  consent_granted: boolean;
  consent_at: string | null;
  policy_version: string | null;
}

export interface EmailSetResult {
  pending_email_masked: string;
  expires_at: string;
  verification_queued: boolean;
}

export interface EmailConfirmResult {
  verified: boolean;
  address_masked: string;
}

export interface AdminChannels {
  telegram: { enabled: boolean; configured: boolean; bot_username: string | null };
  smtp: {
    enabled: boolean;
    configured: boolean;
    host: string | null;
    port: number | null;
    encryption: string | null;
    from_address: string | null;
  };
}

export interface ChannelCheckResult {
  ok: boolean;
  detail: string;
  error_class: string | null;
  bot_username: string | null;
}

export interface ChannelTestResult {
  outbox_id: string;
  status: string;
}

// --- Phase 10: one-way candidate messages --------------------------------------

export type CandidateChannelState =
  | "not_connected"
  | "pending"
  | "allowed"
  | "forbidden"
  | "temporarily_unavailable";

export type CandidateChannelName = "email" | "telegram";

export interface CandidateConsent {
  channel: string;
  granted: boolean;
  granted_at: string | null;
  source: string | null;
  policy_version: string | null;
}

export interface CandidateChannelStatus {
  channel: CandidateChannelName;
  state: CandidateChannelState;
  configured: boolean;
  target_masked: string | null;
  has_target: boolean;
  invite_active: boolean;
  consent: CandidateConsent | null;
}

export interface CandidateChannels {
  email: CandidateChannelStatus;
  telegram: CandidateChannelStatus;
  allowed_channels: CandidateChannelName[];
}

export interface CandidateTelegramInvite {
  deep_link: string;
  expires_at: string;
}

export interface CandidateTelegramConfirm {
  linked: boolean;
  state: CandidateChannelState;
}

export type CandidateMessageType =
  | "interview_scheduled"
  | "interview_reminder"
  | "interview_rescheduled"
  | "interview_cancelled"
  | "document_request"
  | "document_reminder";

/** History-only kind: the double opt-in letter itself. It can never be
 * composed manually — the confirmation link is never rendered for the HR. */
export type CandidateHistoryOnlyMessageType = "candidate_email_confirm";

export interface CandidateMessageSendInput {
  message_type: CandidateMessageType;
  event_id?: string;
  documents?: string[];
  document_set_id?: string;
  channel?: CandidateChannelName;
  /** Client-generated, stable across retries of the same operation. */
  idempotency_key: string;
}

/** Preview shares the closed vocabulary but owns no idempotency key:
 * nothing is queued, so there is nothing to deduplicate. */
export type CandidateMessagePreviewInput = Omit<CandidateMessageSendInput, "idempotency_key">;

export interface CandidateMessagePreview {
  title: string;
  body: string;
  channels: CandidateChannelName[];
}

/** One immutable history entry. `accepted` means the provider took the
 * message — never «delivered», never «read». */
export interface CandidateMessage {
  document_context?: { list_id: string; version_id: string; version_number: number; rule_version: number | null } | null;
  rule_id?: string | null;
  template_version?: number | null;
  id: string;
  message_type: CandidateMessageType | CandidateHistoryOnlyMessageType;
  channel: CandidateChannelName;
  status: string;
  source: string;
  title: string;
  body: string | null;
  event_id: string | null;
  initiator_user_id: string | null;
  initiator_username: string | null;
  scheduled_at: string | null;
  scheduled_at_effective: string | null;
  queued_at: string;
  accepted_at: string | null;
  delivered_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
  attempts: number;
  next_attempt_at: string | null;
  error_code: string | null;
  error_class: string | null;
  provider_message_id: string | null;
}

export interface CandidateMessageList {
  items: CandidateMessage[];
  total: number;
  limit: number;
  offset: number;
}

export interface CandidateMessageSendResult {
  messages: CandidateMessage[];
  channels: CandidateChannelName[];
}

/** Initiation of the candidate's email double opt-in letter. */
export interface CandidateEmailConfirmation {
  queued: boolean;
  email_masked: string;
  expires_at: string;
}

// --- Phase 12: first-run pairing (local pilot) ------------------------------

/** Public first-run state (GET /setup/first-run/state, loopback only). */
export interface FirstRunState {
  pending: boolean;
  pending_work_role: "hr" | "manager" | "admin" | null;
  pending_expires_in_seconds: number | null;
  fresh_install: boolean;
  pilot_owner_exists: boolean;
}

/** POST /setup/first-run/claim result: session + owner, like a login. */
export interface FirstRunClaimResult extends CurrentUser {
  must_set_password: boolean;
}

/** GET /ops/status subset used by the first-run readiness checklist. */
export interface OpsReadinessStatus {
  status: string;
  release_sha: string;
  database: { status: string };
  migrations?: { ok: boolean };
  notifications?: { worker_alive: boolean };
  backup?: { available: boolean; ok?: boolean; age_seconds?: number | null };
}
