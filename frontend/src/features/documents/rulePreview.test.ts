/** Unit-тесты клиентского preview «Что произойдёт».
 *
 * Всё считается на клиенте из формы и настроек профиля — без сервера и без
 * записи в outbox. Фиксированные даты: 2026-09-30 — среда, 2026-10-03 —
 * суббота, 2026-10-05 — понедельник (часовой пояс Europe/Moscow, UTC+3).
 */

import { describe, expect, it } from "vitest";
import type { NotificationPreferences } from "../../types";
import type { RuleInput } from "./types";
import {
  buildRulePreview,
  buildTextSample,
  inQuietPeriod,
  nextAllowedTime,
  parseHHMM,
  TEXT_SAMPLE_LIMIT,
  timingNote,
} from "./rulePreview";

const prefs: NotificationPreferences = {
  timezone: "Europe/Moscow",
  quiet_hours_start: "22:00",
  quiet_hours_end: "09:00",
  workdays: [1, 2, 3, 4, 5],
  enabled_types: [],
  enabled_channels: [],
  initialized: true,
};

const ctx = {
  prefs,
  listName: "Оформление",
  listItems: ["Паспорт", "СНИЛС"],
  now: new Date("2026-09-30T09:00:00Z"), // среда 12:00 по Москве
};

const input: RuleInput = {
  name: "Запрос после оффера",
  enabled: true,
  params: {
    trigger: "stage_transition",
    action: "document_request",
    stage: "offer",
    list_id: "list-1",
    list_version_id: null,
    missing_required: true,
    channel: "email",
    days: null,
  },
};

describe("parseHHMM / inQuietPeriod", () => {
  it("разбирает HH:MM и отклоняет мусор", () => {
    expect(parseHHMM("22:00")?.minutes).toBe(22 * 60);
    expect(parseHHMM("9:00")).toBeNull();
    expect(parseHHMM("25:00")).toBeNull();
    expect(parseHHMM("")).toBeNull();
  });

  it("тихий интервал может переходить полночь", () => {
    const start = parseHHMM("21:00")!;
    const end = parseHHMM("08:00")!;
    expect(inQuietPeriod(23 * 60, start, end)).toBe(true);
    expect(inQuietPeriod(7 * 60 + 59, start, end)).toBe(true);
    expect(inQuietPeriod(8 * 60, start, end)).toBe(false);
    expect(inQuietPeriod(12 * 60, start, end)).toBe(false);
    // Равные границы — тихие часы выключены.
    expect(inQuietPeriod(23 * 60, parseHHMM("09:00")!, parseHHMM("09:00")!)).toBe(false);
  });
});

describe("nextAllowedTime — зеркало серверных тихих часов", () => {
  it("ночью откладывает отправку до конца тихих часов", () => {
    // Среда 23:00 по Москве → четверг 09:00 по Москве.
    const next = nextAllowedTime(new Date("2026-09-30T20:00:00Z"), prefs);
    expect(next?.toISOString()).toBe("2026-10-01T06:00:00.000Z");
  });

  it("в выходные откладывает до ближайшего рабочего дня", () => {
    // Суббота 12:00 по Москве → понедельник 09:00 по Москве.
    const next = nextAllowedTime(new Date("2026-10-03T09:00:00Z"), prefs);
    expect(next?.toISOString()).toBe("2026-10-05T06:00:00.000Z");
  });

  it("днём в рабочий день отправляет сразу", () => {
    // Среда 12:00 по Москве: первый разрешённый момент — само «сейчас».
    const now = new Date("2026-09-30T09:00:00Z");
    const next = nextAllowedTime(now, prefs);
    expect(next?.getTime()).toBe(now.getTime());
  });

  it("без тихих часов, но с буднями ждёт понедельника с полуночи", () => {
    const next = nextAllowedTime(new Date("2026-10-03T09:00:00Z"), {
      ...prefs,
      quiet_hours_start: "00:00",
      quiet_hours_end: "00:00",
    });
    // Понедельник 00:00 по Москве.
    expect(next?.toISOString()).toBe("2026-10-04T21:00:00.000Z");
  });

  it("без ограничений возвращает null", () => {
    expect(
      nextAllowedTime(new Date("2026-10-03T09:00:00Z"), {
        ...prefs,
        quiet_hours_start: "09:00",
        quiet_hours_end: "09:00",
        workdays: [1, 2, 3, 4, 5, 6, 7],
      }),
    ).toBeNull();
  });
});

describe("timingNote — фраза про тихие часы и рабочие дни", () => {
  it("днём в рабочий день обещает отправку в конце тихих часов", () => {
    expect(timingNote(ctx.now, prefs)).toBe(
      "Тихие часы 22:00–09:00 и рабочие дни по будням: если это случится ночью или в нерабочий день — отправим в 09:00.",
    );
  });

  it("ночью честно говорит, что отправка на паузе, и называет время", () => {
    const night = new Date("2026-09-30T20:00:00Z"); // среда 23:00 по Москве
    expect(timingNote(night, prefs)).toBe(
      "Тихие часы 22:00–09:00 и рабочие дни по будням: прямо сейчас отправка на паузе — если правило сработает сейчас, отправим в 09:00 (01.10, пояс Europe/Moscow).",
    );
  });

  it("в выходной называет понедельник", () => {
    const saturday = new Date("2026-10-03T09:00:00Z");
    expect(timingNote(saturday, prefs)).toContain("отправим в 09:00 (05.10");
  });

  it("без настроек пишет обобщённо", () => {
    expect(timingNote(ctx.now, null)).toBe(
      "Тихие часы, рабочие дни и часовой пояс возьмём из ваших настроек уведомлений.",
    );
  });

  it("с выключенными тихими часами ничего не обещает ждать", () => {
    const note = timingNote(ctx.now, {
      ...prefs,
      quiet_hours_start: "09:00",
      quiet_hours_end: "09:00",
      workdays: [1, 2, 3, 4, 5, 6, 7],
    });
    expect(note).toContain("не откладывается");
  });
});

describe("buildRulePreview — живое описание формы", () => {
  it("пишет, чего не хватает, пока форма не заполнена", () => {
    const preview = buildRulePreview(
      { ...input, name: "  ", params: { ...input.params, list_id: "" } },
      ctx,
    );
    expect(preview.missing).toEqual(["название правила", "список документов"]);
  });

  it("описывает переход на этап, отправку и доступ", () => {
    const preview = buildRulePreview(input, ctx);
    expect(preview.missing).toEqual([]);
    expect(preview.when).toContain("перейдёт на этап «Оффер»");
    expect(preview.what).toContain("запрос документов по списку «Оформление»");
    expect(preview.where).toBe("на электронную почту кандидата");
    expect(preview.disabled).toBe(false);
  });

  it("реагирует на смену этапа", () => {
    const preview = buildRulePreview(
      { ...input, params: { ...input.params, stage: "interview_scheduled" } },
      ctx,
    );
    expect(preview.when).toContain("«Собеседование назначено»");
  });

  it("реагирует на смену канала", () => {
    const preview = buildRulePreview(
      { ...input, params: { ...input.params, channel: "telegram" } },
      ctx,
    );
    expect(preview.where).toBe("в Telegram кандидата");
  });

  it("реагирует на смену действия: напоминание вместо запроса", () => {
    const preview = buildRulePreview(
      {
        ...input,
        params: {
          ...input.params,
          trigger: "scheduled_reminder",
          action: "document_reminder",
          days: 1,
        },
      },
      ctx,
    );
    expect(preview.when).toContain("Когда пройдёт 1 день");
    expect(preview.what).toContain("напоминание о документах");
  });

  it("склоняет дни напоминания", () => {
    const withDays = (days: number) =>
      buildRulePreview(
        {
          ...input,
          params: {
            ...input.params,
            trigger: "scheduled_reminder",
            action: "document_reminder",
            days,
          },
        },
        ctx,
      ).when;
    expect(withDays(1)).toContain("пройдёт 1 день");
    expect(withDays(3)).toContain("пройдёт 3 дня");
    expect(withDays(7)).toContain("пройдёт 7 дней");
  });

  it("для применения списка ничего не отправляет кандидату", () => {
    const preview = buildRulePreview(
      { ...input, params: { ...input.params, action: "apply_list", channel: null } },
      ctx,
    );
    expect(preview.what).toContain("сама применит список «Оформление»");
    expect(preview.where).toContain("кандидату ничего не отправляется");
    expect(preview.textSample).toBeNull();
  });

  it("помечает выключенное правило", () => {
    const preview = buildRulePreview({ ...input, enabled: false }, ctx);
    expect(preview.disabled).toBe(true);
  });

  it("показывает список, даже если имя ещё не выбрано", () => {
    const preview = buildRulePreview(input, { ...ctx, listName: "" });
    expect(preview.what).toContain("«выбранный список»");
  });
});

describe("buildTextSample — образец текста кандидату", () => {
  it("повторяет серверный шаблон запроса документов", () => {
    const sample = buildTextSample("document_request", ["Паспорт", "СНИЛС"]);
    expect(sample).toContain("Здравствуйте, {имя кандидата}!");
    expect(sample).toContain(
      "Для продолжения оформления нам нужны следующие документы:",
    );
    expect(sample).toContain("— Паспорт");
    expect(sample).toContain("— СНИЛС");
    expect(sample).toContain("Пожалуйста, передайте их вашему HR-менеджеру.");
  });

  it("для напоминания использует другой повод", () => {
    const sample = buildTextSample("document_reminder", ["Паспорт"]);
    expect(sample).toContain("мы всё ещё ожидаем от вас следующие документы");
  });

  it("обрезает образец до ~200 символов", () => {
    const many = Array.from({ length: 30 }, (_, i) => `Документ номер ${i + 1}`);
    const sample = buildTextSample("document_request", many)!;
    expect(sample.length).toBeLessThanOrEqual(TEXT_SAMPLE_LIMIT + 1);
    expect(sample.endsWith("…")).toBe(true);
  });

  it("для применения списка текста нет", () => {
    expect(buildTextSample("apply_list", ["Паспорт"])).toBeNull();
  });
});
