import { Icon } from "../../design-system/icons/Icon";
import type { UserRole } from "../../types";
import type { WorkspaceSection } from "../../app-shell/useWorkspaceSection";
import { GROUPS } from "./settingsNav";
import "./settings.css";

/**
 * Раздел «Настройки» (UX-замечания 2026-09-29, блок G).
 *
 * Рабочий контур (кандидаты, календарь, воронка, график, напоминания,
 * уведомления, аналитика, удалённые) остаётся в боковом меню; редко
 * используемые разделы сгруппированы здесь. Якоря разделов прежние
 * (#/preferences, #/rules, #/documents, …) — прямые ссылки продолжают
 * работать. Серверные проверки ролей не заменяются скрытием пунктов.
 */

interface SettingsPageProps {
  userRole: UserRole;
  /** Какой раздел сейчас открыт (подсветка активной карточки). */
  activeSection?: WorkspaceSection;
  onNavigate: (section: WorkspaceSection) => void;
}

export function SettingsPage({ userRole, activeSection, onNavigate }: SettingsPageProps) {
  const groups = GROUPS.filter((group) => group.roles.includes(userRole));
  return (
    <div className="settings-page">
      <p className="settings-intro">
        Редко используемые разделы собраны здесь. Рабочие разделы — в меню
        слева; права на каждый раздел сервер проверяет при каждом обращении.
      </p>
      {groups.map((group) => (
        <section key={group.title} className="settings-group" aria-label={group.title}>
          <h2 className="settings-group-title">{group.title}</h2>
          <div className="settings-links">
            {group.links.map((link) => (
              <button
                key={link.section}
                type="button"
                className={`settings-card ${link.section === activeSection ? "is-active" : ""}`}
                onClick={() => onNavigate(link.section)}
              >
                <span className="settings-card-head">
                  <Icon name={link.icon} size={16} />
                  <span className="settings-card-label">{link.label}</span>
                </span>
                <span className="settings-card-description">{link.description}</span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
