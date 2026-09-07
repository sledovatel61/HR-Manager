/** Notification center: server-side pagination, filters, mark-read/dismiss,
 * delivery details, and access-checked navigation to linked objects. */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  dismissNotifications,
  listNotifications,
  markNotificationsRead,
  notificationDelivery,
  resolveNotification,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Modal } from "../../design-system/components/Modal";
import { EmptyState, ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import type { AppNotification, DeliveryInfo, NotificationResolve } from "../../types";
import "./notifications.css";

const PAGE_SIZE = 20;

function formatFull(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

const DELIVERY_LABELS: Record<string, string> = {
  queued: "В очереди",
  sending: "Отправляется",
  accepted: "Принято провайдером",
  delivered: "Доставлено",
  failed: "Ошибка доставки",
  cancelled: "Отменено",
  skipped: "Пропущено (канал не настроен)",
};

export function NotificationCenterPage({ onOpenCandidate }: { onOpenCandidate: (id: string) => void }) {
  const { pushToast } = useToast();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [delivery, setDelivery] = useState<{ item: AppNotification; info: DeliveryInfo } | null>(null);
  const [resolving, setResolving] = useState<string | null>(null);

  const load = useCallback(
    async (nextOffset = offset, nextUnreadOnly = unreadOnly) => {
      setLoading(true);
      setError(false);
      try {
        const payload = await listNotifications({
          unread_only: nextUnreadOnly,
          limit: PAGE_SIZE,
          offset: nextOffset,
        });
        setItems(payload.items);
        setTotal(payload.total);
        setOffset(nextOffset);
        setSelected(new Set());
      } catch {
        setError(true);
      } finally {
        setLoading(false);
      }
    },
    [offset, unreadOnly],
  );

  useEffect(() => {
    void load(0, unreadOnly);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unreadOnly]);

  const refresh = useCallback(() => void load(), [load]);

  const markRead = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      try {
        await markNotificationsRead(ids);
        pushToast("success", "Отмечено как прочитанное.");
        refresh();
      } catch {
        pushToast("danger", "Не удалось отметить прочитанным.");
      }
    },
    [pushToast, refresh],
  );

  const dismiss = useCallback(
    async (ids: string[]) => {
      if (ids.length === 0) return;
      try {
        await dismissNotifications(ids);
        pushToast("success", "Скрыто из списка.");
        refresh();
      } catch {
        pushToast("danger", "Не удалось скрыть уведомления.");
      }
    },
    [pushToast, refresh],
  );

  const openItem = useCallback(
    async (item: AppNotification) => {
      if (!item.object_type || !item.object_id) {
        await markRead([item.id]);
        return;
      }
      setResolving(item.id);
      try {
        const resolved: NotificationResolve = await resolveNotification(item.id);
        if (resolved.allowed && resolved.object_type === "candidate" && resolved.object_id) {
          await markRead([item.id]);
          onOpenCandidate(resolved.object_id);
        } else if (!resolved.allowed) {
          pushToast("info", "Доступ к объекту больше не доступен — права перепроверены сервером.");
        }
      } catch {
        pushToast("danger", "Не удалось проверить доступ к объекту.");
      } finally {
        setResolving(null);
      }
    },
    [markRead, onOpenCandidate, pushToast],
  );

  const showDelivery = useCallback(
    async (item: AppNotification) => {
      try {
        const info = await notificationDelivery(item.id);
        setDelivery({ item, info });
      } catch {
        pushToast("danger", "История отправки недоступна.");
      }
    },
    [pushToast],
  );

  const toggleSelect = useCallback((id: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectedIds = useMemo(() => [...selected], [selected]);

  const unreadIds = useMemo(() => items.filter((i) => !i.read_at).map((i) => i.id), [items]);

  return (
    <div className="notif-page">
      <div className="notif-toolbar" aria-label="Действия с уведомлениями">
        <button
          type="button"
          className={`toggle-chip ${unreadOnly ? "" : ""}`}
          aria-pressed={unreadOnly}
          onClick={() => setUnreadOnly((value) => !value)}
        >
          Только непрочитанные
        </button>
        <Button variant="secondary" icon="check-circle" onClick={() => void markRead(unreadIds)}>
          Прочитать всё на странице
        </Button>
        <Button variant="secondary" icon="trash" onClick={() => void dismiss(unreadIds)}>
          Скрыть прочитанные
        </Button>
      </div>

      {selectedIds.length > 0 && (
        <div className="notif-bulk-bar">
          <span>Выбрано: {selectedIds.length}</span>
          <Button variant="secondary" onClick={() => void markRead(selectedIds)}>
            Прочитать
          </Button>
          <Button variant="secondary" onClick={() => void dismiss(selectedIds)}>
            Скрыть
          </Button>
        </div>
      )}

      <div aria-live="polite">
        {loading ? (
          <SkeletonRows rows={6} columns={4} />
        ) : error ? (
          <ErrorState onRetry={refresh} />
        ) : items.length === 0 ? (
          <EmptyState
            icon="bell"
            title="Уведомлений нет"
            description={
              unreadOnly
                ? "Непрочитанных уведомлений нет — всё спокойно."
                : "Когда появятся события, напоминания и системные сообщения, они будут здесь."
            }
          />
        ) : (
          <ul className="notif-list">
            {items.map((item) => (
              <li
                key={item.id}
                className={`notif-card ${item.read_at ? "" : "is-unread"}`}
              >
                <label className="notif-card-actions">
                  <input
                    type="checkbox"
                    checked={selected.has(item.id)}
                    onChange={() => toggleSelect(item.id)}
                    aria-label={`Выбрать уведомление: ${item.title}`}
                  />
                </label>
                <div className="notif-card-main">
                  <h3 className="notif-card-title">{item.title}</h3>
                  {item.body && <p className="notif-card-body">{item.body}</p>}
                  <div className="notif-card-meta">
                    <span>{formatFull(item.created_at)}</span>
                    {item.read_at ? <span>прочитано</span> : <span>не прочитано</span>}
                    <span>тип: {item.type}</span>
                  </div>
                </div>
                <div className="notif-card-actions">
                  {item.object_type && (
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={resolving === item.id}
                      onClick={() => void openItem(item)}
                    >
                      Открыть
                    </Button>
                  )}
                  {!item.read_at && (
                    <Button variant="secondary" size="sm" onClick={() => void markRead([item.id])}>
                      Прочитано
                    </Button>
                  )}
                  <Button variant="ghost" size="sm" onClick={() => void showDelivery(item)}>
                    Доставка
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => void dismiss([item.id])}>
                    Скрыть
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="notif-pager">
        <Button
          variant="secondary"
          disabled={offset === 0 || loading}
          onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
        >
          Назад
        </Button>
        <span>
          {total === 0 ? 0 : offset + 1}–{Math.min(total, offset + PAGE_SIZE)} из {total}
        </span>
        <Button
          variant="secondary"
          disabled={offset + PAGE_SIZE >= total || loading}
          onClick={() => void load(offset + PAGE_SIZE)}
        >
          Вперёд
        </Button>
      </div>

      <Modal
          open={delivery !== null}
          title="История отправки"
          onClose={() => setDelivery(null)}
          footer={
            <Button variant="primary" onClick={() => setDelivery(null)}>
              Закрыть
            </Button>
          }
        >
          {delivery && (
            <div className="pref-card">
              <p>
                <strong>{delivery.item.title}</strong>
              </p>
              <p>
                Статус: {DELIVERY_LABELS[delivery.info.status] ?? delivery.info.status} (канал{" "}
                {delivery.info.channel})
              </p>
              <p>Попыток: {delivery.info.attempts}</p>
              {delivery.info.provider_message_id && (
                <p>ID сообщения провайдера (Message-ID): {delivery.info.provider_message_id}</p>
              )}
              {delivery.info.scheduled_at && (
                <p>Запланировано: {formatFull(delivery.info.scheduled_at)}</p>
              )}
              {delivery.info.scheduled_at_effective && (
                <p>
                  Фактически разрешено к отправке:{" "}
                  {formatFull(delivery.info.scheduled_at_effective)}
                </p>
              )}
              {delivery.info.accepted_at && (
                <p>Принято провайдером: {formatFull(delivery.info.accepted_at)}</p>
              )}
              {delivery.info.delivered_at && (
                <p>Доставлено: {formatFull(delivery.info.delivered_at)}</p>
              )}
              {delivery.info.error_class && (
                <p>Класс ошибки: {delivery.info.error_class} (подробности в истории попыток)</p>
              )}
              <p>История попыток неизменяема и не содержит персональных данных.</p>
            </div>
          )}
        </Modal>
    </div>
  );
}
