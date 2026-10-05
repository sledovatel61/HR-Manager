import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getQueueSummary, unreadCount } from "../../api";
import { Button } from "../../design-system/components/Button";
import { ErrorState } from "../../design-system/components/StateViews";
import { Icon } from "../../design-system/icons/Icon";
import {
  CANDIDATE_STAGE_ORDER,
  EVENT_TYPE_LABELS,
  STAGE_LABELS,
  type CandidateStage,
  type QueueStuckCandidate,
  type QueueSummary,
  type QueueUpcomingEvent,
} from "../../types";
import "./queue.css";

/**
 * «Моя очередь» — персональная сводка (требование заказчика §7.2).
 *
 * Показатели считает сервер: `GET /candidates/queue/summary` агрегирует **всю**
 * личную область видимости и отдаёт числа плюс две ограниченные выборки для
 * карточек. Раньше экран скачивал 100 самых свежих по `updated_at` кандидатов и
 * считал всё в браузере — но кандидат, который ждёт три недели, по определению
 * не попадает в сотню самых свежих, поэтому «Без движения» и «Требуют внимания»
 * систематически занижались, а плитки KPI выдавали страницу за всю очередь.
 *
 * Область видимости личная для всех ролей (HR, руководитель, администратор,
 * пилот): раздел называется «Моя очередь», а не «Общая база».
 *
 * Завершённые и отменённые события сервер в выборку не включает: выполненное
 * событие — не то, что требует действия.
 *
 * Экран не меняет функциональность: под сводкой продолжает работать обычный
 * список кандидатов в режиме «queue» (с той же персональной областью).
 */

/** Этапы, на которых кандидат уже не «в работе» (зеркало контракта сервера). */
const CLOSED_STAGES: CandidateStage[] = ["hired", "started", "probation", "fired", "rejected"];
const WORK_STAGES = CANDIDATE_STAGE_ORDER.filter((stage) => !CLOSED_STAGES.includes(stage));

/** Сколько кандидатов показываем в карточке «Требуют внимания». */
const STUCK_CARD_LIMIT = 5;
/** Сколько событий показываем в карточке «Ближайшие события». */
const EVENTS_CARD_LIMIT = 6;

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
  summary: QueueSummary;
  unread: number;
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
  /** Сводка уже была загружена, но обновление не удалось: показываем отметку
   *  об устаревших данных, а не молча старые цифры. */
  const [stale, setStale] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const hasData = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([getQueueSummary(), unreadCount()])
      .then(([summary, unread]) => {
        if (cancelled) return;
        hasData.current = true;
        setData({ summary, unread: unread.count });
        setStale(false);
      })
      .catch(() => {
        if (cancelled) return;
        // Первая загрузка — состояние ошибки; повторная — отметка устаревших
        // данных (пользователь видит, что цифры могут быть неактуальными).
        if (hasData.current) {
          setStale(true);
        } else {
          setError("Не удалось загрузить сводку очереди.");
        }
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
    const source = data?.summary;
    const counts = new Map(source?.by_stage.map((row) => [row.stage, row.count]) ?? []);
    const byStage = WORK_STAGES.map((stage) => ({
      stage,
      label: STAGE_LABELS[stage],
      count: counts.get(stage) ?? 0,
      cat: STAGE_CAT[stage],
    }));
    const maxStage = Math.max(1, ...byStage.map((row) => row.count));
    return {
      total: source?.total ?? 0,
      inWork: source?.in_work ?? 0,
      fresh: source?.fresh ?? 0,
      stuck: source?.stuck ?? 0,
      starts: source?.starts ?? 0,
      stuckSample: source?.stuck_sample ?? [],
      // Подписи плиток берут фактические окна сервера, а не локальные константы.
      stuckDays: source?.stuck_days ?? 3,
      horizonDays: source?.horizon_days ?? 7,
      byStage,
      maxStage,
    };
  }, [data]);

  const events: QueueUpcomingEvent[] = data?.summary.upcoming_events ?? [];
  const eventsTotal = data?.summary.upcoming_events_total ?? 0;
  const eventsTruncated = data?.summary.upcoming_events_truncated ?? false;
  const stuckSample: QueueStuckCandidate[] = summary.stuckSample;
  const stuckTruncated = data?.summary.stuck_sample_truncated ?? false;

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

      {stale && (
        <div className="queue-stale" role="status">
          <span>
            Не удалось обновить сводку — показаны данные на{" "}
            {data ? formatDayTime(data.summary.generated_at) : "—"}. Цифры могут быть
            неактуальными.
          </span>
          <Button variant="secondary" size="sm" onClick={reload} disabled={loading}>
            Повторить
          </Button>
        </div>
      )}

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
          <dt className="queue-kpi-label">Без движения {summary.stuckDays}+ дня</dt>
          <dd className="queue-kpi-value">{summary.stuck}</dd>
        </div>
        <div className="queue-kpi">
          <dt className="queue-kpi-label">Выходы на {summary.horizonDays} дней</dt>
          <dd className="queue-kpi-value">{summary.starts}</dd>
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
            <p className="queue-empty">На ближайшие {summary.horizonDays} дней событий нет.</p>
          ) : (
            <ul className="queue-events">
              {events.slice(0, EVENTS_CARD_LIMIT).map((event) => (
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
          {eventsTruncated && (
            <p className="queue-card-more">
              Показаны первые {Math.min(events.length, EVENTS_CARD_LIMIT)} из {eventsTotal} —
              полный список в разделе «Календарь».
            </p>
          )}
        </article>

        <article className="queue-card">
          <h3 className="queue-card-title">Требуют внимания</h3>
          {stuckSample.length === 0 ? (
            <p className="queue-empty">Все кандидаты в движении — просроченных нет.</p>
          ) : (
            <ul className="queue-stuck">
              {stuckSample.slice(0, STUCK_CARD_LIMIT).map((item) => (
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
          {stuckTruncated && (
            <p className="queue-card-more">
              Показаны {Math.min(stuckSample.length, STUCK_CARD_LIMIT)} из {summary.stuck} —
              это самые давние, остальные видны в списке ниже.
            </p>
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
