/** Fully synthetic mock data for the design prototype. No real PII. */

export type Role = "hr" | "manager" | "admin";

export type CandidateStatus =
  | "new"
  | "contact"
  | "reached"
  | "interview_scheduled"
  | "interview_done"
  | "offer"
  | "hired"
  | "probation"
  | "rejected"
  | "left";

export interface User {
  id: string;
  name: string;
  username: string;
  email: string;
  role: Role;
  active: boolean;
  locked: boolean;
  lastLogin: string;
}

export interface Vacancy {
  id: string;
  title: string;
  department: string;
}

export interface Candidate {
  id: string;
  firstName: string;
  lastName: string;
  middleName?: string;
  phone: string;
  email: string;
  source: string;
  vacancyId: string;
  ownerId: string;
  status: CandidateStatus;
  city: string;
  createdAt: string;
  updatedAt: string;
  nextActionAt?: string;
  deleted?: boolean;
}

export interface Interaction {
  id: string;
  candidateId: string;
  type: "call" | "email" | "note" | "meeting" | "status" | "transfer" | "system";
  title: string;
  body: string;
  actorId: string;
  at: string;
}

export interface CalendarEvent {
  id: string;
  candidateId?: string;
  title: string;
  type: "call" | "interview" | "reminder" | "other";
  start: string;
  end: string;
  ownerId: string;
  done: boolean;
}

export interface AuditEvent {
  id: string;
  at: string;
  actorId: string;
  action: string;
  entity: string;
  entityId: string;
  detail: string;
}

export const STATUS_META: Record<
  CandidateStatus,
  { label: string; tone: string }
> = {
  new: { label: "Новый", tone: "slate" },
  contact: { label: "Контакт", tone: "blue" },
  reached: { label: "Дозвон", tone: "sky" },
  interview_scheduled: { label: "Собеседование назначено", tone: "violet" },
  interview_done: { label: "Собеседование проведено", tone: "purple" },
  offer: { label: "Оффер", tone: "amber" },
  hired: { label: "Оформлен", tone: "teal" },
  probation: { label: "Испытательный срок", tone: "teal" },
  rejected: { label: "Отказ", tone: "rose" },
  left: { label: "Уволен", tone: "gray" },
};

export const STATUS_ORDER: CandidateStatus[] = [
  "new",
  "contact",
  "reached",
  "interview_scheduled",
  "interview_done",
  "offer",
  "hired",
  "probation",
  "rejected",
  "left",
];

export const SOURCES = [
  "HH (мок)",
  "Рекомендация",
  "Авито (мок)",
  "Карьерный сайт",
  "Telegram-канал",
  "Внутренний резерв",
  "Ярмарка вакансий",
];

export const users: User[] = [
  {
    id: "u-anna",
    name: "Анна Крылова",
    username: "a.krylova",
    email: "a.krylova@example.com",
    role: "hr",
    active: true,
    locked: false,
    lastLogin: "2026-09-02T08:12:00",
  },
  {
    id: "u-boris",
    name: "Борис Нестеров",
    username: "b.nesterov",
    email: "b.nesterov@example.com",
    role: "hr",
    active: true,
    locked: false,
    lastLogin: "2026-09-01T17:40:00",
  },
  {
    id: "u-vera",
    name: "Вера Шишкина",
    username: "v.shishkina",
    email: "v.shishkina@example.com",
    role: "hr",
    active: true,
    locked: false,
    lastLogin: "2026-09-02T09:01:00",
  },
  {
    id: "u-igor",
    name: "Игорь Савельев",
    username: "i.saveliev",
    email: "i.saveliev@example.com",
    role: "manager",
    active: true,
    locked: false,
    lastLogin: "2026-09-02T07:55:00",
  },
  {
    id: "u-marina",
    name: "Марина Орлова",
    username: "m.orlova",
    email: "m.orlova@example.com",
    role: "admin",
    active: true,
    locked: false,
    lastLogin: "2026-09-01T11:20:00",
  },
  {
    id: "u-dmitry",
    name: "Дмитрий Панов",
    username: "d.panov",
    email: "d.panov@example.org",
    role: "hr",
    active: false,
    locked: true,
    lastLogin: "2026-08-12T10:00:00",
  },
];

export const vacancies: Vacancy[] = [
  { id: "v1", title: "Менеджер по продажам", department: "Коммерция" },
  { id: "v2", title: "Инженер поддержки", department: "IT" },
  { id: "v3", title: "Бухгалтер", department: "Финансы" },
  { id: "v4", title: "Кладовщик", department: "Логистика" },
  { id: "v5", title: "HR-generalist", department: "HR" },
  { id: "v6", title: "Водитель-экспедитор", department: "Логистика" },
];

const FN = [
  "Алексей",
  "Мария",
  "Иван",
  "Елена",
  "Сергей",
  "Ольга",
  "Павел",
  "Наталья",
  "Кирилл",
  "Татьяна",
  "Артём",
  "Юлия",
  "Роман",
  "Светлана",
  "Никита",
  "Дарья",
  "Глеб",
  "Полина",
  "Вадим",
  "Алина",
  "Егор",
  "Виктория",
  "Тимур",
  "Ксения",
  "Марк",
  "София",
  "Лев",
  "Милана",
];

const LN = [
  "Соколов",
  "Волкова",
  "Морозов",
  "Лебедева",
  "Козлов",
  "Новикова",
  "Попов",
  "Васильева",
  "Семёнов",
  "Голубева",
  "Виноградов",
  "Богданова",
  "Воробьёв",
  "Фёдорова",
  "Михайлов",
  "Тарасова",
  "Белов",
  "Комарова",
  "Орлов",
  "Зайцева",
  "Макаров",
  "Куликова",
  "Андреев",
  "Громова",
  "Ершов",
  "Сазонова",
  "Тихонов",
  "Лапина",
];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function iso(day: number, hour = 10, minute = 0): string {
  return `2026-08-${pad(day)}T${pad(hour)}:${pad(minute)}:00`;
}

const ownerCycle = ["u-anna", "u-boris", "u-vera", "u-anna", "u-boris"];
const statusCycle: CandidateStatus[] = [
  "new",
  "contact",
  "reached",
  "interview_scheduled",
  "interview_done",
  "offer",
  "hired",
  "probation",
  "rejected",
  "left",
  "contact",
  "interview_scheduled",
  "new",
  "offer",
  "reached",
  "hired",
  "contact",
  "interview_done",
  "new",
  "probation",
  "rejected",
  "interview_scheduled",
  "contact",
  "offer",
  "reached",
  "new",
  "hired",
  "left",
];

export const candidates: Candidate[] = FN.slice(0, 28).map((firstName, i) => {
  const lastName = LN[i];
  const id = `c-${pad(i + 1)}`;
  const ownerId = ownerCycle[i % ownerCycle.length];
  const status = statusCycle[i];
  const vacancyId = vacancies[i % vacancies.length].id;
  const source = SOURCES[i % SOURCES.length];
  const day = 1 + (i % 28);
  return {
    id,
    firstName,
    lastName,
    middleName: i % 3 === 0 ? "Петрович" : i % 3 === 1 ? "Сергеевна" : undefined,
    phone: `+7 (900) ${pad(100 + i)}-${pad(10 + i)}-${pad(20 + i)}`,
    email: `${firstName.toLowerCase()}.${lastName.toLowerCase()}${i}@example.com`
      .replace(/ё/g, "e")
      .replace(/[^a-z0-9.@-]/gi, ""),
    source,
    vacancyId,
    ownerId,
    status,
    city: ["Москва", "Казань", "Самара", "Новосибирск", "Тула"][i % 5],
    createdAt: iso(day, 9, 15),
    updatedAt: iso(Math.min(28, day + 2), 14, 30),
    nextActionAt: status === "rejected" || status === "left" ? undefined : iso(Math.min(28, day + 3), 11, 0),
    deleted: false,
  };
});

export const interactions: Interaction[] = [];
candidates.forEach((c, i) => {
  interactions.push({
    id: `i-${c.id}-1`,
    candidateId: c.id,
    type: "system",
    title: "Кандидат создан",
    body: `Источник: ${c.source}. Вакансия назначена.`,
    actorId: c.ownerId,
    at: c.createdAt,
  });
  if (i % 2 === 0) {
    interactions.push({
      id: `i-${c.id}-2`,
      candidateId: c.id,
      type: "call",
      title: "Звонок",
      body: "Дозвон успешный. Кандидат заинтересован, запросил описание вакансии.",
      actorId: c.ownerId,
      at: c.updatedAt,
    });
  }
  if (i % 3 === 0) {
    interactions.push({
      id: `i-${c.id}-3`,
      candidateId: c.id,
      type: "note",
      title: "Заметка",
      body: "Готов к выходу в течение 2 недель. Ожидания по графику — сменный.",
      actorId: c.ownerId,
      at: iso(Math.min(28, 5 + (i % 20)), 16, 10),
    });
  }
  if (c.status === "interview_scheduled" || c.status === "interview_done" || c.status === "offer") {
    interactions.push({
      id: `i-${c.id}-4`,
      candidateId: c.id,
      type: "meeting",
      title: "Собеседование",
      body: "Очная встреча в офисе, 45 минут, с руководителем подразделения.",
      actorId: c.ownerId,
      at: iso(Math.min(28, 10 + (i % 15)), 12, 0),
    });
  }
});

export const events: CalendarEvent[] = [
  {
    id: "e1",
    candidateId: "c-04",
    title: "Собеседование · Лебедева Е.",
    type: "interview",
    start: "2026-09-02T11:00:00",
    end: "2026-09-02T11:45:00",
    ownerId: "u-anna",
    done: false,
  },
  {
    id: "e2",
    candidateId: "c-07",
    title: "Звонок · Попов П.",
    type: "call",
    start: "2026-09-02T14:30:00",
    end: "2026-09-02T14:45:00",
    ownerId: "u-anna",
    done: false,
  },
  {
    id: "e3",
    candidateId: "c-11",
    title: "Собеседование · Виноградов А.",
    type: "interview",
    start: "2026-09-03T10:00:00",
    end: "2026-09-03T11:00:00",
    ownerId: "u-boris",
    done: false,
  },
  {
    id: "e4",
    title: "Синхрон команды подбора",
    type: "other",
    start: "2026-09-03T16:00:00",
    end: "2026-09-03T16:30:00",
    ownerId: "u-igor",
    done: false,
  },
  {
    id: "e5",
    candidateId: "c-02",
    title: "Напоминание: оффер · Волкова М.",
    type: "reminder",
    start: "2026-09-04T09:30:00",
    end: "2026-09-04T09:40:00",
    ownerId: "u-vera",
    done: false,
  },
  {
    id: "e6",
    candidateId: "c-15",
    title: "Собеседование · Михайлов Н.",
    type: "interview",
    start: "2026-09-02T16:00:00",
    end: "2026-09-02T16:45:00",
    ownerId: "u-boris",
    done: false,
  },
  {
    id: "e7",
    candidateId: "c-19",
    title: "Звонок · Орлов В.",
    type: "call",
    start: "2026-09-05T12:00:00",
    end: "2026-09-05T12:20:00",
    ownerId: "u-anna",
    done: false,
  },
];

export const auditEvents: AuditEvent[] = [
  {
    id: "a1",
    at: "2026-09-02T08:12:11",
    actorId: "u-anna",
    action: "auth.login",
    entity: "session",
    entityId: "s-1001",
    detail: "Успешный вход",
  },
  {
    id: "a2",
    at: "2026-09-01T18:02:44",
    actorId: "u-igor",
    action: "candidate.transfer",
    entity: "candidate",
    entityId: "c-08",
    detail: "Передача: Вера Шишкина → Анна Крылова. Причина: отпуск HR",
  },
  {
    id: "a3",
    at: "2026-09-01T11:25:03",
    actorId: "u-marina",
    action: "user.role_change",
    entity: "user",
    entityId: "u-vera",
    detail: "Роль: hr (без изменений прав admin)",
  },
  {
    id: "a4",
    at: "2026-08-30T09:14:22",
    actorId: "u-marina",
    action: "user.create",
    entity: "user",
    entityId: "u-dmitry",
    detail: "Создан пользователь d.panov",
  },
  {
    id: "a5",
    at: "2026-08-29T21:00:01",
    actorId: "u-marina",
    action: "user.lock",
    entity: "user",
    entityId: "u-dmitry",
    detail: "Блокировка после серии неудачных входов",
  },
  {
    id: "a6",
    at: "2026-08-28T15:40:18",
    actorId: "u-boris",
    action: "candidate.status",
    entity: "candidate",
    entityId: "c-06",
    detail: "Статус: contact → interview_scheduled",
  },
  {
    id: "a7",
    at: "2026-08-27T10:11:00",
    actorId: "u-igor",
    action: "auth.logout",
    entity: "session",
    entityId: "s-0888",
    detail: "Выход пользователя",
  },
  {
    id: "a8",
    at: "2026-08-26T13:33:09",
    actorId: "u-anna",
    action: "candidate.update",
    entity: "candidate",
    entityId: "c-01",
    detail: "Обновлены контактные данные (мок)",
  },
];

export const kpi = {
  periodLabel: "Август 2026",
  created: 42,
  contacted: 38,
  reached: 29,
  interviewsScheduled: 18,
  interviewsDone: 14,
  offers: 7,
  hired: 5,
  left: 1,
  conversionOfferToHire: 0.71,
  conversionContactToInterview: 0.47,
  byHr: [
    { ownerId: "u-anna", processed: 16, hired: 2, interviews: 7 },
    { ownerId: "u-boris", processed: 14, hired: 2, interviews: 6 },
    { ownerId: "u-vera", processed: 12, hired: 1, interviews: 5 },
  ],
  funnel: [
    { status: "new" as CandidateStatus, count: 42 },
    { status: "contact" as CandidateStatus, count: 38 },
    { status: "reached" as CandidateStatus, count: 29 },
    { status: "interview_scheduled" as CandidateStatus, count: 18 },
    { status: "interview_done" as CandidateStatus, count: 14 },
    { status: "offer" as CandidateStatus, count: 7 },
    { status: "hired" as CandidateStatus, count: 5 },
  ],
};

export const savedViews = [
  { id: "sv1", name: "Мои активные", ownerOnly: true, statuses: ["new", "contact", "reached", "interview_scheduled"] as CandidateStatus[] },
  { id: "sv2", name: "Собеседования", ownerOnly: false, statuses: ["interview_scheduled", "interview_done"] as CandidateStatus[] },
  { id: "sv3", name: "Офферы и выход", ownerOnly: false, statuses: ["offer", "hired", "probation"] as CandidateStatus[] },
];

export function fullName(c: Candidate): string {
  return [c.lastName, c.firstName, c.middleName].filter(Boolean).join(" ");
}

export function shortName(c: Candidate): string {
  const mi = c.middleName ? ` ${c.middleName[0]}.` : "";
  return `${c.lastName} ${c.firstName[0]}.${mi}`;
}

export function initials(name: string): string {
  const parts = name.split(" ").filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function userById(id: string): User | undefined {
  return users.find((u) => u.id === id);
}

export function vacancyById(id: string): Vacancy | undefined {
  return vacancies.find((v) => v.id === id);
}

export const ROLE_LABEL: Record<Role, string> = {
  hr: "HR",
  manager: "Руководитель",
  admin: "Администратор",
};

/** Demo accounts for login screen (passwords are fake UI-only). */
export const DEMO_ACCOUNTS = [
  { username: "a.krylova", password: "demo-hr", role: "hr" as Role, userId: "u-anna" },
  { username: "i.saveliev", password: "demo-mgr", role: "manager" as Role, userId: "u-igor" },
  { username: "m.orlova", password: "demo-adm", role: "admin" as Role, userId: "u-marina" },
];
