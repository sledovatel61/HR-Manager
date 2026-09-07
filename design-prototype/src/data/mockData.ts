/**
 * ПОЛНОСТЬЮ СИНТЕТИЧЕСКИЕ моковые данные для дизайн-прототипа HR Manager.
 *
 * Требования, соблюдённые намеренно:
 * - все ФИО вымышленные (не привязаны к реальным людям);
 * - телефоны используют заведомо нерабочий формат +7 900 000-XX-XX;
 * - email только на example.com / example.org / example.net;
 * - названия компаний-источников вымышленные;
 * - никаких внешних URL, аватары — инициалы + цвет (см. Avatar.tsx).
 *
 * Файл детерминированный (без Math.random) — так прототип и accessibility-
 * аудит воспроизводимы между запусками и снапшот-тестами (если появятся).
 */

import type {
  AppUser,
  AuditEvent,
  Candidate,
  CalendarEvent,
  FunnelPoint,
  Interaction,
  KpiSummary,
  SavedView,
  VacancyRef,
} from "../types";
import { STAGE_ORDER } from "../types";

export const CURRENT_DATE = new Date("2026-09-02T09:00:00+03:00");

function iso(daysOffset: number, hour = 10, minute = 0): string {
  const d = new Date(CURRENT_DATE);
  d.setDate(d.getDate() + daysOffset);
  d.setHours(hour, minute, 0, 0);
  return d.toISOString();
}

export const USERS: AppUser[] = [
  {
    id: "u-anna",
    fullName: "Анна Смирнова",
    initials: "АС",
    username: "a.smirnova",
    email: "a.smirnova@example.com",
    role: "hr",
    isActive: true,
    avatarColor: "violet",
    title: "HR-менеджер · подбор разработчиков",
  },
  {
    id: "u-oleg",
    fullName: "Олег Ткаченко",
    initials: "ОТ",
    username: "o.tkachenko",
    email: "o.tkachenko@example.com",
    role: "hr",
    isActive: true,
    avatarColor: "teal",
    title: "HR-менеджер · подбор продаж",
  },
  {
    id: "u-marina",
    fullName: "Марина Ковалёва",
    initials: "МК",
    username: "m.kovaleva",
    email: "m.kovaleva@example.com",
    role: "hr",
    isActive: true,
    avatarColor: "amber",
    title: "HR-менеджер · массовый подбор",
  },
  {
    id: "u-igor",
    fullName: "Игорь Белов",
    initials: "ИБ",
    username: "i.belov",
    email: "i.belov@example.com",
    role: "manager",
    isActive: true,
    avatarColor: "indigo",
    title: "Руководитель отдела подбора",
  },
  {
    id: "u-elena",
    fullName: "Елена Гурьева",
    initials: "ЕГ",
    username: "e.gurieva",
    email: "e.gurieva@example.com",
    role: "admin",
    isActive: true,
    avatarColor: "slate",
    title: "Администратор системы",
  },
];

export const CURRENT_USER = USERS[0]; // Анна Смирнова, HR — по умолчанию для прототипа входа.

export const VACANCIES: VacancyRef[] = [
  { id: "v-1", title: "Backend-разработчик (Python)", department: "Инженерия", openSince: iso(-40), targetHires: 2, hired: 1 },
  { id: "v-2", title: "Менеджер по продажам B2B", department: "Продажи", openSince: iso(-30), targetHires: 4, hired: 2 },
  { id: "v-3", title: "Продуктовый дизайнер", department: "Продукт", openSince: iso(-20), targetHires: 1, hired: 0 },
  { id: "v-4", title: "Специалист поддержки", department: "Клиентский сервис", openSince: iso(-60), targetHires: 3, hired: 3 },
  { id: "v-5", title: "Аналитик данных", department: "Инженерия", openSince: iso(-15), targetHires: 1, hired: 0 },
  { id: "v-6", title: "Рекрутёр (стажёр)", department: "HR", openSince: iso(-10), targetHires: 1, hired: 0 },
];

const FIRST_NAMES_F = ["Мария", "Ольга", "Наталья", "Екатерина", "Юлия", "Виктория", "Дарья", "Полина", "Светлана", "Алина", "Ксения", "Татьяна"];
const FIRST_NAMES_M = ["Дмитрий", "Сергей", "Александр", "Кирилл", "Артём", "Никита", "Роман", "Максим", "Павел", "Виталий", "Егор", "Тимур", "Денис"];
const LAST_NAMES = [
  "Волков", "Морозов", "Соколов", "Лебедев", "Новиков", "Фёдоров", "Егоров", "Павлов",
  "Козлов", "Степанов", "Николаев", "Орлов", "Андреев", "Макаров", "Никитин",
  "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев", "Романов", "Воробьёв",
];
const CITIES = ["Москва", "Санкт-Петербург", "Казань", "Новосибирск", "Екатеринбург", "Нижний Новгород", "Ростов-на-Дону"];
const POSITIONS = [
  "Backend-разработчик (Python)",
  "Менеджер по продажам B2B",
  "Продуктовый дизайнер",
  "Специалист поддержки",
  "Аналитик данных",
  "Frontend-разработчик (React)",
  "Рекрутёр (стажёр)",
  "QA-инженер",
];
const DEPARTMENTS: Record<string, string> = {
  "Backend-разработчик (Python)": "Инженерия",
  "Frontend-разработчик (React)": "Инженерия",
  "QA-инженер": "Инженерия",
  "Менеджер по продажам B2B": "Продажи",
  "Продуктовый дизайнер": "Продукт",
  "Специалист поддержки": "Клиентский сервис",
  "Аналитик данных": "Инженерия",
  "Рекрутёр (стажёр)": "HR",
};
const SOURCES: Candidate["source"][] = ["site", "referral", "hh_manual", "university", "event", "agency", "inbound_call"];
const AVATAR_COLORS = ["violet", "teal", "amber", "indigo", "rose", "slate", "cyan"];
const TAGS_POOL = ["Срочная вакансия", "Готов к переезду", "Удалённо", "Повторный отклик", "Реферал", "Английский B2+"];

function pick<T>(arr: T[], i: number): T {
  return arr[i % arr.length];
}

/** Deterministic pseudo-hash for stable "random-looking" but reproducible picks. */
function seed(i: number, salt: number): number {
  return (i * 2654435761 + salt) >>> 0;
}

export const CANDIDATES: Candidate[] = Array.from({ length: 32 }, (_, i) => {
  const isFemale = i % 2 === 0;
  const first = isFemale ? pick(FIRST_NAMES_F, i) : pick(FIRST_NAMES_M, i);
  const last = pick(LAST_NAMES, seed(i, 7));
  const lastFinal = isFemale && last.endsWith("ов") ? `${last}а` : isFemale && last.endsWith("ев") ? `${last}а` : isFemale && last.endsWith("ин") ? `${last}а` : last;
  const fullName = `${lastFinal} ${first}`;
  const initials = `${lastFinal[0]}${first[0]}`;
  const position = pick(POSITIONS, i);
  const stage = pick(STAGE_ORDER, seed(i, 13) % STAGE_ORDER.length);
  const owner = pick(USERS.filter((u) => u.role === "hr"), seed(i, 3));
  const createdOffset = -(3 + (seed(i, 11) % 55));
  const activityOffset = createdOffset + (seed(i, 17) % Math.max(1, Math.abs(createdOffset)));
  const hasUpcoming = seed(i, 19) % 3 !== 0;
  const nextOffset = hasUpcoming ? (seed(i, 23) % 9) - 2 : null;
  const tagCount = seed(i, 29) % 3;
  const tags = Array.from({ length: tagCount }, (_, t) => pick(TAGS_POOL, seed(i, 31 + t)));

  return {
    id: `c-${i + 1}`,
    fullName,
    initials,
    avatarColor: pick(AVATAR_COLORS, i),
    position,
    department: DEPARTMENTS[position] ?? "Инженерия",
    city: pick(CITIES, seed(i, 5)),
    phoneMasked: `+7 900 000-${String(10 + (i % 80)).padStart(2, "0")}-${String(10 + ((i * 3) % 80)).padStart(2, "0")}`,
    emailMasked: `candidate${i + 1}@example.${i % 3 === 0 ? "com" : i % 3 === 1 ? "org" : "net"}`,
    source: pick(SOURCES, seed(i, 41)),
    stage,
    ownerId: owner.id,
    createdAt: iso(createdOffset, 9 + (i % 6), (i * 7) % 60),
    lastActivityAt: iso(activityOffset, 10 + (i % 5), (i * 11) % 60),
    nextEventAt: nextOffset === null ? null : iso(nextOffset, 11 + (i % 4), (i * 13) % 60),
    isDeleted: i === 30, // one soft-deleted candidate to demonstrate the state
    salaryExpectation: `${90 + (i % 8) * 15} 000 ₽`,
    tags,
    rating: 1 + (seed(i, 47) % 5),
    notesPreview:
      stage === "rejected"
        ? "Отказ на этапе собеседования: не хватает опыта работы с высоконагруженными системами."
        : "Готов к разговору в будни после 15:00, ждём обратную связь по тестовому заданию.",
    duplicateOf: i === 31 ? "c-1" : undefined,
  };
});

export function candidateById(id: string): Candidate | undefined {
  return CANDIDATES.find((c) => c.id === id);
}

export function userById(id: string): AppUser | undefined {
  return USERS.find((u) => u.id === id);
}

const INTERACTION_TEMPLATES: Array<{ type: Interaction["type"]; summary: string; detail?: string }> = [
  { type: "call", summary: "Первичный звонок", detail: "Обсудили условия, кандидат заинтересован, ждёт вакансию письмом." },
  { type: "email", summary: "Отправлено тестовое задание", detail: "Срок выполнения — 5 рабочих дней." },
  { type: "note", summary: "Комментарий рекрутера", detail: "Кандидат просил перенести собеседование на вечер." },
  { type: "meeting", summary: "Собеседование с руководителем отдела" },
  { type: "status_change", summary: "Статус изменён" },
  { type: "transfer", summary: "Кандидат передан другому HR" },
];

export const INTERACTIONS: Interaction[] = CANDIDATES.filter((c) => !c.isDeleted).flatMap((c, ci) => {
  const count = 2 + (seed(ci, 53) % 4);
  return Array.from({ length: count }, (_, k) => {
    const tpl = pick(INTERACTION_TEMPLATES, seed(ci, 59 + k));
    const dayOffset = -(k * 3 + 1);
    return {
      id: `i-${c.id}-${k}`,
      candidateId: c.id,
      type: tpl.type,
      authorId: c.ownerId,
      createdAt: iso(dayOffset, 9 + k, (ci * 5 + k) % 60),
      summary: tpl.summary,
      detail: tpl.detail,
      fromStage: tpl.type === "status_change" ? STAGE_ORDER[Math.max(0, (seed(ci, k) % STAGE_ORDER.length) - 1)] : undefined,
      toStage: tpl.type === "status_change" ? c.stage : undefined,
      fromOwnerId: tpl.type === "transfer" ? "u-marina" : undefined,
      toOwnerId: tpl.type === "transfer" ? c.ownerId : undefined,
    } satisfies Interaction;
  });
});

export function interactionsForCandidate(id: string): Interaction[] {
  return INTERACTIONS.filter((i) => i.candidateId === id).sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
}

const EVENT_TITLES: Record<CalendarEvent["type"], string[]> = {
  call: ["Повторный звонок", "Уточняющий звонок", "Звонок-напоминание"],
  interview: ["Собеседование с HR", "Техническое собеседование", "Финальное собеседование"],
  reminder: ["Напомнить о тестовом задании", "Проверить фидбэк от руководителя", "Уточнить дату выхода"],
  meeting: ["Встреча с руководителем отдела", "Синхронизация по вакансии"],
};

export const EVENTS: CalendarEvent[] = CANDIDATES.filter((c) => !c.isDeleted && c.nextEventAt).map((c, i) => {
  const type = pick<CalendarEvent["type"]>(["call", "interview", "reminder", "meeting"], seed(i, 71));
  const status: CalendarEvent["status"] = i % 11 === 0 ? "postponed" : i % 7 === 0 ? "done" : "planned";
  return {
    id: `e-${c.id}`,
    candidateId: c.id,
    type,
    status,
    title: pick(EVENT_TITLES[type], seed(i, 73)),
    ownerId: c.ownerId,
    startsAt: c.nextEventAt as string,
    durationMinutes: pick([30, 45, 60], seed(i, 79)),
    location: type === "interview" ? "Переговорная «Полёт», 4 этаж" : "Телефон",
    note: type === "reminder" ? "Проверить, пришёл ли ответ от кандидата." : undefined,
  } satisfies CalendarEvent;
});

export function eventsForCandidate(id: string): CalendarEvent[] {
  return EVENTS.filter((e) => e.candidateId === id);
}

export const SAVED_VIEWS: SavedView[] = [
  { id: "sv-my-active", name: "Моя очередь: активные", description: "Кандидаты в работе без учёта отказов и увольнений", isDefault: true },
  { id: "sv-urgent", name: "Срочные вакансии", description: "Кандидаты по вакансиям с меткой «Срочная вакансия»" },
  { id: "sv-no-activity-7d", name: "Без активности 7+ дней", description: "Риск «зависших» кандидатов — требуют звонка" },
  { id: "sv-interviews-week", name: "Собеседования на этой неделе", description: "Все назначенные и проведённые интервью" },
];

export const FUNNEL: FunnelPoint[] = [
  { stage: "new", count: 148 },
  { stage: "contacted", count: 121 },
  { stage: "reached", count: 96 },
  { stage: "interview_scheduled", count: 58 },
  { stage: "interview_done", count: 41 },
  { stage: "offer", count: 22 },
  { stage: "hired", count: 17 },
];

export const KPI_TEAM: KpiSummary = {
  newCandidates: 148,
  processed: 132,
  calls: 410,
  reached: 289,
  interviewsScheduled: 58,
  interviewsDone: 41,
  offers: 22,
  hired: 17,
  fired: 3,
  conversionToHire: 11.5,
};

export const KPI_PERSONAL: Record<string, KpiSummary> = {
  "u-anna": { newCandidates: 41, processed: 38, calls: 132, reached: 96, interviewsScheduled: 19, interviewsDone: 14, offers: 8, hired: 6, fired: 1, conversionToHire: 14.6 },
  "u-oleg": { newCandidates: 55, processed: 49, calls: 168, reached: 108, interviewsScheduled: 21, interviewsDone: 15, offers: 7, hired: 5, fired: 1, conversionToHire: 9.1 },
  "u-marina": { newCandidates: 52, processed: 45, calls: 110, reached: 85, interviewsScheduled: 18, interviewsDone: 12, offers: 7, hired: 6, fired: 1, conversionToHire: 11.5 },
};

const AUDIT_TEMPLATES: Array<{ action: AuditEvent["action"]; details: string }> = [
  { action: "login_success", details: "Успешный вход в систему" },
  { action: "login_failure", details: "Неверный пароль (попытка 1 из 5)" },
  { action: "logout", details: "Пользователь вышел из системы" },
  { action: "account_locked", details: "Учётная запись временно заблокирована после 5 неудачных попыток входа" },
  { action: "user_created", details: "Создана новая учётная запись" },
  { action: "role_changed", details: "Роль изменена с «HR» на «Руководитель»" },
  { action: "candidate_transferred", details: "Кандидат передан другому HR с указанием причины" },
  { action: "candidate_status_changed", details: "Статус кандидата изменён" },
  { action: "candidate_exported", details: "Экспорт списка кандидатов в CSV" },
];

export const AUDIT_LOG: AuditEvent[] = Array.from({ length: 28 }, (_, i) => {
  const tpl = pick(AUDIT_TEMPLATES, seed(i, 83));
  const actor = pick(USERS, seed(i, 89));
  return {
    id: `audit-${i + 1}`,
    action: tpl.action,
    actorName: actor.fullName,
    targetName: tpl.action.startsWith("candidate") ? pick(CANDIDATES, seed(i, 97)).fullName : tpl.action === "user_created" || tpl.action === "role_changed" ? pick(USERS, seed(i, 101)).fullName : undefined,
    ipAddress: `10.20.${i % 8}.${(i * 7) % 254}`,
    createdAt: iso(-(i * 2), 8 + (i % 10), (i * 17) % 60),
    details: tpl.details,
  } satisfies AuditEvent;
});
