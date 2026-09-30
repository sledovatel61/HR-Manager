/**
 * «Настройки» — the second level of the shell (UX feedback 2026-09-29).
 *
 * The daily sections stay in the sidebar; everything that is set up *once*
 * and then rarely touched moved here, grouped the way the brief asks:
 * Уведомления, Мои правила, Обновления, Лицензия, Администрирование,
 * Пользователи, Интеграции, Контент и документы.
 *
 * Two invariants this module must not break:
 *
 * 1. **Hiding a link is not a permission.** A group is a link to a real
 *    section that the backend re-authorizes; a hidden group and a 403 mean
 *    the same thing to a curious user, never «access granted». Visibility is
 *    derived from the *same* `sectionsForRole` list the sidebar uses, so the
 *    two can never disagree about who may see what.
 * 2. **Old links keep working.** Groups point at the unchanged section
 *    hashes, so a bookmarked `#/documents` or `#/admin` still lands on the
 *    same page it always did.
 */
import type { IconName } from "../design-system/icons/Icon";
import type { WorkspaceSection } from "./useWorkspaceSection";

export interface SettingsGroup {
  id: string;
  title: string;
  description: string;
  icon: IconName;
  /** Sections this group opens, in order. The first is its main destination. */
  targets: readonly WorkspaceSection[];
}

/** Sections «Настройки» opens — exactly the ones removed from the sidebar. */
export const SETTINGS_SECTIONS: readonly WorkspaceSection[] = [
  "preferences",
  "rules",
  "updates",
  "license",
  "admin",
  "users",
  "integrations",
  "documents",
  "templates",
];

export const SETTINGS_GROUPS: readonly SettingsGroup[] = [
  {
    id: "notifications",
    title: "Уведомления",
    description: "Что присылать, в какое время и как реагировать на задачи.",
    icon: "bell",
    targets: ["preferences"],
  },
  {
    id: "rules",
    title: "Мои правила",
    description: "Какие списки документов вы получаете и в каком объёме.",
    icon: "settings",
    targets: ["rules"],
  },
  {
    id: "updates",
    title: "Обновления",
    description: "Канал обновлений, проверка новой версии и установка.",
    icon: "loader",
    targets: ["updates"],
  },
  {
    id: "license",
    title: "Лицензия",
    description: "Ключ продукта, срок действия и количество рабочих мест.",
    icon: "shield",
    targets: ["license"],
  },
  {
    id: "admin",
    title: "Администрирование",
    description: "Очередь, запуск программы и диагностика обновлений.",
    icon: "shield",
    targets: ["admin"],
  },
  {
    id: "users",
    title: "Пользователи",
    description: "Учётные записи, роли и права доступа.",
    icon: "users",
    targets: ["users"],
  },
  {
    id: "integrations",
    title: "Интеграции",
    description: "Почта, календари и другие внешние сервисы.",
    icon: "arrow-right-left",
    targets: ["integrations"],
  },
  {
    id: "content",
    title: "Контент и документы",
    description: "Списки документов для кандидатов и шаблоны документов.",
    icon: "table",
    targets: ["documents", "templates"],
  },
];

/**
 * Groups reachable with the sections this role actually has. `allowed` comes
 * from `sectionsForRole`, the same source the sidebar uses, so the two views
 * cannot drift apart.
 */
export function settingsGroupsFor(
  allowed: readonly WorkspaceSection[],
): SettingsGroup[] {
  return SETTINGS_GROUPS.filter((group) =>
    group.targets.some((target) => allowed.includes(target)),
  );
}

export const SETTINGS_SECTION_LABELS: Record<string, string> = {
  preferences: "Настройки уведомлений",
  rules: "Мои правила",
  updates: "Обновления",
  license: "Лицензия",
  admin: "Администрирование",
  users: "Пользователи",
  integrations: "Интеграции",
  documents: "Списки документов",
  templates: "Шаблоны документов",
};

export function settingsGroupForSection(
  section: WorkspaceSection,
): SettingsGroup | undefined {
  return SETTINGS_GROUPS.find((group) => group.targets.includes(section));
}
