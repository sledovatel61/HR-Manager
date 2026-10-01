/**
 * Section labels, icons and the per-role visibility of the shell
 * (UX feedback 2026-09-29). Kept out of the component so the rules can be
 * unit-tested directly, and so the file exports components only.
 *
 * Права не изменились: этот список решает, что *видно* роли, а сервер
 * перепроверяет каждое действие. Прямые ссылки на скрытые разделы
 * по-прежнему приводят к проверке прав на сервере.
 */
import type { IconName } from "../design-system/icons/Icon";
import type { UserRole } from "../types";
import type { WorkspaceSection } from "./useWorkspaceSection";

export const SECTION_META: Record<WorkspaceSection, { label: string; icon: IconName }> = {
  queue: { label: "Моя очередь", icon: "inbox" },
  candidates: { label: "Кандидаты", icon: "table" },
  calendar: { label: "Календарь", icon: "calendar" },
  kanban: { label: "Воронка кандидатов", icon: "kanban" },
  schedule: { label: "График выхода", icon: "calendar-check" },
  deleted: { label: "Удалённые", icon: "trash" },
  analytics: { label: "Аналитика", icon: "bar-chart" },
  notifications: { label: "Уведомления", icon: "bell" },
  reminders: { label: "Напоминания", icon: "clock" },
  documents: { label: "Списки документов", icon: "table" },
  templates: { label: "Шаблоны и материалы", icon: "book" },
  rules: { label: "Мои правила", icon: "settings" },
  preferences: { label: "Настройки уведомлений", icon: "settings" },
  integrations: { label: "Интеграции", icon: "arrow-right-left" },
  updates: { label: "Обновления", icon: "loader" },
  license: { label: "Лицензия", icon: "shield" },
  admin: { label: "Администрирование", icon: "shield" },
  users: { label: "Пользователи", icon: "users" },
  settings: { label: "Настройки", icon: "settings" },
};

/**
 * Ежедневные разделы остаются в боковом меню; настраиваемые один раз
 * перенесены в «Настройки» (UX 2026-09-29). Права не изменились: список
 * по-прежнему решает, что видит роль, а сервер перепроверяет каждое
 * действие. Старые прямые ссылки (#/admin, #/documents, …) работают.
 */
function sectionsForRole(role: UserRole): WorkspaceSection[] {
  // Analytics is a team-level report: manager/admin only (HR gets 403 from
  // the API and never sees the navigation item). The notification center,
  // reminders and preferences are available to every role; the admin
  // screen (queue diagnostics + pilot setup) is admin-only. The backend
  // re-checks every right regardless of the navigation.
  // Ежедневная работа: то, чем HR пользуется постоянно.
  const daily: WorkspaceSection[] = ["notifications", "reminders"];
  // «Шаблоны и материалы» (библиотека) — ежедневный раздел: он вынесен
  // из «Настроек» в основное меню (ветка «Библиотека HR»), сразу после
  // уведомлений. Права прежние: сервер перепроверяет каждый вызов.
  const personal: WorkspaceSection[] = [
    ...daily,
    "templates",
    "preferences",
    "integrations",
    "documents",
    "rules",
  ];
  if (role === "hr") {
    return ["queue", "calendar", "kanban", "schedule", "deleted", ...personal, "settings"];
  }
  if (role === "admin") {
    // Диагностика запуска и обновлений — вкладка внутри «Администрирование»
    // (бывший отдельный пункт «Готовность пилота»): права прежние,
    // admin + update_channel_manage; backend перепроверяет и отвечает 403.
    // Лицензия — только admin (загрузка/замена).
    // «Пользователи» — управление учётными записями, строго admin-only
    // (и в навигации, и повторно внутри самой страницы).
    return [
      "candidates",
      "calendar",
      "kanban",
      "schedule",
      "deleted",
      "analytics",
      ...personal,
      "updates",
      "license",
      "admin",
      "users",
      "settings",
    ];
  }
  return [
    "candidates",
    "calendar",
    "kanban",
    "schedule",
    "deleted",
    "analytics",
    ...personal,
    "updates",
    "admin",
    "settings",
  ];
}


export { sectionsForRole };
