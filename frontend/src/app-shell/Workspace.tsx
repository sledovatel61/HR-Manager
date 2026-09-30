import { DocumentListsPage } from "../features/documents/DocumentListsPage";
import { TemplatesPage } from "../features/document-templates/TemplatesPage";
import { MyRulesPage } from "../features/documents/MyRulesPage";
import { useEffect, useState } from "react";
import { logout, onUnauthorized } from "../api";
import { Icon, type IconName } from "../design-system/icons/Icon";
import { ROLE_LABELS, WORKING_MODE_LABELS, type CurrentUser, type UserRole } from "../types";
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
import { SettingsPage } from "../features/settings/SettingsPage";
import { SETTINGS_SECTIONS, settingsSectionsForRole } from "../features/settings/settingsNav";
import { AdminPage } from "../features/admin/AdminPage";
import { LicensePage } from "../features/license/LicensePage";
import { SetupWizard } from "../features/notifications/SetupWizard";
import { useWorkspaceSection, type WorkspaceSection } from "./useWorkspaceSection";
import "./workspace.css";

interface WorkspaceProps {
  current: CurrentUser;
  onLoggedOut: () => void;
}

const SECTION_META: Record<WorkspaceSection, { label: string; icon: IconName }> = {
  queue: { label: "Моя очередь", icon: "inbox" },
  candidates: { label: "Кандидаты", icon: "table" },
  calendar: { label: "Календарь", icon: "calendar" },
  kanban: { label: "Воронка кандидатов", icon: "kanban" },
  schedule: { label: "График выхода", icon: "calendar-check" },
  deleted: { label: "Удалённые", icon: "trash" },
  analytics: { label: "Аналитика", icon: "bar-chart" },
  notifications: { label: "Уведомления", icon: "bell" },
  reminders: { label: "Напоминания", icon: "clock" },
  settings: { label: "Настройки", icon: "settings" },
  documents: { label: "Списки документов", icon: "table" },
  templates: { label: "Шаблоны документов", icon: "file-text" },
  rules: { label: "Мои правила", icon: "settings" },
  preferences: { label: "Настройки уведомлений", icon: "settings" },
  integrations: { label: "Интеграции", icon: "arrow-right-left" },
  updates: { label: "Обновления", icon: "loader" },
  license: { label: "Лицензия", icon: "shield" },
  admin: { label: "Администрирование", icon: "shield" },
  users: { label: "Пользователи", icon: "users" },
};

function sectionsForRole(role: UserRole): WorkspaceSection[] {
  // Рабочий контур (UX-замечания 2026-09-29, блок G): кандидаты/очередь,
  // календарь, воронка кандидатов, график выхода, напоминания, уведомления,
  // аналитика (manager/admin) и удалённые. Редко используемые разделы
  // собраны в «Настройках» (settingsForRole ниже); сервер повторно проверяет
  // права независимо от навигации.
  const work: WorkspaceSection[] =
    role === "hr"
      ? ["queue", "calendar", "kanban", "schedule", "reminders", "notifications", "deleted"]
      : [
          "candidates",
          "calendar",
          "kanban",
          "schedule",
          "reminders",
          "notifications",
          "deleted",
          "analytics",
        ];
  return [...work, "settings"];
}

/** Разделы «Настройки», доступные роли: показываются в обзоре настроек и
 * остаются доступными по прежним прямым ссылкам (#/rules, #/documents, …). */
function settingsForRole(role: UserRole): WorkspaceSection[] {
  return settingsSectionsForRole(role);
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
  const [section, navigate, adminTab, navigateAdminTab] =
    useWorkspaceSection(sections[0]);
  // Deep-link на раздел, недоступный роли (например, #/admin у HR или старый
  // #/readiness у руководителя), приводит к первому доступному разделу;
  // права всё равно перепроверяет сервер.
  const settingsSections = settingsForRole(user.role);
  const activeSection =
    sections.includes(section) || settingsSections.includes(section) ? section : sections[0];
  /** Раздел открыт из «Настроек» (в меню подсвечиваем «Настройки»). */
  const isSettingsSubsection = SETTINGS_SECTIONS.includes(activeSection);
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
          {sections.map((item) => (
            <button
              key={item}
              type="button"
              className={`sidebar-link ${
                item === activeSection ||
                (item === "settings" && isSettingsSubsection)
                  ? "is-active"
                  : ""
              }`}
              aria-current={
                item === activeSection || (item === "settings" && isSettingsSubsection)
                  ? "page"
                  : undefined
              }
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
          {activeSection === "settings" && (
            <SettingsPage userRole={user.role} onNavigate={navigate} />
          )}
          {isSettingsSubsection && (
            <>
              <div className="settings-subheader">
                <button type="button" onClick={() => navigate("settings")}>
                  <Icon name="settings" size={14} /> Все настройки
                </button>
                <span aria-hidden="true">→</span>
                <span>{SECTION_META[activeSection].label}</span>
              </div>
              {activeSection === "preferences" && <PreferencesPage />}
              {activeSection === "documents" && <DocumentListsPage />}
              {activeSection === "templates" && <TemplatesPage />}
              {activeSection === "rules" && <MyRulesPage />}
              {activeSection === "integrations" && <IntegrationsPage user={user} />}
              {activeSection === "updates" && <UpdateChannelPage />}
              {activeSection === "license" && <LicensePage />}
              {activeSection === "admin" && (
                <AdminPage role={user.role} tab={adminTab} onTabChange={navigateAdminTab} />
              )}
              {activeSection === "users" && <UsersPage currentUser={user} />}
            </>
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
          {(activeSection === "queue" || activeSection === "candidates" || activeSection === "deleted") && (
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
