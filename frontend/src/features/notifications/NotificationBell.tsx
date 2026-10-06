/** Notification popover: server-confirmed read state, guarded mutations and focus. */
import { useCallback, useEffect, useRef, useState } from "react";
import { listNotifications, markAllNotificationsRead, resolveNotification, unreadCount } from "../../api";
import { Icon } from "../../design-system/icons/Icon";
import type { AppNotification } from "../../types";
import "./notifications.css";

const POLL_MS = 30_000;

function formatWhen(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value));
}

export function NotificationBell({ onOpenCandidate }: { onOpenCandidate: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(0);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [error, setError] = useState(false);
  const [actionError, setActionError] = useState("");
  const [reading, setReading] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const busyRef = useRef(false);
  const sequence = useRef(0);

  const refresh = useCallback(async (afterMutation = false): Promise<boolean> => {
    // Polls must not race the mutation or overwrite its confirmed snapshot.
    if (busyRef.current && !afterMutation) return false;
    const request = ++sequence.current;
    try {
      const [unread, recent] = await Promise.all([
        unreadCount(), listNotifications({ limit: 5 }),
      ]);
      if (request !== sequence.current) return false;
      setCount(unread.count);
      setItems(recent.items);
      setError(false);
      return true;
    } catch {
      if (request === sequence.current) setError(true);
      return false;
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), POLL_MS);
    return () => { window.clearInterval(timer); sequence.current += 1; };
  }, [refresh]);

  useEffect(() => {
    if (!open) return;
    dialogRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
      if (event.key === "Tab") {
        const buttons = dialogRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
        if (!buttons?.length) { event.preventDefault(); return; }
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) {
          event.preventDefault(); first.focus();
        }
      }
    };
    const onClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClickOutside);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClickOutside);
    };
  }, [open]);

  const handleItem = async (item: AppNotification) => {
    setOpen(false);
    triggerRef.current?.focus();
    if (!item.object_type || !item.object_id) return;
    try {
      const resolved = await resolveNotification(item.id);
      if (resolved.allowed && resolved.object_type === "candidate" && resolved.object_id) {
        onOpenCandidate(resolved.object_id);
      }
    } catch { /* No navigation without server authorization. */ }
  };

  const handleReadAll = async () => {
    if (busyRef.current) return;
    busyRef.current = true;
    sequence.current += 1;
    setReading(true);
    setActionError("");
    try {
      await markAllNotificationsRead();
      if (!await refresh(true)) {
        setActionError("Отметка сохранена, но список не обновился. Нажмите «Обновить».");
      }
    } catch {
      setActionError("Не удалось отметить уведомления прочитанными. Повторите попытку.");
    } finally {
      busyRef.current = false;
      setReading(false);
    }
  };

  return (
    <div className="topbar-bell-wrap" ref={rootRef}>
      <button ref={triggerRef} type="button" className="topbar-bell"
        aria-label={count > 0 ? `Уведомления, непрочитанных: ${count}` : "Уведомления"}
        aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((value) => !value)}>
        <Icon name="bell" size={17} />
        <span className="bell-badge" aria-live="polite" aria-atomic="true">
          {count > 99 ? "99+" : count}
        </span>
      </button>
      {open && (
        <div ref={dialogRef} tabIndex={-1} className="bell-popover" role="dialog" aria-label="Последние уведомления">
          <div className="bell-popover-header">
            <span>Уведомления</span>
            <span className="status-pill neutral">{error ? "нет связи" : "обновлено"}</span>
          </div>
          {items.length === 0 ? (
            <p className="bell-empty">{error ? "Не удалось загрузить уведомления." : "Уведомлений пока нет."}</p>
          ) : (
            <ul className="bell-popover-list">
              {items.map((item) => (
                <li key={item.id}>
                  <button type="button" className={`bell-item ${item.read_at ? "" : "is-unread"}`}
                    onClick={() => void handleItem(item)}>
                    <span className="bell-item-title">
                      <span>{item.title}</span>
                      <time className="bell-item-time" dateTime={item.created_at}>{formatWhen(item.created_at)}</time>
                    </span>
                    {item.body && <span className="bell-item-body">{item.body}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {actionError && <p className="bell-action-error" role="alert">{actionError}</p>}
          <div className="bell-popover-footer">
            <button type="button" className="btn btn-secondary btn-md" disabled={reading || count === 0}
              aria-busy={reading} onClick={() => void handleReadAll()}>
              {reading ? "Отмечаем…" : "Прочитать все"}
            </button>
            {error && <button type="button" className="btn btn-secondary btn-md" disabled={reading}
              onClick={() => void refresh().then((ok) => { if (ok) setActionError(""); })}>Обновить</button>}
          </div>
        </div>
      )}
    </div>
  );
}
