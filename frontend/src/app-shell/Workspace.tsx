import { DocumentListsPage } from "../features/documents/DocumentListsPage";
import { LibraryPage } from "../features/library/LibraryPage";
import { MyRulesPage } from "../features/documents/MyRulesPage";
import { useEffect, useState } from "react";
import { logout, onUnauthorized } from "../api";
import { Icon } from "../design-system/icons/Icon";
import { ROLE_LABELS, WORKING_MODE_LABELS, type CurrentUser } from "../types";
import CandidatesListPage from "../features/candidates/CandidatesListPage";
import KanbanPage from "../features/candidates/KanbanPage";
import SchedulePage from "../features/schedule/SchedulePage";
import CalendarPage from "../features/calendar/CalendarPage";
import AnalyticsPage from "../features/analytics/AnalyticsPage";
import { NotificationBell } from "../features/notifications/NotificationBell";
import { NotificationCenterPage } from "../features/notifications/NotificationCenterPage";
import { RemindersPage } from "../features/notifications/RemindersPage";
import { PreferencesPage } from "../features/notifications/PreferencesPage";
import { IntegrationsPage } from "../features/notifications/IntegrationsPage";
import { UsersPage } from "../features/users/UsersPage";
import { UpdateChannelPage } from "../features/updates/UpdateChannelPage";
import { AdminPage } from "../features/admin/AdminPage";
import { LicensePage } from "../features/license/LicensePage";
import { SetupWizard } from "../features/notifications/SetupWizard";
import MyQueuePage from "../features/queue/MyQueuePage";
import { SettingsHub } from "./SettingsHub";
import { SETTINGS_SECTIONS } from "./settingsGroups";
import { SECTION_META, sectionsForRole } from "./workspaceSections";
import { useWorkspaceSection } from "./useWorkspaceSection";
import "./workspace.css";

interface WorkspaceProps {
  current: CurrentUser;
  onLoggedOut: () => void;
}

function initialsOf(fullName: string, username: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }
  return (fullName || username).slice(0, 2).toUpperCase();
}

/** Post-login application shell: navigation, current-user info, logout. */
export default function Workspace({ current, onLoggedOut }: WorkspaceProps) {
  const { user } = current;
  const sections = sectionsForRole(user.role);
  // Ежедневные разделы — то, что осталось в меню; остальное живёт в
  // «Настройках». Фильтр по общему с `sectionsForRole`, поэтому права и
  // видимость не могут разойтись.
  const dailySections = sections.filter(
    (item) => item !== "settings" && !SETTINGS_SECTIONS.includes(item),
  );
  const [section, navigate, adminTab, navigateAdminTab] =
    useWorkspaceSection(sections[0]);
  // Deep-link на раздел, недоступный роли (например, #/admin у HR или старый
  // #/readiness у руководителя), приводит к первому доступному разделу;
  // права всё равно перепроверяет сервер.
  const activeSection = sections.includes(section) ? section : sections[0];
  const [pendingCandidateId, setPendingCandidateId] = useState<string | null>(null);

  // A 401 from any API call means the session is gone: return to login.
  useEffect(() => onUnauthorized(onLoggedOut), [onLoggedOut]);

  // First-login setup wizard (timezone + quiet hours quick-set). Shown
  // once; safe to skip and to resume later from the preferences section.
  const [setupDone, setSetupDone] = useState(false);

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      onLoggedOut();
    }
  };

  /** Cross-section navigation: jump from a calendar event to its candidate
   * card (queue for HR, shared list for managers/admins). */
  const openCandidate = (id: string) => {
    setPendingCandidateId(id);
    navigate(user.role === "hr" ? "queue" : "candidates");
  };

  return (
    <div className="workspace">
      <a className="skip-link" href="#main-content">
        Перейти к содержимому
      </a>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="sidebar-logo" aria-hidden="true">
            <Icon name="users" size={18} />
          </span>
          <span>HR Manager</span>
        </div>
        <nav className="sidebar-nav" aria-label="Разделы">
          {dailySections.map((item) => (
            <button
              key={item}
              type="button"
              className={`sidebar-link ${item === activeSection ? "is-active" : ""}`}
              aria-current={item === activeSection ? "page" : undefined}
              onClick={() => navigate(item)}
            >
              <Icon name={SECTION_META[item].icon} size={16} />
              <span>{SECTION_META[item].label}</span>
            </button>
          ))}
        </nav>
        <nav className="sidebar-nav sidebar-nav-secondary" aria-label="Настройки">
          {sections
            .filter((item) => item === "settings")
            .map((item) => (
              <button
                key={item}
                type="button"
                className={`sidebar-link sidebar-link-settings ${item === activeSection ? "is-active" : ""}`}
                aria-current={item === activeSection ? "page" : undefined}
                onClick={() => navigate(item)}
              >
                <Icon name={SECTION_META[item].icon} size={16} />
                <span>{SECTION_META[item].label}</span>
              </button>
            ))}
        </nav>
      </aside>

      <div className="workspace-main">
        <header className="topbar">
          <h1 className="topbar-title">{SECTION_META[activeSection].label}</h1>
          <div className="topbar-right">
            <button
              type="button"
              className={`topbar-settings ${activeSection === "settings" ? "is-active" : ""}`}
              onClick={() => navigate("settings")}
            >
              <Icon name="settings" size={16} />
              Настройки
            </button>
            <NotificationBell onOpenCandidate={openCandidate} />
            <div className="topbar-user">
            <span className="topbar-avatar" aria-hidden="true">
              {initialsOf(user.full_name, user.username)}
            </span>
            <span className="topbar-user-text">
              <span className="topbar-username">{user.full_name || user.username}</span>
              <span className="topbar-role">{ROLE_LABELS[user.role]}</span>
              {current.working_mode && (
                <span className="topbar-working-mode" title="Режим, выбранный при установке">
                  Режим: {WORKING_MODE_LABELS[current.working_mode]}
                </span>
              )}
            </span>
            <button type="button" className="topbar-logout" onClick={() => void handleLogout()}>
              <Icon name="log-out" size={15} />
              Выйти
            </button>
            </div>
          </div>
        </header>

        <main id="main-content" className="workspace-content" tabIndex={-1}>
          {/* «Моя очередь»: сначала сводка дня, ниже — рабочий список
              кандидатов в режиме queue (как и раньше). */}
          {activeSection === "queue" && (
            <MyQueuePage
              onOpenCandidate={openCandidate}
              onOpenNotifications={() => navigate("notifications")}
            />
          )}
          {activeSection === "calendar" && (
            <CalendarPage user={user} onOpenCandidate={openCandidate} />
          )}
          {activeSection === "kanban" && <KanbanPage user={user} />}
          {activeSection === "schedule" && (
            <SchedulePage user={user} onOpenCandidate={openCandidate} />
          )}
          {activeSection === "analytics" && <AnalyticsPage user={user} />}
          {activeSection === "notifications" && (
            <NotificationCenterPage onOpenCandidate={openCandidate} />
          )}
          {activeSection === "reminders" && (
            <RemindersPage user={user} onOpenCandidate={openCandidate} />
          )}
          {activeSection === "preferences" && <PreferencesPage />}
          {activeSection === "documents" && <DocumentListsPage />}
          {activeSection === "templates" && <LibraryPage />}
          {activeSection === "rules" && <MyRulesPage />}
          {activeSection === "integrations" && <IntegrationsPage user={user} />}
          {activeSection === "updates" && <UpdateChannelPage />}
          {activeSection === "license" && <LicensePage />}
          {activeSection === "admin" && (
            <AdminPage role={user.role} tab={adminTab} onTabChange={navigateAdminTab} />
          )}
          {activeSection === "users" && <UsersPage currentUser={user} />}
          {activeSection === "settings" && (
            <SettingsHub
              allowed={sections}
              activeSection={activeSection}
              onOpen={navigate}
            />
          )}
          {activeSection !== "calendar" &&
            activeSection !== "kanban" &&
            activeSection !== "schedule" &&
            activeSection !== "analytics" &&
            activeSection !== "notifications" &&
            activeSection !== "reminders" &&
            activeSection !== "preferences" &&
            activeSection !== "documents" &&
            activeSection !== "templates" &&
            activeSection !== "rules" &&
            activeSection !== "integrations" &&
            activeSection !== "updates" &&
            activeSection !== "license" &&
            activeSection !== "admin" &&
            activeSection !== "users" &&
            activeSection !== "settings" && (
            <CandidatesListPage
              key={activeSection}
              user={user}
              mode={activeSection === "deleted" ? "deleted" : activeSection === "queue" ? "queue" : "all"}
              openCandidateId={pendingCandidateId}
              onCandidateOpened={() => setPendingCandidateId(null)}
            />
          )}
        </main>
        {!setupDone && <SetupWizard onDone={() => setSetupDone(true)} />}
      </div>
    </div>
  );
}
