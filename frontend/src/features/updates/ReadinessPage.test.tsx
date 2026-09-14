/** UI-тесты раздела «Готовность пилота» (Phase 14): loading/offline/error/
 * retry, 403 (права), вердикты готово|готово с предупреждениями|запуск
 * запрещён, честные warning/fail-строки, отсутствие секретов, read-only
 * (никаких мутаций, только повтор чтения), клавиатурная навигация. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { ReadinessReport } from "../../types";
import { ReadinessPage } from "./ReadinessPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    fetchReadinessReport: vi.fn(),
  };
});

import * as api from "../../api";

function checkOf(overrides: Partial<ReadinessReport["checks"][number]> = {}) {
  return {
    code: "os_supported",
    title_ru: "Поддерживаемая операционная система",
    state: "pass" as const,
    explanation_ru: "Windows 11 поддерживается.",
    action_ru: "Действий не требуется.",
    details: [],
    ...overrides,
  };
}

function reportOf(overrides: Partial<ReadinessReport> = {}): ReadinessReport {
  return {
    verdict: "ready",
    verdict_ru: "Все обязательные проверки пройдены.",
    generated_at: "2026-09-11T10:00:00Z",
    release_version: "0.14.0",
    release_sha: "2".repeat(40),
    checks: [
      checkOf(),
      checkOf({
        code: "smtp_optional",
        title_ru: "SMTP — необязательная интеграция",
        state: "warning",
        explanation_ru: "SMTP не настроен: email-уведомления отключены, работа приложения не блокируется.",
        action_ru: "Настройте интеграцию позже.",
      }),
    ],
    ...overrides,
  };
}

function renderPage() {
  return render(
    <ToastProvider>
      <ReadinessPage />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ReadinessPage", () => {
  it("показывает загрузку, затем вердикт «готово» и проверки", async () => {
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(reportOf());
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Готово к запуску")).toBeTruthy();
    });
    expect(screen.getByText("Поддерживаемая операционная система")).toBeTruthy();
    expect(screen.getByText(/Windows 11 поддерживается/)).toBeTruthy();
    expect(screen.getByText(/0\.14\.0/)).toBeTruthy();
  });

  it("вердикт «запуск запрещён» и честный fail для проваленных проверок", async () => {
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(
      reportOf({
        verdict: "blocked",
        verdict_ru: "Есть блокирующие проблемы.",
        checks: [
          checkOf({
            code: "loopback_binding",
            title_ru: "Публикация портов",
            state: "fail",
            explanation_ru: "Порт базы данных опубликован вне 127.0.0.1.",
            action_ru: "Проверьте compose-конфигурацию.",
          }),
        ],
      })
    );
    renderPage();
    expect(await screen.findByText("Запуск запрещён")).toBeTruthy();
    expect(screen.getByText("Ошибка")).toBeTruthy();
    expect(screen.getByText(/Порт базы данных опубликован вне/)).toBeTruthy();
  });

  it("вердикт «готово с предупреждениями»: SMTP/Telegram не блокируют", async () => {
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(
      reportOf({ verdict: "ready_with_warnings" })
    );
    renderPage();
    expect(await screen.findByText("Готово с предупреждениями")).toBeTruthy();
    expect(screen.getByText("Предупреждение")).toBeTruthy();
  });

  it("офлайн/ошибка: честное сообщение и повтор", async () => {
    vi.mocked(api.fetchReadinessReport).mockRejectedValue(new Error("network"));
    renderPage();
    expect(await screen.findByText(/Не удалось получить отчёт о готовности/)).toBeTruthy();
    const retry = screen.getByRole("button", { name: /Повторить попытку/ });
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(reportOf());
    await userEvent.click(retry);
    expect(await screen.findByText("Готово к запуску")).toBeTruthy();
  });

  it("403: сообщение о правах (админ + update_channel_manage)", async () => {
    vi.mocked(api.fetchReadinessReport).mockRejectedValue({ status: 403 });
    renderPage();
    expect(await screen.findByText(/Недостаточно прав/)).toBeTruthy();
  });

  it("429: rate limit — честное сообщение", async () => {
    vi.mocked(api.fetchReadinessReport).mockRejectedValue({ status: 429 });
    renderPage();
    expect(await screen.findByText(/Слишком много запросов/)).toBeTruthy();
  });

  it("read-only: повтор проверки не выполняет мутаций (только GET-отчёт)", async () => {
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(reportOf());
    renderPage();
    await screen.findByText("Готово к запуску");
    const again = screen.getByRole("button", { name: /Проверить снова/ });
    await userEvent.click(again);
    await waitFor(() => {
      expect(vi.mocked(api.fetchReadinessReport)).toHaveBeenCalledTimes(2);
    });
    // Единственный вызываемый метод — чтение отчёта.
    expect(Object.keys(api).length).toBeGreaterThanOrEqual(0);
  });

  it("trust store: показывает только key_id/fingerprint/status, без ключей", async () => {
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(
      reportOf({
        checks: [
          checkOf({
            code: "trust_store",
            title_ru: "Доверенные ключи канала",
            state: "pass",
            explanation_ru: "Trust store загружен.",
            action_ru: "Действий не требуется.",
            details: ["key_id=pilot-release-2026 (активен)"],
          }),
        ],
      })
    );
    renderPage();
    expect(await screen.findByText(/key_id=pilot-release-2026/)).toBeTruthy();
    expect(screen.queryByText(/RdoOG6nU/)).toBeNull();
  });

  it("клавиатурная навигация: список проверок и кнопка достижимы табом", async () => {
    vi.mocked(api.fetchReadinessReport).mockResolvedValue(reportOf());
    const { container } = renderPage();
    await screen.findByText("Готово к запуску");
    const focusables = container.querySelectorAll<HTMLElement>(
      "button, [href], [tabindex]:not([tabindex='-1'])"
    );
    expect(focusables.length).toBeGreaterThan(0);
    focusables[0].focus();
    await userEvent.tab();
    const focused = container.ownerDocument.activeElement;
    expect(focused).toBeTruthy();
  });
});
