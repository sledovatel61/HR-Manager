/**
 * Дата-хелперы вкладки «График выхода» (локальная дата, без UTC-сдвигов).
 *
 * Сервер обменивается календарными датами в формате ISO (`YYYY-MM-DD`):
 * дата выхода — это день календаря, а не момент времени, поэтому конвертация
 * идёт через локальные компоненты даты, а не через `toISOString()`.
 */

const WEEKDAYS_SHORT = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MONTHS_SHORT = [
  "янв.",
  "февр.",
  "мар.",
  "апр.",
  "мая",
  "июн.",
  "июл.",
  "авг.",
  "сент.",
  "окт.",
  "нояб.",
  "дек.",
];

/** Локальная дата → `YYYY-MM-DD`. */
export function toIsoDate(value: Date): string {
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

/** `YYYY-MM-DD` → локальная дата (полночь выбранного дня). */
export function fromIsoDate(value: string): Date {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(year, (month ?? 1) - 1, day ?? 1);
}

export function today(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

export function addDays(value: Date, days: number): Date {
  const result = new Date(value);
  result.setDate(result.getDate() + days);
  return result;
}

/** Понедельник недели, содержащей дату. */
export function startOfWeek(value: Date): Date {
  const result = new Date(value);
  const shift = (result.getDay() + 6) % 7; // понедельник = 0
  result.setDate(result.getDate() - shift);
  result.setHours(0, 0, 0, 0);
  return result;
}

export function startOfMonth(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

export function endOfMonth(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth() + 1, 0);
}

/** «пн, 10 авг. 2026» — формат строки-разделителя дня (как в Excel). */
export function formatDayHeading(iso: string): string {
  const value = fromIsoDate(iso);
  return `${WEEKDAYS_SHORT[value.getDay()]}, ${value.getDate()} ${MONTHS_SHORT[value.getMonth()]} ${value.getFullYear()}`;
}

/** «10.08.2026» — компактная дата для подписей. */
export function formatShortDate(iso: string): string {
  const value = fromIsoDate(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(value.getDate())}.${pad(value.getMonth() + 1)}.${value.getFullYear()}`;
}

/** «3–9 августа» / «29 сентября – 5 октября» — подпись периода. */
export function formatPeriodLabel(fromIso: string, toIso: string): string {
  const from = fromIsoDate(fromIso);
  const to = fromIsoDate(toIso);
  const monthLong = (value: Date) =>
    value.toLocaleDateString("ru-RU", { month: "long" }).replace(" г.", "");
  if (from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear()) {
    return `${from.getDate()}–${to.getDate()} ${monthLong(from)}`;
  }
  return `${from.getDate()} ${monthLong(from)} – ${to.getDate()} ${monthLong(to)}`;
}

/** `09:15:00` → `09:15` (в API время приходит с секундами). */
export function shortTime(value: string | null): string {
  if (!value) return "";
  return value.slice(0, 5);
}

/** Время строки: `09:15`, интервал `13:00–14:00` или «—». */
export function displayTime(start: string | null, end: string | null): string {
  const from = shortTime(start);
  if (!from) return "—";
  const to = shortTime(end);
  return to ? `${from}–${to}` : from;
}
