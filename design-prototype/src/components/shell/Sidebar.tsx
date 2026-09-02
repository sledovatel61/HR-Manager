import { Icon, type IconName } from "../../icons/Icon";
import { useRouter, type RouteName } from "../../router";
import { useAppState } from "../../state/AppState";
import { userById } from "../../data/mockData";
import { ROLE_LABELS, type UserRole } from "../../types";
import { Avatar } from "../ui/Avatar";

interface NavItem {
  route: RouteName;
  label: string;
  icon: IconName;
  roles?: UserRole[];
  shortcut?: string;
}

const NAV_ITEMS: NavItem[] = [
  { route: "home", label: "Главная", icon: "home", shortcut: "G H" },
  { route: "queue", label: "Моя очередь", icon: "inbox", roles: ["hr"], shortcut: "G Q" },
  { route: "candidates", label: "Кандидаты", icon: "users", shortcut: "G C" },
  { route: "kanban", label: "Kanban", icon: "kanban", shortcut: "G K" },
  { route: "calendar", label: "Календарь", icon: "calendar", shortcut: "G L" },
  { route: "analytics", label: "Аналитика", icon: "bar-chart", roles: ["manager", "admin"], shortcut: "G A" },
  { route: "templates", label: "Шаблоны", icon: "file-text" },
  { route: "users", label: "Пользователи", icon: "shield", roles: ["admin"] },
  { route: "audit", label: "Журнал аудита", icon: "clock", roles: ["admin", "manager"] },
  { route: "settings", label: "Настройки", icon: "settings" },
];

export function Sidebar() {
  const { route, navigate } = useRouter();
  const { currentUserId } = useAppState();
  const currentUser = userById(currentUserId)!;

  const visibleItems = NAV_ITEMS.filter((item) => !item.roles || item.roles.includes(currentUser.role));

  return (
    <nav className="app-sidebar" aria-label="Основная навигация">
      <div className="app-sidebar-brand">
        <span className="brand-mark" aria-hidden="true">
          <Icon name="spark" size={16} />
        </span>
        <span className="brand-name">HR Manager</span>
      </div>

      <ul className="app-sidebar-nav">
        {visibleItems.map((item) => {
          const active = route === item.route;
          return (
            <li key={item.route}>
              <button
                type="button"
                className={`sidebar-link ${active ? "is-active" : ""}`}
                onClick={() => navigate(item.route)}
                aria-current={active ? "page" : undefined}
              >
                <Icon name={item.icon} size={16} />
                <span>{item.label}</span>
                {item.shortcut && <span className="sidebar-shortcut">{item.shortcut}</span>}
              </button>
            </li>
          );
        })}
      </ul>

      <div className="app-sidebar-footer">
        <div className="sidebar-user">
          <Avatar initials={currentUser.initials} color={currentUser.avatarColor} size="sm" name={currentUser.fullName} />
          <div className="sidebar-user-info">
            <span className="sidebar-user-name">{currentUser.fullName}</span>
            <span className="sidebar-user-role">{ROLE_LABELS[currentUser.role]}</span>
          </div>
        </div>
      </div>
    </nav>
  );
}
