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
  /** Phase 12: role chosen in the Windows installer (profile data only). */
  working_mode?: WorkingMode | null;
}

/** PATCH /admin/users/{user_id} payload: все поля необязательны (backend UserUpdate). */
export interface UserUpdateInput {
  full_name?: string;
  role?: UserRole;
  password?: string;
  is_active?: boolean;
}

export type WorkingMode = "hr" | "manager" | "admin";

export const WORKING_MODE_LABELS: Record<WorkingMode, string> = {
  hr: "HR",
  manager: "Руководитель",
  admin: "Администратор",
};

/** POST /setup/owner/preview payload (first-run screen data). */
export interface SetupPreview {
  surname: string;
  working_mode: WorkingMode;
  timezone: string;
  readiness: Record<string, string>;
  channels: Record<string, string>;
  full_access: boolean;
}

/** POST /setup/owner/redeem payload. */
export interface RedeemOwnerInput {
  ticket: string;
  timezone: string;
  workdays: number[];
  quiet_hours_start: string;
  quiet_hours_end: string;
  password: string;
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
  | "inbound_call"
  | "excel_import";

export const SOURCE_LABELS: Record<CandidateSource, string> = {
  site: "Сайт компании",
  referral: "Рекомендация сотрудника",
  hh_manual: "Внешний портал (ручной ввод)",
  university: "Вуз / стажировка",
  event: "Карьерное мероприятие",
  agency: "Кадровое агентство",
  inbound_call: "Входящий звонок",
  excel_import: "Импорт графика из Excel",
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
  /** GET /candidates only; optional for compatibility with older servers/detail. */
  attachment_count?: number;
  id: string;
  full_name: string;
  phone: string | null;
  email: string | null;
  source: CandidateSource;
  position: string;
  owner_user_id: string | null;
  owner_username: string | null;
  stage: CandidateStage;
  /** Phase 18: «Выход на работу» — дата/время и место выхода (ISO date/time). */
  start_date: string | null;
  start_time: string | null;
  start_organization: string | null;
  start_department: string | null;
  shift: string | null;
  start_comment: string | null;
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
  /** Free-text position (no vacancy directory exists — see backlog §7.1). */
  position?: string;
  owner_id?: string;
  /** Scope the listing to soft-deleted candidates (the trash view). */
  include_deleted?: boolean;
  sort?: "created_at" | "updated_at" | "full_name" | "stage";
  direction?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

/** Scope of the distinct-position directory (same rules as the list). */
export interface PositionOptionsQuery {
  /** Managers/admins may narrow the scope to one owner; HR is always own. */
  owner_id?: string;
  /** Switch to the soft-deleted view (the trash tab). */
  include_deleted?: boolean;
  /** Server-side ceiling on the number of options (default 500). */
  limit?: number;
}

/** One distinct free-text position with the number of candidates behind it. */
export interface CandidatePositionOption {
  position: string;
  count: number;
}

export interface CandidatePositionList {
  items: CandidatePositionOption[];
  /** Distinct positions in the scope, ignoring `limit`. */
  total: number;
  limit: number;
  /** True when fewer options than exist were returned. */
  truncated: boolean;
}

/** One funnel row of the «Моя очередь» summary (zero-filled when empty). */
export interface QueueStageCount {
  stage: CandidateStage;
  count: number;
}

/** Bounded card of «Требуют внимания» (a candidate without movement). */
export interface QueueStuckCandidate {
  id: string;
  full_name: string;
  position: string;
  stage: CandidateStage;
  updated_at: string;
}

/** Statuses a «Ближайшие события» card can carry: the server excludes the
 *  terminal ones (completed/cancelled), so only these two can arrive. */
export type QueueEventStatus = Extract<CalendarEventStatus, "scheduled" | "postponed">;

/** Bounded card of «Ближайшие события» (never completed/cancelled). */
export interface QueueUpcomingEvent {
  id: string;
  candidate_id: string;
  candidate_full_name: string;
  type: CalendarEventType;
  title: string;
  status: QueueEventStatus;
  starts_at: string;
  ends_at: string | null;
}

/** «Моя очередь»: aggregates over the whole personal scope + small samples. */
export interface QueueSummary {
  owner_id: string;
  owner_username: string;
  personal: boolean;
  generated_at: string;
  total: number;
  in_work: number;
  fresh: number;
  stuck: number;
  starts: number;
  stuck_days: number;
  horizon_days: number;
  /** Stages that mean «не в работе» (mirrors the server contract). */
  closed_stages: string[];
  by_stage: QueueStageCount[];
  stuck_sample: QueueStuckCandidate[];
  stuck_sample_truncated: boolean;
  upcoming_events: QueueUpcomingEvent[];
  upcoming_events_total: number;
  upcoming_events_truncated: boolean;
}

/** Presets of the «Моя очередь» dashboard (boundaries are computed by the
 *  server in `timezone`, so the browser never guesses a day boundary). */
export type QueueDashboardPeriodKey = "today" | "week" | "all";

/** Query of `GET /candidates/queue/dashboard`. */
export interface QueueDashboardQuery {
  period?: QueueDashboardPeriodKey;
  /** IANA timezone the period boundaries are computed in. */
  timezone?: string;
  /** Whose queue (manager/administrator only; HR is always pinned to self). */
  owner_id?: string;
  position?: string;
  stage?: CandidateStage;
  source?: CandidateSource;
}

/** One point of the shared bucket axis (half-open `[from, to)`). */
export interface QueueDashboardBucket {
  bucket: string;
  from: string;
  to: string;
  label: string;
}

/** The resolved period: the server computes it, the browser only renders it. */
export interface QueueDashboardPeriod {
  key: string;
  from: string;
  to: string;
  timezone: string;
  bucket_size: "hour" | "day" | "month" | string;
  /** «Всё» axis cut at 24 months: the UI must say so out loud. */
  capped: boolean;
  buckets: QueueDashboardBucket[];
}

/** A conversion: `rate` is `null` (not 0) when the cohort is empty. */
export interface QueueRatio {
  numerator: number;
  denominator: number;
  rate: number | null;
}

/** An average in days plus the sample it was computed from. */
export interface QueueDuration {
  value: number | null;
  sample: number;
}

export interface QueueSeriesValue {
  bucket: string;
  value: number;
}

export interface QueueSeriesRatio {
  bucket: string;
  numerator: number;
  denominator: number;
  rate: number | null;
}

export interface QueueSeriesDuration {
  bucket: string;
  value: number | null;
  sample: number;
}

/** «Динамика найма»: exits per bucket plus the hiring-time line. */
export interface QueueHiringPoint {
  bucket: string;
  exits: number;
  hired: number;
  avg_hiring_days: number | null;
}

export interface QueueSourceCount {
  source: string;
  label: string;
  count: number;
}

/** A task of «Моя очередь»: the project has no Task entity, so a task is an
 *  active reminder assigned to the caller. */
export interface QueueDashboardTask {
  id: string;
  title: string;
  due_at: string;
  importance: string;
  status: string;
  candidate_id: string | null;
  event_id: string | null;
}

export interface QueueDashboardScope {
  owner_id: string;
  owner_username: string;
  personal: boolean;
  role: string;
}

/** The KPI row. `active_vacancies` is `null` until the project gets a vacancy
 *  entity — the UI shows «—» with `kpi_notes.active_vacancies`, never 0. */
export interface QueueDashboardKpis {
  total_candidates: number;
  in_work: number;
  my_tasks: number;
  overdue_tasks: number;
  new_candidates: number;
  interviews: number;
  interview_conversion: QueueRatio;
  average_hiring_days: QueueDuration;
  active_vacancies: number | null;
  weekly_exits: number;
}

/** `GET /candidates/queue/dashboard` — every number of the screen, aggregated
 *  server-side over the caller's whole scope (never over a page). */
export interface QueueDashboard {
  scope: QueueDashboardScope;
  period: QueueDashboardPeriod;
  generated_at: string;
  filters: Record<string, string | null>;
  kpis: QueueDashboardKpis;
  /** Why a KPI is `null` (there is no vacancy entity in the project). */
  kpi_notes: Record<string, string>;
  created_candidates_series: QueueSeriesValue[];
  interview_conversion_series: QueueSeriesRatio[];
  average_hiring_days_series: QueueSeriesDuration[];
  hiring_dynamics: QueueHiringPoint[];
  sources: QueueSourceCount[];
  funnel: QueueStageCount[];
  attention_candidates: QueueStuckCandidate[];
  attention_candidates_total: number;
  attention_truncated: boolean;
  upcoming_events: QueueUpcomingEvent[];
  upcoming_events_total: number;
  upcoming_events_truncated: boolean;
  tasks_due: QueueDashboardTask[];
  tasks_overdue: QueueDashboardTask[];
  unread_notifications: number;
  truncated: boolean;
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

/** PATCH /candidates/{id} payload — all fields optional.
 *
 * Phase 18: `null` в полях графика означает «очистить значение» (сервер
 * различает отсутствующее поле и явный null). */
export type CandidateUpdateInput = Partial<
  Omit<CandidateCreateInput, "full_name"> & { full_name?: string }
> & {
  stage?: CandidateStage;
  start_date?: string | null;
  start_time?: string | null;
  start_organization?: string | null;
  start_department?: string | null;
  shift?: string | null;
  start_comment?: string | null;
};

/** Этапы, на которых разрешено назначать дату выхода (правило сервера). */
export const START_STAGES: readonly CandidateStage[] = ["offer", "hired", "started"];

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
  /**
   * Display name of the linked candidate (block B). Convenience only — the
   * server still enforces every access check.
   */
  candidate_full_name: string | null;
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

/** Phase 13: Windows pilot update channel status (never URLs/paths/secrets). */
export interface UpdateStatus {
  state: string;
  installed_version: string;
  installed_release_sha: string;
  available_version: string | null;
  available_release_sha: string | null;
  available_published_at: string | null;
  notes_ru: string | null;
  download_progress: number | null;
  last_check_at: string | null;
  last_check_ok: boolean | null;
  error_code: string | null;
  last_result: string | null;
  channel_configured: boolean;
}

export interface UpdateInstallResult {
  state: string;
  job_id: string | null;
  message: string | null;
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

/** Phase 14: состояние одной серверной проверки готовности пилота. */
export type PilotReadinessState = "pass" | "warning" | "fail";

export interface PilotReadinessCheck {
  code: string;
  title: string;
  state: PilotReadinessState;
  detail: string;
  action: string;
  evidence: Record<string, unknown> | null;
}

/** Phase 14: read-only отчёт «Проверить готовность пилота» (admin + scope). */
export interface PilotReadiness {
  generated_at: string;
  verdict: "готово" | "готово с предупреждениями" | "запуск запрещён";
  counts: { pass: number; warning: number; fail: number };
  host_evidence_age_seconds: number | null;
  host_evidence_fresh: boolean;
  server_version: string;
  checks: PilotReadinessCheck[];
}

/** License — offline pilot license status */
export interface LicenseInfo {
  license_id: string;
  client_name: string;
  issued_at: string;
  expires_at: string;
  max_active_users: number;
  active_users?: number;
  days_left?: number | null;
  last_seen_at?: string | null;
}

export interface LicenseStatus {
  enforcement: "enabled" | "disabled" | string;
  has_license: boolean;
  is_valid: boolean;
  license?: LicenseInfo;
  active_users?: number;
  max_active_users?: number | null;
  public_key_fingerprint?: string | null;
  code?: string;
  message?: string;
  note?: string;
}

/** Phase 16: versioned document templates (textual MVP — no files). */
export type TemplateVersionState = "draft" | "active" | "archived";

/** One immutable version of a template. Content never changes after saving. */
export interface DocumentTemplateVersion {
  id: string;
  template_id: string;
  number: number;
  state: TemplateVersionState;
  title: string;
  body: string;
  /** Placeholder tokens used by this version (allowlist only). */
  placeholders: string[];
  author_id: string;
  created_at: string;
  activated_at: string | null;
}

export interface DocumentTemplate {
  id: string;
  /** Controlled kind key (`offer`, `anketa`, `dogovor`, …), not a free label. */
  kind: string;
  /** Empty string = available for every stage, otherwise a CandidateStage. */
  scope: string;
  name: string;
  /** Library category key (closed dictionary) — «Библиотека HR» filter. */
  category: string;
  /** One-sentence purpose shown on the library card. */
  summary: string;
  /** Optimistic counter; every rename or new version bumps it. */
  revision: number;
  author_id: string;
  created_at: string;
  updated_at: string;
  versions: DocumentTemplateVersion[];
}

export interface DocumentTemplates {
  items: DocumentTemplate[];
  /** False for HR without the document_lists_manage grant. */
  can_manage: boolean;
}

/** One typed placeholder from the server-side allowlist. */
export interface TemplatePlaceholder {
  token: string;
  description: string;
}

/** Structured 409 payload of a re-import hitting an existing material. */
export interface ImportDuplicateDetail {
  message: string;
  existing: {
    id: string;
    name: string;
    kind: string;
    /** Raw funnel stage key ("" = вся база) — part of the material identity. */
    scope: string;
    revision: number;
  };
}

// --- «Библиотека HR»: read-only material screens ------------------------------

/** Closed-dictionary category key; the empty string means uncategorized. */
export type LibraryCategoryKey =
  | ""
  | "interview"
  | "candidate_docs"
  | "calls"
  | "onboarding"
  | "memos"
  | "position";

export interface LibraryCategory {
  key: string;
  label: string;
}

/** Card of the library main screen (no content — only card fields). */
export interface LibraryMaterial {
  id: string;
  name: string;
  kind: string;
  category: string;
  summary: string;
  scope: string;
  version_id: string;
  version_number: number;
  title: string;
  published_at: string | null;
  has_placeholders: boolean;
}

export interface LibraryMaterials {
  items: LibraryMaterial[];
  categories: LibraryCategory[];
  /** False for HR without the document_lists_manage grant. */
  can_manage: boolean;
}

/** Human words for one placeholder token used by a material. */
export interface LibraryPlaceholderHint {
  token: string;
  hint: string;
}

/** Read-only view of one published material with a safe demo rendering. */
export interface LibraryMaterialDetail extends LibraryMaterial {
  body: string;
  body_html: string;
  body_text: string;
  placeholders: string[];
  placeholder_hints: LibraryPlaceholderHint[];
  updated_at: string;
}

export interface TemplateVersionInput {
  title: string;
  body: string;
}

/** Immutable snapshot of a document rendered for a candidate. */
export interface GeneratedDocument {
  id: string;
  candidate_id: string;
  template_id: string;
  template_version_id: string;
  template_number: number;
  template_name: string;
  template_title: string;
  kind: string;
  revision: number;
  body_text: string;
  content_sha256: string;
  created_by: string;
  created_at: string;
}

export interface GeneratedDocumentPreview extends GeneratedDocument {
  body_html: string;
}

export interface GeneratedDocumentPage {
  items: GeneratedDocument[];
  total: number;
  limit: number;
  offset: number;
}

/** Render result before saving (nothing is stored by a preview). */
export interface DocumentRenderPreview {
  template_id: string;
  template_version_id: string;
  template_number: number;
  template_name: string;
  kind: string;
  title: string;
  body_text: string;
  body_html: string;
  placeholders: string[];
}

export type GeneratedDocumentFormat = "html" | "txt";

// --- Phase 18: «График выхода на работу» -------------------------------------

export type ScheduleRowKind = "candidate" | "entry";
export type ScheduleRowStatus = "planned" | "started" | "not_came" | "dismissed";

/** Одна строка дневного блока графика (ответ GET /work-schedule). */
export interface WorkScheduleRow {
  kind: ScheduleRowKind;
  /** Кандидат или служебная строка — используется для правки строки. */
  id: string;
  candidate_id: string | null;
  entry_date: string;
  /** Номер строки внутри дня (1..N) — как в колонке «№» образца. */
  number: number;
  start_time: string | null;
  end_time: string | null;
  full_name: string | null;
  display_name: string;
  organization: string | null;
  department: string | null;
  position: string;
  shift: string | null;
  comment: string | null;
  owner_user_id: string | null;
  owner_username: string | null;
  stage: CandidateStage | null;
  status: ScheduleRowStatus;
  status_label: string;
}

export interface WorkScheduleList {
  items: WorkScheduleRow[];
  total: number;
  days: number;
  period_from: string | null;
  period_to: string | null;
  include_rejected: boolean;
}

/** GET /work-schedule query parameters (те же фильтры у export.xlsx). */
export interface WorkScheduleQuery {
  from?: string;
  to?: string;
  organization?: string;
  department?: string;
  position?: string;
  shift?: string;
  owner?: string;
  stage?: CandidateStage;
  q?: string;
  include_rejected?: boolean;
}

export interface WorkScheduleSuggestions {
  organizations: string[];
  departments: string[];
  shifts: string[];
}

/** Служебная строка графика без кандидата. */
export interface ScheduleEntry {
  id: string;
  entry_date: string;
  time_from: string | null;
  time_to: string | null;
  title: string;
  organization: string | null;
  department: string | null;
  comment: string | null;
  author_user_id: string | null;
  author_username: string | null;
  created_at: string;
  updated_at: string;
}

export interface ScheduleEntryCreateInput {
  entry_date: string;
  time_from?: string | null;
  time_to?: string | null;
  title: string;
  organization?: string | null;
  department?: string | null;
  comment?: string | null;
}

export type ScheduleEntryUpdateInput = Partial<ScheduleEntryCreateInput>;

// --- Импорт графика выхода из Excel ------------------------------------------

export type ImportRowKind = "candidate" | "service" | "skip";
export type ImportSourceRowType = "person" | "service" | "skip" | "error";
export type ImportSyncStatus = "added" | "updated" | "unchanged" | "missing";
export type ImportSuggestedAction = "create" | "match" | "service" | "skip";
export type ImportDecisionAction = "create" | "match" | "service" | "skip";

/** Кандидат из картотеки, найденный сервером для строки импорта. */
export interface ImportMatchInfo {
  candidate_id: string;
  full_name: string;
  stage: CandidateStage;
  /** exact_name | phone | partial — причина совпадения. */
  reason: string;
  /** Совпадение по телефону надёжнее, чем только по ФИО. */
  confident: boolean;
}

/** Одна строка в превью импорта. */
export interface ImportRowPreview {
  row_index: number;
  sheet_row: number;
  entry_date: string | null;
  full_name: string | null;
  time_display: string;
  time_from: string | null;
  time_to: string | null;
  organization: string | null;
  department: string | null;
  position: string | null;
  shift: string | null;
  comment: string | null;
  /** Маскированный телефон («+7 ••• •••-••-29») — только для сверки. */
  phone_masked: string | null;
  kind: ImportRowKind;
  row_type: ImportSourceRowType;
  name_confidence: "full" | "partial" | null;
  suggested_action: ImportSuggestedAction;
  match: ImportMatchInfo | null;
  match_options: ImportMatchInfo[];
  candidate_id: string | null;
  source_row_key: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  schedule_ready: boolean;
  already_imported: boolean;
  warnings: string[];
  parse_error: string | null;
}

export interface ImportPreviewSummary {
  rows_total: number;
  days_total: number;
  candidate_rows: number;
  service_rows: number;
  skipped_rows: number;
  error_rows: number;
  new_count: number;
  match_count: number;
  ambiguous_count: number;
}

/** Ответ «проверить без сохранения» (превью). */
export interface WorkScheduleImportPreview {
  file_name: string;
  file_sha256: string;
  sheet_title: string;
  days: string[];
  warnings: string[];
  rows: ImportRowPreview[];
  summary: ImportPreviewSummary;
}

/** Явное действие пользователя по одной строке (подтверждение). */
export interface ImportRowDecision {
  row_index: number;
  action: ImportDecisionAction;
  candidate_id?: string | null;
}

export interface ImportRowResult {
  row_index: number;
  sheet_row: number;
  entry_date: string | null;
  time_display: string;
  action_label: string;
  result: "created" | "matched" | "updated" | "service" | "skipped" | "error";
  candidate_id: string | null;
  entry_id: string | null;
  reason: string | null;
  sync_status?: ImportSyncStatus | null;
}

/** Итог подтверждённого импорта + санитизированный CSV-отчёт. */
export interface WorkScheduleImportResult {
  import_id: string;
  created: number;
  matched: number;
  updated: number;
  service_created: number;
  skipped: number;
  errors: number;
  rows_added: number;
  rows_updated: number;
  rows_unchanged: number;
  rows_missing: number;
  active_people: number;
  rows: ImportRowResult[];
  report_csv: string;
}

export interface ActiveScheduleImportRow {
  row_key: string;
  row_order: number;
  sheet_row: number;
  row_type: ImportSourceRowType;
  full_name: string | null;
  entry_date: string | null;
  time_from: string | null;
  time_to: string | null;
  organization: string | null;
  department: string | null;
  position: string | null;
  candidate_id: string | null;
  owner_user_id: string | null;
  owner_name: string | null;
  schedule_ready: boolean;
  sync_status: ImportSyncStatus;
}

export interface ActiveScheduleImportRows {
  import_id: string | null;
  file_name: string | null;
  imported_at: string | null;
  active_people: number;
  can_assign: boolean;
  rows: ActiveScheduleImportRow[];
}

export interface ScheduleImportAssignmentInput {
  row_keys: string[];
  owner_user_id: string | null;
}

export interface ScheduleImportAssignmentResult {
  updated: number;
  owner_user_id: string | null;
  owner_name: string | null;
}

// --- Вложения кандидата: анкеты (.docx) и сканы (.pdf) -----------------------

/** Формат вложения. Сервер определяет его по сигнатуре, не по имени файла. */
export type AttachmentKind = "docx" | "pdf";

/** Метаданные одного вложения. Байты приходят только отдельным скачиванием. */
export interface CandidateAttachment {
  id: string;
  candidate_id: string;
  filename: string;
  kind: AttachmentKind;
  size_bytes: number;
  sha256: string;
  uploaded_by_user_id: string | null;
  uploaded_by_username: string;
  uploaded_at: string;
}

/** Действующие лимиты: UI предупреждает о превышении до отправки файла. */
export interface AttachmentLimits {
  max_file_bytes: number;
  max_total_bytes: number;
  max_count: number;
}

export interface CandidateAttachmentList {
  items: CandidateAttachment[];
  total: number;
  total_bytes: number;
  limits: AttachmentLimits;
  /** Право загружать/удалять вложения этого кандидата — решение сервера. */
  can_manage: boolean;
}
