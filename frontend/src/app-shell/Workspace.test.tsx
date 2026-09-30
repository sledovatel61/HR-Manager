/** Workspace-тесты переноса диагностики: отдельный пункт меню «Готовность
 * пилота» убран, вместо него вкладка «Диагностика запуска и обновлений»
 * внутри «Администрирование»; старая ссылка #/readiness редиректит на новую
 * вкладку; не-админы вкладку не видят.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CurrentUser, PilotReadiness, UserRole } from "../types";
import { ToastProvider } from "../design-system/components/Toast";
import Workspace from "./Workspace";

vi.mock("../api", () => ({
  logout: vi.fn().mockResolvedValue(undefined),
  onUnauthorized: vi.fn(() => () => undefined),
  unreadCount: vi.fn().mockResolvedValue({ count: 0 }),
  listNotifications: vi.fn().mockResolvedValue({ items: [] }),
  markAllNotificationsRead: vi.fn().mockResolvedValue(undefined),
  resolveNotification: vi.fn().mockResolvedValue(undefined),
  fetchSetupState: vi.fn().mockResolvedValue({
    pilot_exists: true,
    pilot_grant_active: true,
    preferences_initialized: true,
    worker_alive: true,
    channels: { telegram: "not_configured", email: "not_configured" },
  }),
  getPreferences: vi.fn().mockResolvedValue({
    timezone: "Europe/Moscow",
    quiet_hours_start: "22:00",
    quiet_hours_end: "09:00",
    workdays: [1, 2, 3, 4, 5],
    enabled_types: [],
    enabled_channels: [],
    initialized: true,
  }),
  listTimezones: vi.fn().mockResolvedValue({ timezones: ["Europe/Moscow"] }),
  savePreferences: vi.fn(),
  fetchQueueDiagnostics: vi.fn().mockResolvedValue({
    counts: {},
    oldest_queued_at: null,
    stuck_sending: 0,
    worker: { alive: true },
  }),
  listAccessGrants: vi.fn().mockResolvedValue({ items: [] }),
  createPilot: vi.fn(),
  revokePilotAccess: vi.fn(),
  fetchPilotReadiness: vi.fn().mockResolvedValue({
    generated_at: "2026-09-29T09:00:00Z",
    verdict: "готово",
    counts: { pass: 1, warning: 0, fail: 0 },
    host_evidence_age_seconds: 30,
    host_evidence_fresh: true,
    server_version: "0.14.0",
    checks: [
      {
        code: "database",
        title: "База данных",
        state: "pass",
        detail: "Подключение к базе работает.",
        action: "Ничего делать не нужно.",
        evidence: null,
      },
    ],
  } satisfies PilotReadiness),
}));

function currentUser(role: UserRole): CurrentUser {
  return {
    csrf_token: "csrf-test",
    user: {
      id: "user-1",
      username: "test",
      full_name: "Тест Тестов",
      role,
      is_active: true,
      locked_until: null,
      last_login_at: null,
      created_at: "2026-09-01T09:00:00Z",
    },
  };
}

function renderWorkspace(role: UserRole) {
  return render(
    <ToastProvider>
      <Workspace current={currentUser(role)} onLoggedOut={() => undefined} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  window.location.hash = "#/admin";
});

afterEach(async () => {
  // Сначала размонтируем (снимаем слушатели hashchange), затем сбрасываем
  // хэш и даём jsdom доставить отложенные события уже без слушателей —
  // иначе асинхронный hashchange «прошлого» теста прилетает в следующий.
  cleanup();
  window.location.hash = "";
  await new Promise((resolve) => setTimeout(resolve, 0));
});

describe("Диагностика в администрировании", () => {
  it("«Готовность пилота» убрана из меню, «Администрирование» живёт в «Настройках» (UX 2026-09-29)", async () => {
    renderWorkspace("admin");
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Настройки" })).toBeInTheDocument(),
    );
    expect(nav).not.toHaveTextContent("Готовность пилота");
    // Рабочий контур + «Настройки»; подразделы открываются по прежним ссылкам.
    expect(nav).toHaveTextContent("Воронка кандидатов");
    expect(nav).toHaveTextContent("Настройки");
    expect(nav).not.toHaveTextContent("Администрирование");
    expect(
      screen.getByRole("heading", { name: "Администрирование" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Все настройки" })).toBeInTheDocument();
  });

  it("в администрировании две вкладки, диагностика показывает отчёт и пояснение", async () => {
    const user = userEvent.setup();
    renderWorkspace("admin");
    const diagnosticsTab = await screen.findByRole("tab", {
      name: "Диагностика запуска и обновлений",
    });
    expect(
      screen.getByRole("tab", { name: "Уведомления и пилот" }),
    ).toBeInTheDocument();

    await user.click(diagnosticsTab);
    expect(
      await screen.findByText(/всё ли в порядке с установкой/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/HR Manager — отчёт для разработчика/),
    ).toBeInTheDocument();
    expect(await screen.findByText(/Вердикт: готово/)).toBeInTheDocument();
    expect(window.location.hash).toBe("#/admin/diagnostics");
  });

  it("прямая ссылка #/readiness редиректит на вкладку диагностики", async () => {
    window.location.hash = "#/readiness";
    renderWorkspace("admin");
    await waitFor(() => expect(window.location.hash).toBe("#/admin/diagnostics"));
    const diagnosticsTab = await screen.findByRole("tab", {
      name: "Диагностика запуска и обновлений",
    });
    expect(diagnosticsTab).toHaveAttribute("aria-selected", "true");
    expect(
      await screen.findByText(/всё ли в порядке с установкой/),
    ).toBeInTheDocument();
  });

  it("руководитель видит администрирование, но без вкладки диагностики", async () => {
    window.location.hash = "#/readiness";
    renderWorkspace("manager");
    await screen.findByRole("heading", { name: "Администрирование" });
    expect(
      screen.queryByRole("tab", { name: "Диагностика запуска и обновлений" }),
    ).toBeNull();
    expect(screen.queryByText(/всё ли в порядке с установкой/)).toBeNull();
    // Старая ссылка не открывает диагностику руководителю — он на очереди.
    expect(await screen.findByText(/Состояние очереди и worker/)).toBeInTheDocument();
  });

  it("обычный HR не видит администрирования ни в меню, ни в «Настройках»", async () => {
    window.location.hash = "#/preferences";
    renderWorkspace("hr");
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Настройки уведомлений" }),
      ).toBeInTheDocument(),
    );
    expect(nav).not.toHaveTextContent("Администрирование");
    expect(nav).not.toHaveTextContent("Готовность пилота");
    // Обзор «Настройки» без административных групп.
    await userEvent.click(screen.getByRole("button", { name: "Все настройки" }));
    expect(await screen.findByRole("button", { name: /Мои правила/ })).toBeInTheDocument();
    expect(screen.queryByText("Администрирование")).toBeNull();
    expect(screen.queryByText("Лицензия")).toBeNull();
    expect(screen.queryByText("Пользователи")).toBeNull();
  });
});
