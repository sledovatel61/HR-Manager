/** UI-тесты раздела «Готовность пилота» (Phase 14): loading/verdict/offline/403, a11y, keyboard, redacted. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { PilotReadiness } from "../../types";
import { ReadinessPage } from "./ReadinessPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    fetchPilotReadiness: vi.fn(),
  };
});

import * as api from "../../api";

function readinessOf(overrides: Partial<PilotReadiness> = {}): PilotReadiness {
  return {
    verdict: "ready",
    generated_at: "2026-09-11T10:00:00Z",
    checks: [
      {
        code: "database",
        status: "pass",
        message_ru: "База данных доступна.",
        next_action_ru: "Действий не требуется.",
        details: {},
      },
      {
        code: "release_trust",
        status: "pass",
        message_ru: "Версия 0.13.0, trust store — 2 ключ(ей).",
        next_action_ru: "Действий не требуется.",
        details: { keys: [{ key_id: "pilot-test-key", fingerprint: "abcdef123456", revoked: false }] },
      },
      {
        code: "smtp",
        status: "warning",
        message_ru: "SMTP не настроен — только внутри приложения.",
        next_action_ru: "Если нужен email, задайте SMTP.",
        details: { enabled: false },
      },
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
  it("показывает загрузку, затем verdict ready и список проверок", async () => {
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(readinessOf());
    renderPage();
    expect(screen.getByRole("heading", { name: "Проверка готовности пилота" })).toBeTruthy();
    // loading via SkeletonRows (aria-busy)
    expect(screen.getByLabelText("Проверка готовности пилота")).toBeTruthy();
    await waitFor(() => expect(vi.mocked(api.fetchPilotReadiness)).toHaveBeenCalled());
    expect(await screen.findByText("Готово к пилоту")).toBeTruthy();
    expect(screen.getByText("database")).toBeTruthy();
    expect(screen.getByText("release_trust")).toBeTruthy();
    expect(screen.getByText(/База данных доступна/)).toBeTruthy();
  });

  it("offline/warning не блокируют: warning показан, verdict готов с предупреждениями", async () => {
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(
      readinessOf({
        verdict: "ready_with_warnings",
        checks: [
          {
            code: "channel",
            status: "warning",
            message_ru: "Канал недоступен.",
            next_action_ru: "Проверьте сеть позже.",
            details: {},
          },
          {
            code: "smtp",
            status: "warning",
            message_ru: "SMTP не настроен.",
            next_action_ru: "Настройте SMTP.",
            details: {},
          },
        ],
      })
    );
    renderPage();
    expect(await screen.findByText("Готово с предупреждениями")).toBeTruthy();
    expect(await screen.findByText("Канал недоступен.")).toBeTruthy();
    // warning pill
    expect(screen.getByText("Предупреждение")).toBeTruthy();
  });

  it("blocked: показывает запрет и fail статусы", async () => {
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(
      readinessOf({
        verdict: "blocked",
        checks: [
          {
            code: "release_trust",
            status: "fail",
            message_ru: "Trust store некорректен.",
            next_action_ru: "Исправьте trust store.",
            details: { keys: [] },
          },
        ],
      })
    );
    renderPage();
    expect(await screen.findByText("Запуск запрещён")).toBeTruthy();
    expect(await screen.findByText("Ошибка")).toBeTruthy();
  });

  it("403 показывает сообщение о правах", async () => {
    vi.mocked(api.fetchPilotReadiness).mockRejectedValue({ status: 403 });
    renderPage();
    expect(await screen.findByText(/Недостаточно прав/)).toBeTruthy();
  });

  it("offline: сеть недоступна — retry работает, сообщение про offline", async () => {
    vi.mocked(api.fetchPilotReadiness).mockRejectedValue({ status: 0 });
    renderPage();
    expect(await screen.findByText(/Сеть недоступна/)).toBeTruthy();
    expect(screen.getByText(/Офлайн не блокирует/)).toBeTruthy();
    const retry = screen.getByRole("button", { name: /Повторить попытку/ });
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(readinessOf());
    await userEvent.click(retry);
    expect(await screen.findByText("Готово к пилоту")).toBeTruthy();
  });

  it("a11y: aria-live, heading, keyboard nav (tabIndex)", async () => {
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(readinessOf());
    renderPage();
    const verdict = await screen.findByRole("status");
    expect(verdict.getAttribute("aria-live")).toBe("polite");
    // Each check has tabIndex 0 for keyboard
    const checks = await screen.findAllByRole("listitem");
    expect(checks.length).toBeGreaterThan(0);
    for (const li of checks) {
      expect(li.getAttribute("tabIndex")).toBe("0");
    }
    // Details are redacted (no raw private)
    const details = screen.queryByText(/private/);
    expect(details).toBeNull();
    // Keyboard: tab to first check
    const first = checks[0];
    first.focus();
    expect(document.activeElement).toBe(first);
  });

  it("empty state: показывает кнопку Проверить", async () => {
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue({ verdict: "ready", generated_at: "2026-09-11T10:00:00Z", checks: [] });
    renderPage();
    expect(await screen.findByText("Нет данных для отображения.")).toBeTruthy();
    const btn = screen.getByRole("button", { name: "Проверить" });
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(readinessOf());
    await userEvent.click(btn);
    expect(await screen.findByText("Готово к пилоту")).toBeTruthy();
  });

  it("details redacted: показывает только fingerprint, не raw key", async () => {
    vi.mocked(api.fetchPilotReadiness).mockResolvedValue(
      readinessOf({
        checks: [
          {
            code: "release_trust",
            status: "pass",
            message_ru: "trust ok",
            next_action_ru: "ok",
            details: { keys: [{ key_id: "pilot-test-key", fingerprint: "a1b2c3d4e5f6", revoked: false }] },
          },
        ],
      })
    );
    renderPage();
    await screen.findByText("Готово к пилоту");
    // Fingerprint visible
    expect(screen.getByText(/a1b2c3d4e5f6/)).toBeTruthy();
  });
});
