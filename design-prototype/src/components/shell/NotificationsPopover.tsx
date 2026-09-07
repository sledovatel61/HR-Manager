import { useEffect, useRef } from "react";
import { Icon } from "../../icons/Icon";
import "./notifications.css";

const MOCK_NOTIFICATIONS = [
  { id: "n1", title: "Собеседование через 30 минут", detail: "Дмитрий Волков — техническое интервью", tone: "info" as const },
  { id: "n2", title: "Кандидат передан вам", detail: "Марина Ковалёва передала Наталью Морозову", tone: "success" as const },
  { id: "n3", title: "Просрочено напоминание", detail: "Проверить фидбэк по тестовому заданию", tone: "warning" as const },
];

export function NotificationsPopover({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    function onClickAway(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickAway);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickAway);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="notif-popover" ref={ref} role="dialog" aria-label="Уведомления">
      <div className="notif-popover-head">Уведомления</div>
      <ul>
        {MOCK_NOTIFICATIONS.map((n) => (
          <li key={n.id} className={`notif-item notif-${n.tone}`}>
            <Icon name={n.tone === "warning" ? "alert-triangle" : n.tone === "success" ? "check-circle" : "info"} size={14} />
            <div>
              <p className="notif-title">{n.title}</p>
              <p className="notif-detail">{n.detail}</p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
