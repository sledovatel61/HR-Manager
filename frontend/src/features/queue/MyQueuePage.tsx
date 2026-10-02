import { useCallback, useEffect, useMemo, useState } from "react";
import { listCandidates, listEvents, unreadCount } from "../../api";
import { Button } from "../../design-system/components/Button";
import { ErrorState } from "../../design-system/components/StateViews";
import { Icon } from "../../design-system/icons/Icon";
import {
  CANDIDATE_STAGE_ORDER,
  EVENT_TYPE_LABELS,
  STAGE_LABELS,
  type CalendarEvent,
  type Candidate,
  type CandidateStage,
} from "../../types";
import "./queue.css";

/**
 * «Моя очередь» — персональная сводка HR (требование заказчика §7.2).
 *
 * Данные берутся только из существующих endpoint'ов (GET /candidates,
 * GET /events, GET /notifications/unread-count); новых полей и API не
 * заведено. Все показатели считаются на клиенте из уже загруженных данных —
 * сервер остаётся источником фактов, сводка ничего не «додумывает».
 *
 * Экран не меняет функциональность: под сводкой продолжает работать обычный
 * список кандидатов в режиме «queue».
 */

const DIRECTORY_LIMIT = 100;

/** Этапы, на которых кандидат уже не «в работе». */
const CLOSED_STAGES: CandidateStage[] = ["hired", "started", "probation", "fired", "rejected"];
const WORK_STAGES = CANDIDATE_STAGE_ORDER.filter((stage) => !CLOSED_STAGES.includes(stage));

/** Сколько дней без движения считаем застоем. */
const STUCK_DAYS = 3;

/** Цвет полосы воронки — категориальные токены направления (--cat-*). */
const STAGE_CAT: Record<CandidateStage, string> = {
  new: "info",
  contacted: "info",
  reached: "indigo",
  interview_scheduled: "violet",
  interview_done: "violet",
  offer: "warning",
  hired: "success",
  started: "success",
  probation: "success",
  fired: "neutral",
  rejected: "neutral",
};

interface QueueData {
  candidates: Candidate[];
  events: CalendarEvent[];
  unread: number;
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

function isWithinDays(iso: string | null, from: Date, days: number): boolean {
  if (!iso) return false;
  const value = new Date(iso).getTime();
  if (Number.isNaN(value)) return false;
  return value >= from.getTime() && value < from.getTime() + days * MS_PER_DAY;
}

function formatDayTime(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

interface MyQueuePageProps {
  /** Открыть карточку кандидата (cross-section hand-off). */
  onOpenCandidate?: (id: string) => void;
  /** Перейти в центр уведомлений (раздел шелла). */
  onOpenNotifications?: () => void;
}

export default function MyQueuePage({ onOpenCandidate, onOpenNotifications }: MyQueuePageProps) {
  const [data, setData] = useState<QueueData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const today = startOfToday();
    const week = new Date(today.getTime() + 7 * MS_PER_DAY);
    Promise.all([
      listCandidates({ limit: DIRECTORY_LIMIT, sort: "updated_at", direction: "desc" }),
      listEvents({
        from: today.toISOString(),
        to: week.toISOString(),
        sort: "starts_at",
        direction: "asc",
        limit: 20,
      }),
      unreadCount(),
    ])
      .then(([candidates, events, unread]) => {
        if (cancelled) return;
        setData({ candidates: candidates.items, events: events.items, unread: unread.count });
      })
      .catch(() => {
        if (cancelled) return;
        setError("Не удалось загрузить сводку очереди.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  const reload = useCallback(() => setReloadTick((tick) => tick + 1), []);

  const summary = useMemo(() => {
    const candidates = data?.candidates ?? [];
    const today = startOfToday();
    const dayAgo = new Date(Date.now() - MS_PER_DAY);
    const inWork = candidates.filter((item) => !CLOSED_STAGES.includes(item.stage));
    const fresh = candidates.filter((item) => new Date(item.created_at) >= dayAgo);
    const stuck = inWork.filter(
      (item) => new Date(item.updated_at).getTime() < Date.now() - STUCK_DAYS * MS_PER_DAY,
    );
    const starts = candidates.filter((item) => isWithinDays(item.start_date, today, 7));
    const byStage = WORK_STAGES.map((stage) => ({
      stage,
      label: STAGE_LABELS[stage],
      count: candidates.filter((item) => item.stage === stage).length,
      cat: STAGE_CAT[stage],
    }));
    const maxStage = Math.max(1, ...byStage.map((row) => row.count));
    return { total: candidates.length, inWork: inWork.length, fresh: fresh.length, stuck, starts, byStage, maxStage };
  }, [data]);

  const events = data?.events ?? [];

  if (loading && !data) {
    return (
      <section className="queue-page" aria-label="Моя очередь">
        <div className="queue-kpis" aria-hidden="true">
          {[0, 1, 2, 3].map((index) => (
            <span key={index} className="queue-skeleton" />
          ))}
        </div>
      </section>
    );
  }

  if (error && !data) {
    return (
      <section className="queue-page" aria-label="Моя очередь">
        <ErrorState onRetry={reload} />
      </section>
    );
  }

  return (
    <section className="queue-page" aria-label="Моя очередь">
      <header className="queue-head">
        <div>
          <p className="queue-eyebrow">Сводка на {new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}</p>
          <h2 className="queue-title">Моя очередь</h2>
        </div>
        <Button variant="secondary" size="sm" icon="loader" onClick={reload} disabled={loading}>
          Обновить
        </Button>
      </header>

      <dl className="queue-kpis">
        <div className="queue-kpi">
          <dt className="queue-kpi-label">В работе</dt>
          <dd className="queue-kpi-value">{summary.inWork}</dd>
        </div>
        <div className="queue-kpi">
          <dt className="queue-kpi-label">Новые за сутки</dt>
          <dd className="queue-kpi-value">{summary.fresh}</dd>
        </div>
        <div className="queue-kpi queue-kpi-warning">
          <dt className="queue-kpi-label">Без движения {STUCK_DAYS}+ дня</dt>
          <dd className="queue-kpi-value">{summary.stuck.length}</dd>
        </div>
        <div className="queue-kpi">
          <dt className="queue-kpi-label">Выходы на неделе</dt>
          <dd className="queue-kpi-value">{summary.starts.length}</dd>
        </div>
      </dl>

      <div className="queue-grid">
        <article className="queue-card">
          <h3 className="queue-card-title">Воронка моих кандидатов</h3>
          <ul className="queue-funnel">
            {summary.byStage.map((row) => (
              <li key={row.stage} className="queue-funnel-row">
                <span className="queue-funnel-label">{row.label}</span>
                <span className="queue-funnel-track">
                  <span
                    className="queue-funnel-fill"
                    style={{
                      width: `${Math.round((row.count / summary.maxStage) * 100)}%`,
                      background: `linear-gradient(135deg, var(--cat-${row.cat}-1), var(--cat-${row.cat}-2))`,
                    }}
                  />
                </span>
                <span className="queue-funnel-count">{row.count}</span>
              </li>
            ))}
          </ul>
        </article>

        <article className="queue-card">
          <h3 className="queue-card-title">Ближайшие события</h3>
          {events.length === 0 ? (
            <p className="queue-empty">На ближайшие 7 дней событий нет.</p>
          ) : (
            <ul className="queue-events">
              {events.slice(0, 6).map((event) => (
                <li key={event.id} className="queue-event">
                  <span className="queue-event-time">{formatDayTime(event.starts_at)}</span>
                  <span className="queue-event-body">
                    <button
                      type="button"
                      className="queue-event-name"
                      onClick={() => onOpenCandidate?.(event.candidate_id)}
                    >
                      {event.candidate_full_name}
                    </button>
                    <span className="queue-event-meta">
                      {EVENT_TYPE_LABELS[event.type]} · {event.title}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </article>

        <article className="queue-card">
          <h3 className="queue-card-title">Требуют внимания</h3>
          {summary.stuck.length === 0 ? (
            <p className="queue-empty">Все кандидаты в движении — просроченных нет.</p>
          ) : (
            <ul className="queue-stuck">
              {summary.stuck.slice(0, 5).map((item) => (
                <li key={item.id} className="queue-stuck-row">
                  <button
                    type="button"
                    className="queue-event-name"
                    onClick={() => onOpenCandidate?.(item.id)}
                  >
                    {item.full_name}
                  </button>
                  <span className="queue-event-meta">
                    {item.position || "без должности"} · {STAGE_LABELS[item.stage]}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {data && data.unread > 0 && onOpenNotifications && (
            <button type="button" className="queue-link" onClick={onOpenNotifications}>
              <Icon name="bell" size={14} />
              Непрочитанных уведомлений: {data.unread}
            </button>
          )}
        </article>
      </div>
    </section>
  );
}
