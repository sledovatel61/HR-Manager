import { useEffect, useRef, useState } from "react";
import { Icon } from "../../icons/Icon";
import { IconButton } from "../ui/Button";
import { useAppState } from "../../state/AppState";
import { useRouter } from "../../router";
import { userById } from "../../data/mockData";
import { Avatar } from "../ui/Avatar";
import { NotificationsPopover } from "./NotificationsPopover";
import "./topbar.css";

const ROUTE_TITLES: Record<string, string> = {
  home: "Главная",
  queue: "Моя очередь",
  candidates: "Кандидаты",
  kanban: "Kanban",
  calendar: "Календарь",
  analytics: "Аналитика",
  templates: "Шаблоны",
  users: "Пользователи",
  audit: "Журнал аудита",
  settings: "Настройки",
};

export function Topbar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const { route } = useRouter();
  const { theme, toggleTheme, density, setDensity, currentUserId, logout, pushToast, simStatus } = useAppState();
  const currentUser = userById(currentUserId)!;
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickAway(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setUserMenuOpen(false);
    }
    document.addEventListener("mousedown", onClickAway);
    return () => document.removeEventListener("mousedown", onClickAway);
  }, []);

  return (
    <header className="app-topbar">
      <div className="topbar-title">
        <h1>{ROUTE_TITLES[route] ?? "HR Manager"}</h1>
        {simStatus !== "online" && (
          <span className="topbar-status-flag" role="status">
            <Icon name={simStatus === "degraded" ? "alert-triangle" : "wifi-off"} size={12} />
            {simStatus === "degraded" ? "Соединение нестабильно" : "Нет связи с сервером"}
          </span>
        )}
      </div>

      <button type="button" className="topbar-search" onClick={onOpenPalette}>
        <Icon name="search" size={15} />
        <span>Поиск кандидатов и команд…</span>
        <span className="topbar-search-kbd">
          <kbd>Ctrl</kbd>
          <kbd>K</kbd>
        </span>
      </button>

      <div className="topbar-actions">
        <IconButton
          icon={density === "compact" ? "list" : "layout-grid"}
          label={density === "compact" ? "Комфортная плотность" : "Компактная плотность"}
          onClick={() => setDensity(density === "compact" ? "comfortable" : "compact")}
        />
        <IconButton icon={theme === "dark" ? "sun" : "moon"} label={theme === "dark" ? "Светлая тема" : "Тёмная тема"} onClick={toggleTheme} />
        <div style={{ position: "relative" }}>
          <IconButton icon="bell" label="Уведомления" active={notifOpen} onClick={() => setNotifOpen((v) => !v)} />
          <NotificationsPopover open={notifOpen} onClose={() => setNotifOpen(false)} />
        </div>

        <div className="topbar-user" ref={menuRef}>
          <button type="button" className="topbar-user-trigger" onClick={() => setUserMenuOpen((v) => !v)} aria-haspopup="menu" aria-expanded={userMenuOpen}>
            <Avatar initials={currentUser.initials} color={currentUser.avatarColor} size="sm" />
            <Icon name="chevron-down" size={14} />
          </button>
          {userMenuOpen && (
            <div className="topbar-user-menu" role="menu">
              <div className="topbar-user-menu-head">
                <strong>{currentUser.fullName}</strong>
                <span>{currentUser.email}</span>
              </div>
              <button role="menuitem" type="button" className="menu-item" onClick={() => { setUserMenuOpen(false); pushToast("info", "Открыт раздел «Настройки» (мок)."); }}>
                <Icon name="settings" size={14} /> Настройки профиля
              </button>
              <button
                role="menuitem"
                type="button"
                className="menu-item menu-item-danger"
                onClick={() => {
                  setUserMenuOpen(false);
                  logout();
                }}
              >
                <Icon name="log-out" size={14} /> Выйти
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
