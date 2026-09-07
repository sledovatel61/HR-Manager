/**
 * Типы дизайн-прототипа HR Manager.
 *
 * ВАЖНО: это НЕ производственная модель данных и не API-контракт.
 * Формы объектов приближены к `PRODUCT_SPEC.md` и `docs/ARCHITECTURE.md`,
 * но упрощены и не обязаны совпадать с будущей backend-схемой этапов 2–6.
 * Все данные ниже — синтетические моки (см. `data/mockData.ts`).
 */

export type UserRole = "hr" | "manager" | "admin";

export const ROLE_LABELS: Record<UserRole, string> = {
  hr: "HR-менеджер",
  manager: "Руководитель",
  admin: "Администратор",
};

export interface AppUser {
  id: string;
  fullName: string;
  initials: string;
  username: string;
  email: string;
  role: UserRole;
  isActive: boolean;
  avatarColor: string;
  title: string;
}

export type CandidateStage =
  | "new"
  | "contacted"
  | "reached"
  | "interview_scheduled"
  | "interview_done"
  | "offer"
  | "hired"
  | "probation"
  | "fired"
  | "rejected";

export const STAGE_ORDER: CandidateStage[] = [
  "new",
  "contacted",
  "reached",
  "interview_scheduled",
  "interview_done",
  "offer",
  "hired",
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
  probation: "teal",
  fired: "danger",
  rejected: "neutral",
};

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

export interface Candidate {
  id: string;
  fullName: string;
  initials: string;
  avatarColor: string;
  position: string;
  department: string;
  city: string;
  phoneMasked: string;
  emailMasked: string;
  source: CandidateSource;
  stage: CandidateStage;
  ownerId: string;
  createdAt: string;
  lastActivityAt: string;
  nextEventAt: string | null;
  isDeleted: boolean;
  salaryExpectation: string;
  tags: string[];
  rating: number;
  notesPreview: string;
  duplicateOf?: string;
}

export type InteractionType = "call" | "email" | "note" | "status_change" | "transfer" | "meeting";

export interface Interaction {
  id: string;
  candidateId: string;
  type: InteractionType;
  authorId: string;
  createdAt: string;
  summary: string;
  detail?: string;
  fromStage?: CandidateStage;
  toStage?: CandidateStage;
  fromOwnerId?: string;
  toOwnerId?: string;
}

export type EventType = "call" | "interview" | "reminder" | "meeting";
export type EventStatus = "planned" | "done" | "postponed" | "canceled";

export interface CalendarEvent {
  id: string;
  candidateId: string;
  type: EventType;
  status: EventStatus;
  title: string;
  ownerId: string;
  startsAt: string;
  durationMinutes: number;
  location: string;
  note?: string;
}

export type AuditAction =
  | "login_success"
  | "login_failure"
  | "logout"
  | "account_locked"
  | "user_created"
  | "user_updated"
  | "role_changed"
  | "candidate_transferred"
  | "candidate_status_changed"
  | "candidate_exported";

export interface AuditEvent {
  id: string;
  action: AuditAction;
  actorName: string;
  targetName?: string;
  ipAddress: string;
  createdAt: string;
  details: string;
}

export interface VacancyRef {
  id: string;
  title: string;
  department: string;
  openSince: string;
  targetHires: number;
  hired: number;
}

export interface FunnelPoint {
  stage: CandidateStage;
  count: number;
}

export interface KpiSummary {
  newCandidates: number;
  processed: number;
  calls: number;
  reached: number;
  interviewsScheduled: number;
  interviewsDone: number;
  offers: number;
  hired: number;
  fired: number;
  conversionToHire: number;
}

export interface SavedView {
  id: string;
  name: string;
  description: string;
  isDefault?: boolean;
}
