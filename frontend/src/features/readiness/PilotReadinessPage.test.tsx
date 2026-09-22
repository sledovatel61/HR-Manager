/** UI-тесты «Проверка готовности пилота» (Phase 14): loading/empty/offline/
 * error/retry, права (403), вердикты, клавиатурная доступность. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import type { PilotReadiness, PilotReadinessCheck } from "../../types";
import { PilotReadinessPage } from "./PilotReadinessPage";

function checkOf(overrides: Partial<PilotReadinessCheck> = {}): PilotReadinessCheck {
  return {
    code: "host_evidence",
    title: "Данные о хосте",
    state: "pass",
    detail: "Отчёт движка получен недавно.",
    action: "Ничего делать не нужно.",
    evidence: null,
    ...overrides,
  };
}

function reportOf(overrides: Partial<PilotReadiness> = {}): PilotReadiness {
  return {
    generated_at: "2026-09-11T09:00:00Z",
    verdict: "готово",
    counts: { pass: 1, warning: 0, fail: 0 },
    host_evidence_age_seconds: 30,
    host_evidence_fresh: true,
    server_version: "0.14.0",
    checks: [checkOf()],
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PilotReadinessPage", () => {
  it("показывает загрузку, затем вердикт «готово»", async () => {
    const fetcher = vi.fn().mockResolvedValue(reportOf());
    render(<PilotReadinessPage fetcher={fetcher} />);
    expect(screen.getByLabelText("Проверка готовности пилота")).toBeTruthy();
    expect(await screen.findByText(/Вердикт: готово/)).toBeTruthy();
    expect(screen.getByText("Проверено: 1, предупреждений: 0, блокирующих: 0.")).toBeTruthy();
    expect(screen.getByText("Проверено", { selector: ".readiness-pill" })).toBeTruthy();
  });

  it("офлайн/ошибка: честное сообщение и повтор по кнопке", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(reportOf({ verdict: "готово с предупреждениями" }));
    render(<PilotReadinessPage fetcher={fetcher} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/Приложение продолжает работать/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Повторить попытку" }));
    expect(await screen.findByText(/Вердикт: готово с предупреждениями/)).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("403: администратор без scope видит отказ, а не ложную готовность", async () => {
    const fetcher = vi.fn().mockRejectedValue(new ApiError(403, "forbidden"));
    render(<PilotReadinessPage fetcher={fetcher} />);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/update_channel_manage/)).toBeTruthy();
    expect(screen.queryByText(/Вердикт:/)).toBeNull();
  });

  it("показывает блокирующие проверки с объяснением и следующим действием", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      reportOf({
        verdict: "запуск запрещён",
        counts: { pass: 0, warning: 1, fail: 2 },
        checks: [
          checkOf({
            code: "published_ports",
            title: "Опубликованные порты",
            state: "fail",
            detail: "Порт базы данных опубликован на внешний адрес.",
            action: "Остановите публикацию порта и перезапустите стек.",
          }),
          checkOf({
            code: "disk_space",
            title: "Свободное место",
            state: "fail",
            detail: "На диске меньше 2048 МБ.",
            action: "Освободите место на диске.",
          }),
          checkOf({
            code: "backup_freshness",
            title: "Свежесть шифрованного бэкапа",
            state: "warning",
            detail: "Шифрованный бэкап старше 26 часов.",
            action: "Проверьте планировщик бэкапов.",
          }),
        ],
      })
    );
    render(<PilotReadinessPage fetcher={fetcher} />);
    expect(await screen.findByText(/Вердикт: запуск запрещён/)).toBeTruthy();
    expect(screen.getAllByText("Блокирует запуск")).toHaveLength(2);
    expect(screen.getByText(/Остановите публикацию порта/)).toBeTruthy();
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("честно помечает отсутствие свежих данных о хосте", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      reportOf({
        verdict: "готово с предупреждениями",
        counts: { pass: 0, warning: 1, fail: 0 },
        host_evidence_fresh: false,
        host_evidence_age_seconds: null,
        checks: [
          checkOf({
            code: "host_evidence",
            title: "Данные о хосте",
            state: "warning",
            detail: "Отчёт Windows-движка ещё не получен.",
            action: "Запустите движок и повторите проверку.",
          }),
        ],
      })
    );
    render(<PilotReadinessPage fetcher={fetcher} />);
    expect(await screen.findByText(/свежих данных о хосте нет/)).toBeTruthy();
    expect(screen.getByText("Предупреждение")).toBeTruthy();
  });

  it("пустой список проверок не считается готовностью", async () => {
    const fetcher = vi.fn().mockResolvedValue(reportOf({ checks: [], counts: { pass: 0, warning: 0, fail: 0 } }));
    render(<PilotReadinessPage fetcher={fetcher} />);
    expect(await screen.findByText(/пустой список проверок/)).toBeTruthy();
  });

  it("клавиатурная доступность: кнопка обновления работает с Enter", async () => {
    const fetcher = vi.fn().mockResolvedValue(reportOf());
    render(<PilotReadinessPage fetcher={fetcher} />);
    await screen.findByText(/Вердикт: готово/);
    const refresh = screen.getByRole("button", { name: "Проверить снова" });
    refresh.focus();
    expect(document.activeElement).toBe(refresh);
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it("вердикт доступен скринридеру и получает фокус после повторной проверки", async () => {
    const fetcher = vi.fn().mockResolvedValue(reportOf());
    render(<PilotReadinessPage fetcher={fetcher} />);
    await screen.findByText(/Вердикт: готово/);
    const verdict = screen.getByRole("status");
    expect(verdict.textContent).toContain("Вердикт: готово");
    expect(verdict.getAttribute("aria-live")).toBe("polite");
    await userEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
    // После повторной загрузки узел вердикта пересоздаётся — ищем заново.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("status")));
  });
});
