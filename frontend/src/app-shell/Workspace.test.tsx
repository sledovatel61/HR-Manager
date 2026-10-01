/** Workspace-тесты переноса диагностики: отдельный пункт меню «Готовность
 * пилота» убран, вместо него вкладка «Диагностика запуска и обновлений»
 * внутри «Администрирование»; старая ссылка #/readiness редиректит на новую
 * вкладку; не-админы вкладку не видят.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CurrentUser, PilotReadiness, UserRole } from "../types";
import { ToastProvider } from "../design-system/components/Toast";
import Workspace from "./Workspace";
import {
  SETTINGS_GROUPS,
  SETTINGS_SECTIONS,
  settingsGroupForSection,
  settingsGroupsFor,
} from "./settingsGroups";
import { sectionsForRole } from "./workspaceSections";

vi.mock("../api", async () => ({
  ...(await vi.importActual<typeof import("../api")>("../api")),
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
  listLibraryMaterials: vi.fn().mockResolvedValue({
    items: [],
    categories: [],
    can_manage: false,
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
  it("отдельный пункт меню «Готовность пилота» убран, «Администрирование» живёт в «Настройках»", async () => {
    renderWorkspace("admin");
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    const settingsNav = screen.getByRole("navigation", { name: "Настройки" });
    await waitFor(() =>
      expect(within(settingsNav).getByRole("button", { name: "Настройки" })).toBeInTheDocument(),
    );
    expect(nav).not.toHaveTextContent("Готовность пилота");
    expect(nav).not.toHaveTextContent("Администрирование");
    expect(settingsNav).toHaveTextContent("Настройки");
    // Прямая ссылка открывает раздел даже без пункта в меню.
    expect(await screen.findByRole("tab", { name: "Уведомления и пилот" })).toBeInTheDocument();
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
    await screen.findByRole("navigation", { name: "Настройки" });
    expect(
      screen.queryByRole("tab", { name: "Диагностика запуска и обновлений" }),
    ).toBeNull();
    expect(screen.queryByText(/всё ли в порядке с установкой/)).toBeNull();
    // Старая ссылка не открывает диагностику руководителю — он на очереди.
    expect(await screen.findByText(/Состояние очереди и worker/)).toBeInTheDocument();
  });

  it("обычный HR не видит ни пункта меню, ни администрирования", async () => {
    window.location.hash = "#/preferences";
    renderWorkspace("hr");
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    const settingsNav = screen.getByRole("navigation", { name: "Настройки" });
    await waitFor(() =>
      expect(within(settingsNav).getByRole("button", { name: "Настройки" })).toBeInTheDocument(),
    );
    expect(nav).not.toHaveTextContent("Администрирование");
    expect(nav).not.toHaveTextContent("Готовность пилота");
    // Прямая ссылка на скрытый раздел работает: скрытие ссылки не равно праву.
    expect(await screen.findByText(/Настройки уведомлений/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// «Настройки» (UX feedback 2026-09-29)
// ---------------------------------------------------------------------------

describe("раздел «Настройки»", () => {
  it("оставляет ежедневные разделы в меню, а настраиваемые переносит в группы", async () => {
    renderWorkspace("admin");
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    await screen.findByRole("navigation", { name: "Настройки" });

    for (const daily of [
      "Кандидаты",
      "Календарь",
      "Воронка кандидатов",
      "График выхода",
      "Аналитика",
      "Уведомления",
      "Напоминания",
      "Шаблоны и материалы",
    ]) {
      expect(nav).toHaveTextContent(daily);
    }
    for (const moved of [
      "Мои правила",
      "Настройки уведомлений",
      "Интеграции",
      "Списки документов",
      "Шаблоны документов",
      "Обновления",
      "Лицензия",
      "Администрирование",
      "Пользователи",
    ]) {
      expect(nav).not.toHaveTextContent(moved);
    }
  });

  it("показывает администратору все восемь групп", async () => {
    const user = userEvent.setup();
    renderWorkspace("admin");
    await user.click(
      within(screen.getByRole("navigation", { name: "Настройки" })).getByRole("button", {
        name: "Настройки",
      }),
    );
    expect(window.location.hash).toBe("#/settings");
    const hub = await screen.findByRole("list", { name: "Группы настроек" });
    expect(within(hub).getAllByRole("listitem")).toHaveLength(SETTINGS_GROUPS.length);
    for (const group of SETTINGS_GROUPS) {
      expect(within(hub).getByRole("heading", { name: group.title })).toBeInTheDocument();
    }
  });

  it("не показывает HR групп, у которых нет прав", async () => {
    const user = userEvent.setup();
    renderWorkspace("hr");
    await user.click(
      within(screen.getByRole("navigation", { name: "Настройки" })).getByRole("button", {
        name: "Настройки",
      }),
    );
    const hub = await screen.findByRole("list", { name: "Группы настроек" });
    expect(within(hub).getByRole("heading", { name: "Мои правила" })).toBeInTheDocument();
    expect(within(hub).getByRole("heading", { name: "Контент и документы" })).toBeInTheDocument();
    for (const forbidden of [
      "Обновления",
      "Лицензия",
      "Администрирование",
      "Пользователи",
    ]) {
      expect(within(hub).queryByRole("heading", { name: forbidden })).not.toBeInTheDocument();
    }
  });

  it("открывает раздел из группы и ставит его в заголовок", async () => {
    const user = userEvent.setup();
    renderWorkspace("hr");
    await user.click(
      within(screen.getByRole("navigation", { name: "Настройки" })).getByRole("button", {
        name: "Настройки",
      }),
    );
    await user.click((await screen.findByRole("button", { name: /Мои правила/ })));
    expect(window.location.hash).toBe("#/rules");
    expect(await screen.findByRole("heading", { name: "Мои правила" })).toBeInTheDocument();
  });
});

describe("группы настроек", () => {
  it("покрывают все перенесённые разделы и ничего лишнего", () => {
    // Новый перенесённый раздел нельзя забыть: тест упадёт, пока группа
    // для него не появится.
    const covered = new Set(SETTINGS_GROUPS.flatMap((group) => [...group.targets]));
    expect([...covered].sort()).toEqual([...SETTINGS_SECTIONS].sort());
    for (const group of SETTINGS_GROUPS) {
      expect(group.title.trim()).not.toBe("");
      expect(group.description.trim()).not.toBe("");
    }
  });

  it("для HR показывают только те группы, куда у него есть доступ", () => {
    const allowed = sectionsForRole("hr");
    const ids = settingsGroupsFor(allowed).map((group) => group.id);
    expect(ids).toEqual(["notifications", "rules", "integrations", "content"]);
    for (const id of ["updates", "license", "admin", "users"]) {
      expect(ids).not.toContain(id);
    }
  });

  it("у каждого раздела есть ровно одна группа", () => {
    for (const section of SETTINGS_SECTIONS) {
      expect(settingsGroupForSection(section)).toBeDefined();
    }
  });
});
