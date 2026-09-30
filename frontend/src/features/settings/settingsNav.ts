import type { WorkspaceSection } from "../../app-shell/useWorkspaceSection";
import type { IconName } from "../../design-system/icons/Icon";
import type { UserRole } from "../../types";

/** Группы и подразделы «Настроек» (навигация; якоря разделов прежние). */

interface SettingsLink {
  section: WorkspaceSection;
  label: string;
  description: string;
  icon: IconName;
}

interface SettingsGroup {
  title: string;
  links: SettingsLink[];
  /** Минимальная роль для группы (пункты показываем по доступности). */
  roles: UserRole[];
}

export const GROUPS: SettingsGroup[] = [
  {
    title: "Уведомления",
    roles: ["hr", "manager", "admin"],
    links: [
      {
        section: "preferences",
        label: "Настройки уведомлений",
        description: "Часовой пояс, тихие часы, рабочие дни и типы уведомлений.",
        icon: "bell",
      },
    ],
  },
  {
    title: "Мои правила",
    roles: ["hr", "manager", "admin"],
    links: [
      {
        section: "rules",
        label: "Мои правила",
        description: "Автоматические действия по документам кандидатов.",
        icon: "settings",
      },
    ],
  },
  {
    title: "Контент и документы",
    roles: ["hr", "manager", "admin"],
    links: [
      {
        section: "documents",
        label: "Списки документов",
        description: "Состав и версии списков документов для кандидатов.",
        icon: "table",
      },
      {
        section: "templates",
        label: "Шаблоны документов",
        description: "Тексты, плейсхолдеры и генерация документов по шаблону.",
        icon: "file-text",
      },
    ],
  },
  {
    title: "Интеграции",
    roles: ["hr", "manager", "admin"],
    links: [
      {
        section: "integrations",
        label: "Интеграции",
        description: "Электронная почта и Telegram — подключение и тест.",
        icon: "arrow-right-left",
      },
    ],
  },
  {
    title: "Обновления",
    roles: ["manager", "admin"],
    links: [
      {
        section: "updates",
        label: "Обновления",
        description: "Канал обновлений и диагностика установки.",
        icon: "loader",
      },
    ],
  },
  {
    title: "Лицензия",
    roles: ["admin"],
    links: [
      {
        section: "license",
        label: "Лицензия",
        description: "Загрузка лицензионного ключа, лимиты и срок действия.",
        icon: "shield",
      },
    ],
  },
  {
    title: "Администрирование",
    roles: ["manager", "admin"],
    links: [
      {
        section: "admin",
        label: "Администрирование",
        description: "Очередь фоновых задач, диагностика запуска и обновлений.",
        icon: "shield",
      },
    ],
  },
  {
    title: "Пользователи",
    roles: ["admin"],
    links: [
      {
        section: "users",
        label: "Пользователи",
        description: "Учётные записи, роли и активность пользователей.",
        icon: "users",
      },
    ],
  },
];

/** Какие разделы «Настройки» доступны роли (навигация + прямые ссылки). */
export function settingsSectionsForRole(role: UserRole): WorkspaceSection[] {
  return GROUPS.filter((group) => group.roles.includes(role)).flatMap((group) =>
    group.links.map((link) => link.section)
  );
}

/** Все разделы «Настройки» (для определения активного пункта меню). */
export const SETTINGS_SECTIONS: WorkspaceSection[] = GROUPS.flatMap((group) =>
  group.links.map((link) => link.section)
);

