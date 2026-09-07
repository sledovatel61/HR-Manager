/**
 * Period helpers for the analytics section.
 *
 * Preset boundaries (day/week/month/quarter) are computed in the SELECTED
 * IANA timezone and sent to the backend as explicit ``from``/``to`` ISO
 * instants, so the same preset always produces the same report parameters
 * regardless of the machine timezone. The backend only validates/echoes the
 * timezone — it never guesses machine-local time.
 */

interface WallParts {
  y: number;
  m: number;
  d: number;
  h: number;
  min: number;
  s: number;
  ms: number;
}

/** Wall-clock parts of an instant in the given IANA timezone. */
function wallParts(date: Date, timeZone: string): WallParts {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const parts = fmt.formatToParts(date);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return {
    y: get("year"),
    m: get("month"),
    d: get("day"),
    h: get("hour"),
    min: get("minute"),
    s: get("second"),
    ms: date.getMilliseconds(),
  };
}

/** Normalize a wall date shifted by ``deltaDays`` (month/year rollover). */
function shiftedWall(y: number, m: number, d: number, deltaDays: number): { y: number; m: number; d: number } {
  const date = new Date(Date.UTC(y, m - 1, d + deltaDays));
  return {
    y: date.getUTCFullYear(),
    m: date.getUTCMonth() + 1,
    d: date.getUTCDate(),
  };
}

/**
 * The instant when wall date ``(y, m, d)`` begins in the timezone.
 *
 * Moves the guess backwards by its own wall time-of-day until the wall clock
 * reads 00:00:00 (2-3 iterations; DST-safe). When the local midnight does not
 * exist (a DST jump exactly at midnight), the earliest instant of the day
 * (closest to midnight) is returned.
 */
export function localDayStart(y: number, m: number, d: number, timeZone: string): Date {
  let guess = new Date(Date.UTC(y, m - 1, d));
  let best = guess;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let i = 0; i < 10; i += 1) {
    const p = wallParts(guess, timeZone);
    const distance = p.h * 3_600_000 + p.min * 60_000 + p.s * 1000 + p.ms;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = guess;
    }
    if (distance === 0) {
      return guess;
    }
    guess = new Date(guess.getTime() - distance);
  }
  return best;
}

/** The start of the local day containing ``date`` in the timezone. */
export function startOfLocalDay(date: Date, timeZone: string): Date {
  const p = wallParts(date, timeZone);
  return localDayStart(p.y, p.m, p.d, timeZone);
}

export interface PeriodBounds {
  from: string;
  to: string;
}

/** [from, to) instant bounds of the given preset, computed in the timezone.
 * Days may be 23/24/25 hours long around DST — never a fixed 24h step. */
export function presetBounds(
  preset: "day" | "week" | "month" | "quarter",
  now: Date,
  timeZone: string
): PeriodBounds {
  const dayStart = startOfLocalDay(now, timeZone);
  const { y, m, d } = wallParts(dayStart, timeZone);

  let fromWall: { y: number; m: number; d: number };
  let toWall: { y: number; m: number; d: number };
  if (preset === "day") {
    fromWall = { y, m, d };
    toWall = shiftedWall(y, m, d, 1);
  } else if (preset === "week") {
    // Weekday of the WALL date (getDay() would use the machine timezone).
    const weekday = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 = Sunday
    const daysSinceMonday = (weekday + 6) % 7;
    fromWall = shiftedWall(y, m, d, -daysSinceMonday);
    toWall = shiftedWall(y, m, d, -daysSinceMonday + 7);
  } else if (preset === "month") {
    fromWall = { y, m, d: 1 };
    toWall = m === 12 ? { y: y + 1, m: 1, d: 1 } : { y, m: m + 1, d: 1 };
  } else {
    const quarterStartMonth = Math.floor((m - 1) / 3) * 3 + 1;
    fromWall = { y, m: quarterStartMonth, d: 1 };
    const nextMonth = quarterStartMonth + 3;
    toWall =
      nextMonth > 12
        ? { y: y + 1, m: nextMonth - 12, d: 1 }
        : { y, m: nextMonth, d: 1 };
  }

  const from = localDayStart(fromWall.y, fromWall.m, fromWall.d, timeZone);
  const to = localDayStart(toWall.y, toWall.m, toWall.d, timeZone);
  return { from: from.toISOString(), to: to.toISOString() };
}

/** Parse a <input type="date"> value ("YYYY-MM-DD") into local midnight
 * bounds in the timezone: [dayStart, next dayStart). */
export function customDayBounds(value: string, timeZone: string): PeriodBounds | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const y = Number(match[1]);
  const m = Number(match[2]);
  const d = Number(match[3]);
  const start = localDayStart(y, m, d, timeZone);
  const next = shiftedWall(y, m, d, 1);
  const end = localDayStart(next.y, next.m, next.d, timeZone);
  return { from: start.toISOString(), to: end.toISOString() };
}

/** IANA timezone choices for the selector (no hardcoded machine-local
 * fallbacks; the server validates whatever the user picks). */
export function timeZoneChoices(): string[] {
  const intl = Intl as unknown as {
    supportedValuesOf?: (key: string) => string[];
  };
  if (typeof intl.supportedValuesOf === "function") {
    try {
      return intl.supportedValuesOf("timeZone");
    } catch {
      // fall through to the minimal safe list
    }
  }
  return [
    "UTC",
    "Europe/Moscow",
    "Europe/Berlin",
    "Europe/London",
    "Asia/Yekaterinburg",
    "Asia/Almaty",
  ];
}
