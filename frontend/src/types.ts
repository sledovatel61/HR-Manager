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
