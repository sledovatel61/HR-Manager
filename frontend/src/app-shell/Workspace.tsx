import { useEffect } from "react";
import { logout, onUnauthorized } from "../api";
import { Icon, type IconName } from "../design-system/icons/Icon";
import { ROLE_LABELS, type CurrentUser, type UserRole } from "../types";
import CandidatesListPage from "../features/candidates/CandidatesListPage";
import KanbanPage from "../features/candidates/KanbanPage";
import { useWorkspaceSection, type WorkspaceSection } from "./useWorkspaceSection";
import "./workspace.css";

interface WorkspaceProps {
  current: CurrentUser;
  onLoggedOut: () => void;
}

const SECTION_META: Record<WorkspaceSection, { label: string; icon: IconName }> = {
  queue: { label: "Моя очередь", icon: "inbox" },
  candidates: { label: "Кандидаты", icon: "table" },
  kanban: { label: "Kanban", icon: "kanban" },
  deleted: { label: "Удалённые", icon: "trash" },
};

function sectionsForRole(role: UserRole): WorkspaceSection[] {
  return role === "hr" ? ["queue", "kanban", "deleted"] : ["candidates", "kanban", "deleted"];
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
  const [section, navigate] = useWorkspaceSection(sections[0]);

  // A 401 from any API call means the session is gone: return to login.
  useEffect(() => onUnauthorized(onLoggedOut), [onLoggedOut]);

  const handleLogout = async () => {
    try {
      await logout();
    } finally {
      onLoggedOut();
    }
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
              className={`sidebar-link ${item === section ? "is-active" : ""}`}
              aria-current={item === section ? "page" : undefined}
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
          <h1 className="topbar-title">{SECTION_META[section].label}</h1>
          <div className="topbar-user">
            <span className="topbar-avatar" aria-hidden="true">
              {initialsOf(user.full_name, user.username)}
            </span>
            <span className="topbar-user-text">
              <span className="topbar-username">{user.full_name || user.username}</span>
              <span className="topbar-role">{ROLE_LABELS[user.role]}</span>
            </span>
            <button type="button" className="topbar-logout" onClick={() => void handleLogout()}>
              <Icon name="log-out" size={15} />
              Выйти
            </button>
          </div>
        </header>

        <main id="main-content" className="workspace-content" tabIndex={-1}>
          {section === "kanban" ? (
            <KanbanPage user={user} />
          ) : (
            <CandidatesListPage
              key={section}
              user={user}
              mode={section === "deleted" ? "deleted" : section === "queue" ? "queue" : "all"}
            />
          )}
        </main>
      </div>
    </div>
  );
}
