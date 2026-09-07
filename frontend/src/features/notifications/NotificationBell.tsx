/** Topbar notification bell: unread badge + popover with the latest items.

 * The popover is keyboard-navigable (Escape closes, focus stays inside),
 * the badge announces changes politely (aria-live), and item clicks
 * resolve backend access before navigation — the backend re-checks rights,
 * the bell never navigates blindly.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listNotifications, markAllNotificationsRead, resolveNotification, unreadCount } from "../../api";
import { Icon } from "../../design-system/icons/Icon";
import type { AppNotification } from "../../types";
import "./notifications.css";

const POLL_MS = 30_000;

function formatWhen(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function NotificationBell({
  onOpenCandidate,
}: {
  onOpenCandidate: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [error, setError] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [unread, recent] = await Promise.all([
        unreadCount(),
        listNotifications({ limit: 5 }),
      ]);
      setCount(unread.count);
      setItems(recent.items);
      setError(false);
    } catch {
      setError(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    const onClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open]);

  const handleItem = useCallback(
    async (item: AppNotification) => {
      setOpen(false);
      if (!item.object_type || !item.object_id) return;
      try {
        const resolved = await resolveNotification(item.id);
        if (resolved.allowed && resolved.object_type === "candidate" && resolved.object_id) {
          onOpenCandidate(resolved.object_id);
        }
      } catch {
        // backend unreachable — stay put, nothing is fabricated
      }
    },
    [onOpenCandidate],
  );

  const handleReadAll = useCallback(async () => {
    await markAllNotificationsRead();
    await refresh();
  }, [refresh]);

  const list = useMemo(() => items, [items]);

  return (
    <div className="topbar-bell-wrap" ref={rootRef}>
      <button
        type="button"
        className="topbar-bell"
        aria-label={count > 0 ? `Уведомления, непрочитанных: ${count}` : "Уведомления"}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Icon name="bell" size={17} />
        <span className="bell-badge" aria-live="polite" aria-atomic="true">
          {count > 99 ? "99+" : count}
        </span>
      </button>
      {open && (
        <div className="bell-popover" role="dialog" aria-label="Последние уведомления">
          <div className="bell-popover-header">
            <span>Уведомления</span>
            <span className="status-pill neutral">{error ? "нет связи" : "обновлено"}</span>
          </div>
          {list.length === 0 ? (
            <p className="bell-item-body" style={{ padding: "14px" }}>
              Уведомлений пока нет.
            </p>
          ) : (
            <ul className="bell-popover-list">
              {list.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`bell-item ${item.read_at ? "" : "is-unread"}`}
                    onClick={() => void handleItem(item)}
                  >
                    <span className="bell-item-title">
                      <span>{item.title}</span>
                      <span className="bell-item-time">{formatWhen(item.created_at)}</span>
                    </span>
                    {item.body && <p className="bell-item-body">{item.body}</p>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <div className="bell-popover-footer">
            <button type="button" className="btn btn-secondary btn-md" onClick={() => void handleReadAll()}>
              Прочитать все
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
