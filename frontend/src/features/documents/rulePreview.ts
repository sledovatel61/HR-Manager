/** Живой preview «Что произойдёт» для формы «Моих правил».
 *
 * Строится ПОЛНОСТЬЮ на клиенте из данных формы и настроек уведомлений
 * автора правила — без обращения к серверу: никакой записи в outbox или
 * создания задач здесь нет и быть не может (зеркало политики сервера:
 * фактический текст и получателей определяет только backend при отправке).
 *
 * Логика тихих часов и рабочих дней зеркалит чистые функции
 * `backend/app/quiet_hours.py` (тихие часы могут переходить полночь,
 * рабочие дни — ISO 1=Пн..7=Вс), а образец текста —
 * `backend/app/candidate_messages.py` (document_request/document_reminder):
 * имя кандидата и фактический список недостающих документов при реальной
 * отправке подставит сервер, здесь — образец из выбранной версии списка.
 */

import { STAGE_LABELS, type NotificationPreferences } from "../../types";
import type { RuleInput } from "./types";

/** Сколько символов образца текста показываем в блоке «Что произойдёт». */
export const TEXT_SAMPLE_LIMIT = 200;

export interface RulePreviewContext {
  /** Настройки уведомлений автора; null — ещё грузятся или недоступны. */
  prefs: NotificationPreferences | null;
  /** Имя выбранной опубликованной версии списка ("" — не выбран). */
  listName: string;
  /** Названия документов выбранной версии — для образца текста. */
  listItems: string[];
  /** Момент времени для расчёта «если правило сработает сейчас». */
  now: Date;
}

export interface RulePreview {
  /** Чего не хватает, чтобы описание было полным. */
  missing: string[];
  /** Когда сработает, для каких кандидатов и что сделает. */
  when: string;
  what: string;
  where: string;
  /** Правило выключено в форме. */
  disabled: boolean;
  /** Тихие часы/рабочие дни: что случится с отправкой. */
  timing: string;
  /** Образец текста кандидату (первые ~200 символов) или null. */
  textSample: string | null;
}

interface HHMM {
  minutes: number;
  label: string;
}

export function parseHHMM(value: string): HHMM | null {
  if (typeof value !== "string" || value.length !== 5 || value[2] !== ":") return null;
  const hours = Number(value.slice(0, 2));
  const minutesPart = Number(value.slice(3));
  if (
    !Number.isInteger(hours) ||
    !Number.isInteger(minutesPart) ||
    hours < 0 ||
    hours > 23 ||
    minutesPart < 0 ||
    minutesPart > 59
  ) {
    return null;
  }
  return { minutes: hours * 60 + minutesPart, label: value };
}

/** Тихий интервал может переходить полночь: 21:00–08:00. */
export function inQuietPeriod(
  currentMinutes: number,
  start: HHMM,
  end: HHMM,
): boolean {
  if (start.minutes === end.minutes) return false;
  if (start.minutes < end.minutes) {
    return currentMinutes >= start.minutes && currentMinutes < end.minutes;
  }
  return currentMinutes >= start.minutes || currentMinutes < end.minutes;
}

interface LocalParts {
  /** ISO weekday: 1=Пн..7=Вс. */
  weekday: number;
  /** Минуты от полуночи локального времени. */
  minutes: number;
}

/** Локальные часы/минуты и день недели для UTC-мгновения в часовом поясе. */
function localParts(instant: Date, timeZone: string, cache: Map<string, Intl.DateTimeFormat>): LocalParts {
  let fmt = cache.get(timeZone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
    cache.set(timeZone, fmt);
  }
  let year = 1970;
  let month = 1;
  let day = 1;
  let hour = 0;
  let minute = 0;
  for (const part of fmt.formatToParts(instant)) {
    const value = Number(part.value);
    if (part.type === "year") year = value;
    else if (part.type === "month") month = value;
    else if (part.type === "day") day = value;
    else if (part.type === "hour") hour = value % 24;
    else if (part.type === "minute") minute = value;
  }
  // День недели именно локальной календарной даты (0=Вс → ISO 7).
  const dow = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return { weekday: dow === 0 ? 7 : dow, minutes: hour * 60 + minute };
}

const formatterCache = new Map<string, Intl.DateTimeFormat>();

export interface QuietSchedule {
  timezone: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
  workdays: number[];
}

/**
 * Первое разрешённое мгновение НЕ РАНЬШЕ `now` по тихим часам и рабочим
 * дням (зеркало `next_allowed_time` из backend). Возвращает `null`, если
 * ограничений нет (тихие часы выключены и все дни рабочие).
 */
export function nextAllowedTime(nowUtc: Date, schedule: QuietSchedule): Date | null {
  const start = parseHHMM(schedule.quiet_hours_start);
  const end = parseHHMM(schedule.quiet_hours_end);
  if (!start || !end) return null;
  const workdays = schedule.workdays.length > 0 ? schedule.workdays : [1, 2, 3, 4, 5, 6, 7];
  const quietEnabled = start.minutes !== end.minutes;
  if (!quietEnabled && workdays.length === 7) return null;

  let instant = new Date(Math.floor(nowUtc.getTime() / 60_000) * 60_000);
  // Ограниченный обход: прыгаем сразу к концу тихих часов / к следующему
  // дню, поэтому итераций единицы даже при неделе выходных.
  for (let guard = 0; guard < 64; guard += 1) {
    const local = localParts(instant, schedule.timezone, formatterCache);
    if (!workdays.includes(local.weekday)) {
      instant = new Date(instant.getTime() + (24 * 60 - local.minutes) * 60_000);
      continue;
    }
    if (quietEnabled && inQuietPeriod(local.minutes, start, end)) {
      // Сколько минут осталось до конца тихого интервала.
      let delta: number;
      if (start.minutes <= end.minutes) {
        delta = end.minutes - local.minutes;
      } else if (local.minutes >= start.minutes) {
        delta = 24 * 60 - local.minutes + end.minutes;
      } else {
        delta = end.minutes - local.minutes;
      }
      instant = new Date(instant.getTime() + delta * 60_000);
      continue;
    }
    return instant;
  }
  return instant;
}

const WEEKDAYS_FULL = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

function workdaysLabel(workdays: number[]): string | null {
  const sorted = [...new Set(workdays)].sort((a, b) => a - b);
  if (sorted.length === 7) return null;
  if (sorted.length === 5 && sorted.every((d) => d <= 5)) return "по будням";
  return `в дни: ${sorted.map((d) => WEEKDAYS_FULL[(d - 1) % 7]).join(", ")}`;
}

/** Фраза про тихие часы и рабочие дни для текущей формы. */
export function timingNote(now: Date, prefs: NotificationPreferences | null): string {
  if (!prefs) {
    return "Тихие часы, рабочие дни и часовой пояс возьмём из ваших настроек уведомлений.";
  }
  const start = parseHHMM(prefs.quiet_hours_start);
  const end = parseHHMM(prefs.quiet_hours_end);
  if (!start || !end) {
    return "Тихие часы, рабочие дни и часовой пояс возьмём из ваших настроек уведомлений.";
  }
  const workdays = prefs.workdays.length > 0 ? prefs.workdays : [1, 2, 3, 4, 5, 6, 7];
  const quietEnabled = start.minutes !== end.minutes;
  const daysLabel = workdaysLabel(workdays);

  const next = nextAllowedTime(now, {
    timezone: prefs.timezone,
    quiet_hours_start: prefs.quiet_hours_start,
    quiet_hours_end: prefs.quiet_hours_end,
    workdays,
  });
  const truncatedNow = new Date(Math.floor(now.getTime() / 60_000) * 60_000);
  const deferred = next !== null && next.getTime() > truncatedNow.getTime();

  if (deferred && next) {
    const time = new Intl.DateTimeFormat("ru-RU", {
      timeZone: prefs.timezone,
      hour: "2-digit",
      minute: "2-digit",
    }).format(next);
    const date = new Intl.DateTimeFormat("ru-RU", {
      timeZone: prefs.timezone,
      day: "2-digit",
      month: "2-digit",
    }).format(next);
    const windowPart = quietEnabled
      ? `Тихие часы ${start.label}–${end.label}${daysLabel ? ` и рабочие дни ${daysLabel}` : ""}`
      : `Рабочие дни ${daysLabel}`;
    return `${windowPart}: прямо сейчас отправка на паузе — если правило сработает сейчас, отправим в ${time} (${date}, пояс ${prefs.timezone}).`;
  }
  if (quietEnabled && daysLabel) {
    return `Тихие часы ${start.label}–${end.label} и рабочие дни ${daysLabel}: если это случится ночью или в нерабочий день — отправим в ${end.label}.`;
  }
  if (quietEnabled) {
    return `Тихие часы ${start.label}–${end.label}: если это случится ночью — отправим в ${end.label}.`;
  }
  if (daysLabel) {
    return `Отправка только ${daysLabel}: если это случится в нерабочий день — подождём до ближайшего рабочего.`;
  }
  return "Тихих часов нет: если правило сработает, отправка не откладывается.";
}

/** Образец письма кандидату по шаблону сервера (первые ~200 символов). */
export function buildTextSample(
  action: RuleInput["params"]["action"],
  items: string[],
): string | null {
  if (action === "apply_list") return null;
  const names = items.length > 0 ? items.slice(0, 4) : [];
  const block =
    names.length > 0
      ? names.map((name) => `— ${name}`).join("\n") +
        (items.length > names.length ? "\n— …" : "")
      : "— …";
  const lead =
    action === "document_request"
      ? "Для продолжения оформления нам нужны следующие документы:"
      : "Напоминаем: мы всё ещё ожидаем от вас следующие документы:";
  const full =
    "Здравствуйте, {имя кандидата}!\n\n" +
    `${lead}\n${block}\n\n` +
    "Пожалуйста, передайте их вашему HR-менеджеру.";
  if (full.length <= TEXT_SAMPLE_LIMIT) return full;
  return `${full.slice(0, TEXT_SAMPLE_LIMIT).trimEnd()}…`;
}

/** Полное описание того, что сделает правило, по данным формы. */
export function buildRulePreview(input: RuleInput, ctx: RulePreviewContext): RulePreview {
  const missing: string[] = [];
  if (!input.name.trim()) missing.push("название правила");
  if (!input.params.list_id) missing.push("список документов");
  if (
    input.params.action === "document_reminder" &&
    (input.params.days === null || !Number.isFinite(input.params.days))
  ) {
    missing.push("через сколько дней напомнить");
  }

  const stage = STAGE_LABELS[input.params.stage];
  const listName = ctx.listName || "выбранный список";

  let when: string;
  if (input.params.trigger === "stage_transition") {
    when = `Когда кандидат, к которому у вас есть доступ, перейдёт на этап «${stage}»`;
  } else {
    const days = input.params.days ?? 1;
    when = `Когда пройдёт ${days} ${days === 1 ? "день" : days < 5 ? "дня" : "дней"} после применения списка документов, а документы так и не получены (этап «${stage}»)`;
  }

  let what: string;
  let where: string;
  if (input.params.action === "apply_list") {
    what = `система сама применит список «${listName}» в карточке кандидата, если списка там ещё нет`;
    where = "кандидату ничего не отправляется — всё происходит в карточке";
  } else if (input.params.action === "document_request") {
    what = `система отправит кандидату запрос документов по списку «${listName}»`;
    where =
      input.params.channel === "telegram"
        ? "в Telegram кандидата"
        : "на электронную почту кандидата";
  } else {
    what = `система отправит кандидату напоминание о документах по списку «${listName}»`;
    where =
      input.params.channel === "telegram"
        ? "в Telegram кандидата"
        : "на электронную почту кандидата";
  }

  return {
    missing,
    when,
    what,
    where,
    disabled: !input.enabled,
    timing: timingNote(ctx.now, ctx.prefs),
    textSample: buildTextSample(input.params.action, ctx.listItems),
  };
}
