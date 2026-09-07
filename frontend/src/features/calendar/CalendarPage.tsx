import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  listEvents,
  listHrUsers,
  updateEvent,
} from "../../api";
import { Button, IconButton } from "../../design-system/components/Button";
import { Field, SelectInput } from "../../design-system/components/Field";
import { EmptyState, ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import { Icon } from "../../design-system/icons/Icon";
import {
  EVENT_STATUS_LABELS,
  EVENT_TYPE_LABELS,
  type CalendarEvent,
  type CalendarEventStatus,
  type CalendarEventType,
  type User,
  type UserListItem,
} from "../../types";
import { EventFormModal } from "./EventFormModal";
import { formatDayLabel, formatTime, formatWeekRange, startOfWeek } from "./time";
import "./calendar.css";

const HOURS = Array.from({ length: 10 }, (_, index) => 8 + index); // 8:00–17:00
const PANEL_SIZE = 5;

interface CalendarPageProps {
  user: User;
  /** Cross-section navigation to the candidate card. */
  onOpenCandidate: (id: string) => void;
}

/** Production calendar: week grid + server-driven reminder/upcoming/overdue
 * panels. All data comes from GET /events with server-side filters. */
export default function CalendarPage({ user, onOpenCandidate }: CalendarPageProps) {
  const { pushToast } = useToast();
  const canSeeAll = user.role !== "hr";

  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  const [typeFilter, setTypeFilter] = useState<CalendarEventType | "">("");
  const [statusFilter, setStatusFilter] = useState<CalendarEventStatus | "">("");
  const [ownerFilter, setOwnerFilter] = useState("");
  const [directory, setDirectory] = useState<UserListItem[]>([]);

  const [grid, setGrid] = useState<CalendarEvent[]>([]);
  const [gridLoading, setGridLoading] = useState(true);
  const [gridError, setGridError] = useState<string | null>(null);
  const [upcoming, setUpcoming] = useState<CalendarEvent[]>([]);
  const [overdue, setOverdue] = useState<CalendarEvent[]>([]);
  const [reminders, setReminders] = useState<CalendarEvent[]>([]);
  const [panelsLoading, setPanelsLoading] = useState(true);
  const [panelsError, setPanelsError] = useState<string | null>(null);

  const [modal, setModal] = useState<{ event: CalendarEvent | null } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const [busyEventId, setBusyEventId] = useState<string | null>(null);

  const days = useMemo(() => {
    return Array.from({ length: 5 }, (_, index) => {
      const date = new Date(weekStart);
      date.setDate(date.getDate() + index);
      return date;
    });
  }, [weekStart]);

  const periodFrom = useMemo(() => weekStart.toISOString(), [weekStart]);
  const periodTo = useMemo(() => {
    const end = new Date(weekStart);
    end.setDate(end.getDate() + 5);
    return end.toISOString();
  }, [weekStart]);

  useEffect(() => {
    if (!canSeeAll) return;
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [canSeeAll]);

  const loadGrid = useCallback(async () => {
    setGridLoading(true);
    setGridError(null);
    try {
      const page = await listEvents({
        from: periodFrom,
        to: periodTo,
        type: typeFilter || undefined,
        status: statusFilter || undefined,
        owner_id: canSeeAll && ownerFilter ? ownerFilter : undefined,
        sort: "starts_at",
        direction: "asc",
        limit: 100,
      });
      setGrid(page.items);
    } catch (caught) {
      setGridError(caught instanceof ApiError ? caught.message : "Не удалось загрузить календарь.");
    } finally {
      setGridLoading(false);
    }
  }, [periodFrom, periodTo, typeFilter, statusFilter, ownerFilter, canSeeAll]);

  const loadPanels = useCallback(async () => {
    setPanelsLoading(true);
    setPanelsError(null);
    const now = new Date().toISOString();
    try {
      const [upcomingPage, overduePage, remindUpcoming, remindOverdue] = await Promise.all([
        listEvents({ from: now, status: "scheduled", sort: "starts_at", direction: "asc", limit: PANEL_SIZE }),
        listEvents({ to: now, status: "scheduled", sort: "starts_at", direction: "asc", limit: PANEL_SIZE }),
        listEvents({ remind_from: now, status: "scheduled", sort: "starts_at", direction: "asc", limit: PANEL_SIZE }),
        listEvents({ remind_to: now, status: "scheduled", sort: "starts_at", direction: "asc", limit: PANEL_SIZE }),
      ]);
      setUpcoming(upcomingPage.items);
      setOverdue(overduePage.items);
      setReminders([...remindOverdue.items, ...remindUpcoming.items]);
    } catch (caught) {
      setPanelsError(
        caught instanceof ApiError ? caught.message : "Не удалось загрузить сводку событий."
      );
    } finally {
      setPanelsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadGrid();
  }, [loadGrid, reloadTick]);

  useEffect(() => {
    void loadPanels();
  }, [loadPanels, reloadTick]);

  const byDayAndHour = useMemo(() => {
    const buckets: Record<string, CalendarEvent[]> = {};
    for (const event of grid) {
      const date = new Date(event.starts_at);
      const hour = date.getHours();
      const day = (date.getDay() + 6) % 7;
      if (day < 0 || day > 4) continue;
      const key = `${day}:${hour}`;
      buckets[key] = buckets[key] ?? [];
      buckets[key].push(event);
    }
    for (const key of Object.keys(buckets)) {
      buckets[key].sort((a, b) => a.starts_at.localeCompare(b.starts_at));
    }
    return buckets;
  }, [grid]);

  const quickComplete = async (event: CalendarEvent) => {
    if (busyEventId) return;
    setBusyEventId(event.id);
    try {
      const updated = await updateEvent(event.id, {
        expected_version: event.version,
        status: "completed",
      });
      pushToast("success", `Событие «${updated.title}» выполнено.`);
      setReloadTick((tick) => tick + 1);
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось выполнить событие."
      );
    } finally {
      setBusyEventId(null);
    }
  };

  const changeWeek = (offset: number) => {
    const next = new Date(weekStart);
    next.setDate(next.getDate() + offset * 7);
    setWeekStart(next);
  };

  const now = new Date();
  const isOverdue = (event: CalendarEvent) =>
    event.status === "scheduled" && new Date(event.starts_at) < now;

  return (
    <div className="calendar-page">
      <div className="calendar-toolbar">
        <div className="calendar-nav">
          <IconButton
            icon="chevron-left"
            label="Предыдущая неделя"
            onClick={() => changeWeek(-1)}
          />
          <Button variant="secondary" size="sm" onClick={() => setWeekStart(startOfWeek(new Date()))}>
            Сегодня
          </Button>
          <IconButton icon="chevron-right" label="Следующая неделя" onClick={() => changeWeek(1)} />
          <span className="calendar-range" aria-live="polite">
            {formatWeekRange(weekStart)}
          </span>
        </div>

        <div className="calendar-filters">
          <Field label="Тип">
            {(id) => (
              <SelectInput
                id={id}
                value={typeFilter}
                onChange={(event) => setTypeFilter(event.target.value as CalendarEventType | "")}
              >
                <option value="">Все типы</option>
                {(Object.keys(EVENT_TYPE_LABELS) as CalendarEventType[]).map((item) => (
                  <option key={item} value={item}>
                    {EVENT_TYPE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Состояние">
            {(id) => (
              <SelectInput
                id={id}
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as CalendarEventStatus | "")}
              >
                <option value="">Все</option>
                {(Object.keys(EVENT_STATUS_LABELS) as CalendarEventStatus[]).map((item) => (
                  <option key={item} value={item}>
                    {EVENT_STATUS_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          {canSeeAll && (
            <Field label="Ответственный">
              {(id) => (
                <SelectInput
                  id={id}
                  value={ownerFilter}
                  onChange={(event) => setOwnerFilter(event.target.value)}
                >
                  <option value="">Все HR</option>
                  {directory.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.full_name || item.username}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          )}
          <Button icon="plus" onClick={() => setCreateOpen(true)}>
            Добавить событие
          </Button>
        </div>
      </div>

      <div className="calendar-layout">
        <section className="calendar-grid-section" aria-label="Недельная сетка событий">
          {gridLoading && <SkeletonRows rows={6} columns={5} />}
          {!gridLoading && gridError && <ErrorState onRetry={() => void loadGrid()} />}
          {!gridLoading && !gridError && grid.length === 0 && (
            <EmptyState
              icon="calendar"
              title="На этой неделе событий нет"
              description="Добавьте событие или измените фильтры."
            />
          )}
          {!gridLoading && !gridError && grid.length > 0 && (
            <div className="calendar-table-wrap">
              <table className="calendar-table">
                <thead>
                  <tr>
                    <th scope="col" className="calendar-hour-col">
                      <span className="sr-only">Время</span>
                    </th>
                    {days.map((day) => (
                      <th scope="col" key={day.toISOString()}>
                        {formatDayLabel(day)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {HOURS.map((hour) => (
                    <tr key={hour}>
                      <th scope="row" className="calendar-hour-col">
                        {hour}:00
                      </th>
                      {days.map((day, dayIndex) => {
                        const events = byDayAndHour[`${dayIndex}:${hour}`] ?? [];
                        return (
                          <td key={day.toISOString()} className="calendar-cell">
                            {events.map((event) => (
                              <button
                                key={event.id}
                                type="button"
                                className={`calendar-chip calendar-chip-${event.type} calendar-chip-${event.status}`}
                                onClick={() => setModal({ event })}
                              >
                                <span className="calendar-chip-time">{formatTime(event.starts_at)}</span>
                                <span className="calendar-chip-title">{event.title}</span>
                                <span className="calendar-chip-status">
                                  {event.status !== "scheduled" && (
                                    <Icon
                                      name={event.status === "completed" ? "check" : "clock"}
                                      size={11}
                                    />
                                  )}
                                  {isOverdue(event) && <Icon name="alert-triangle" size={11} />}
                                  {event.remind_at && <Icon name="bell" size={11} />}
                                </span>
                              </button>
                            ))}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <aside className="calendar-panels" aria-label="Сводка событий">
          <EventPanel
            title="Просроченные"
            icon="alert-triangle"
            loading={panelsLoading}
            error={panelsError}
            events={overdue}
            busyEventId={busyEventId}
            onOpen={(event) => setModal({ event })}
            onComplete={(event) => void quickComplete(event)}
          />
          <EventPanel
            title="Ближайшие"
            icon="clock"
            loading={panelsLoading}
            error={panelsError}
            events={upcoming}
            busyEventId={busyEventId}
            onOpen={(event) => setModal({ event })}
            onComplete={(event) => void quickComplete(event)}
          />
          <EventPanel
            title="Напоминания"
            icon="bell"
            loading={panelsLoading}
            error={panelsError}
            events={reminders}
            busyEventId={busyEventId}
            onOpen={(event) => setModal({ event })}
            onComplete={(event) => void quickComplete(event)}
          />
        </aside>
      </div>

      <EventFormModal
        open={createOpen}
        user={user}
        onClose={() => setCreateOpen(false)}
        onSaved={() => {
          setCreateOpen(false);
          setReloadTick((tick) => tick + 1);
        }}
        onOpenCandidate={onOpenCandidate}
      />

      {modal && (
        <EventFormModal
          open
          user={user}
          event={modal.event}
          onClose={() => setModal(null)}
          onSaved={() => {
            setModal(null);
            setReloadTick((tick) => tick + 1);
          }}
          onOpenCandidate={onOpenCandidate}
        />
      )}
    </div>
  );
}

interface EventPanelProps {
  title: string;
  icon: "alert-triangle" | "clock" | "bell";
  loading: boolean;
  error: string | null;
  events: CalendarEvent[];
  busyEventId: string | null;
  onOpen: (event: CalendarEvent) => void;
  onComplete: (event: CalendarEvent) => void;
}

function EventPanel({
  title,
  icon,
  loading,
  error,
  events,
  busyEventId,
  onOpen,
  onComplete,
}: EventPanelProps) {
  return (
    <section className="calendar-panel" aria-label={title}>
      <h3 className="calendar-panel-title">
        <Icon name={icon} size={14} />
        {title}
        <span className="tab-count">{events.length}</span>
      </h3>
      {loading && <p className="muted-text">Загрузка…</p>}
      {!loading && error && <p className="muted-text">Недоступно</p>}
      {!loading && !error && events.length === 0 && (
        <p className="muted-text">Ничего нет.</p>
      )}
      {!loading && !error && events.length > 0 && (
        <ul className="calendar-panel-list">
          {events.map((event) => (
            <li key={event.id} className="calendar-panel-item">
              <button type="button" className="calendar-panel-event" onClick={() => onOpen(event)}>
                <span className="calendar-panel-time">{formatTime(event.starts_at)}</span>
                <span className="calendar-panel-event-title">{event.title}</span>
                <span className="calendar-panel-candidate">{event.candidate_full_name}</span>
              </button>
              <Button
                variant="secondary"
                size="sm"
                disabled={busyEventId === event.id}
                onClick={() => onComplete(event)}
                aria-label={`Выполнить: ${event.title}`}
              >
                Выполнено
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
