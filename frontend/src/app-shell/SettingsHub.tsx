/**
 * «Настройки» — the hub that groups the rarely-touched sections
 * (UX feedback 2026-09-29). Every card is a link to a real section; the
 * group list is derived from the caller's allowed sections, so this page can
 * never show a link the server would refuse.
 */
import { Button } from "../design-system/components/Button";
import { EmptyState } from "../design-system/components/StateViews";
import { Icon } from "../design-system/icons/Icon";
import { AppearanceControls } from "./AppearanceControls";
import type { WorkspaceSection } from "./useWorkspaceSection";
import {
  SETTINGS_SECTION_LABELS,
  settingsGroupsFor,
  type SettingsGroup,
} from "./settingsGroups";

export function SettingsHub({
  allowed,
  activeSection,
  onOpen,
}: {
  allowed: readonly WorkspaceSection[];
  activeSection: WorkspaceSection;
  onOpen: (section: WorkspaceSection) => void;
}) {
  const groups = settingsGroupsFor(allowed);

  return (
    <section className="settings-hub">
      <p className="settings-intro">
        Здесь собраны разделы, которые настраивают один раз: уведомления,
        автоматические правила, обновления, лицензия, доступ и контент.
        Прямые ссылки на них по-прежнему работают.
      </p>
      <AppearanceControls />
      {groups.length === 0 ? (
        <EmptyState
          icon="settings"
          title="Настраивать пока нечего"
          description="Для вашей роли доступны только рабочие разделы — слева в меню."
        />
      ) : (
        <ul className="settings-grid" aria-label="Группы настроек">
          {groups.map((group) => (
            <li key={group.id}>
              <SettingsCard
                group={group}
                activeSection={activeSection}
                onOpen={onOpen}
              />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SettingsCard({
  group,
  activeSection,
  onOpen,
}: {
  group: SettingsGroup;
  activeSection: WorkspaceSection;
  onOpen: (section: WorkspaceSection) => void;
}) {
  const isActive = group.targets.includes(activeSection);
  return (
    <div className={`settings-card ${isActive ? "is-active" : ""}`}>
      <span className="settings-card-icon" aria-hidden="true">
        <Icon name={group.icon} size={18} />
      </span>
      <h2 className="settings-card-title">{group.title}</h2>
      <p className="settings-card-description">{group.description}</p>
      <div className="settings-card-actions">
        {group.targets.map((target) => (
          <Button
            key={target}
            variant={target === group.targets[0] ? "secondary" : "ghost"}
            size="sm"
            icon="chevron-right"
            onClick={() => onOpen(target)}
          >
            {SETTINGS_SECTION_LABELS[target] ?? target}
          </Button>
        ))}
      </div>
    </div>
  );
}
