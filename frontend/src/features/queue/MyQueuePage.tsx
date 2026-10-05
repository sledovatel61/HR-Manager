import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, getQueueDashboard, listHrUsers } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import {
  EmptyState,
  ErrorState,
  PermissionDeniedState,
} from "../../design-system/components/StateViews";
import { Icon } from "../../design-system/icons/Icon";
import {
  CANDIDATE_STAGE_ORDER,
  EVENT_TYPE_LABELS,
  SOURCE_LABELS,
  STAGE_LABELS,
  type CandidateSource,
  type CandidateStage,
  type QueueDashboard,
  type QueueDashboardPeriodKey,
  type QueueStuckCandidate,
  type QueueUpcomingEvent,
  type User,
  type UserListItem,
} from "../../types";
import { usePositionOptions } from "../candidates/usePositionOptions";
import { QueueBarChart, QueueChart } from "./QueueCharts";
import { formatDays, formatInt, formatPercent } from "./chartFormat";
import "./queue.css";

/**
 * «Моя очередь» — аналитическая сводка дня (требование заказчика §7.2).
 *
 * Все числа считает сервер: `GET /candidates/queue/dashboard` агрегирует
 * **всю** личную область видимости и отдаёт KPI, ряды по бакетам периода,
 * источники и персональные блоки. Браузер ничего не считает по страницам:
 * раньше экран скачивал 100 самых свежих по `updated_at` кандидатов — но
 * кандидат, который ждёт три недели, по определению не попадает в сотню самых
 * свежих, поэтому «Без движения» и «Требуют внимания» систематически
 * занижались, а плитки KPI выдавали страницу за всю очередь.
 *
 * Область видимости личная для всех ролей (HR, руководитель, администратор,
 * пилот): раздел называется «Моя очередь», а не «Общая база». Руководитель и
 * администратор могут переключиться на коллегу — но это осознанный выбор в
 * фильтрах, а не расширение области по умолчанию.
 *
 * Ни одно число не берётся из макета: 248 / 41,2 % / 18,4 дн. в мокапе —
 * демонстрационные. Метрика, которую модель данных выразить не может
 * (активных вакансий нет как сущности), показывается как «—» с пояснением, а
 * не как ноль и не как выдуманное значение.
 *
 * Экран не меняет функциональность: под сводкой продолжает работать обычный
 * список кандидатов в режиме «queue» (с той же персональной областью).
 */

/** Этапы, на которых кандидат уже не «в работе» (зеркало контракта сервера). */
const CLOSED_STAGES: CandidateStage[] = ["hired", "started", "probation", "fired", "rejected"];
const WORK_STAGES = CANDIDATE_STAGE_ORDER.filter((stage) => !CLOSED_STAGES.includes(stage));

const PERIODS: { key: QueueDashboardPeriodKey; label: string; hint: string }[] = [
  { key: "today", label: "Сегодня", hint: "по часам" },
  { key: "week", label: "Неделя", hint: "по дням" },
  { key: "all", label: "Всё", hint: "по месяцам" },
];

/** Сколько кандидатов показываем в карточке «Требуют внимания». */
const STUCK_CARD_LIMIT = 5;
/** Сколько событий показываем в карточке «Ближайшие события». */
const EVENTS_CARD_LIMIT = 6;
/** Ключ сохранения периода и фильтров (переживает переход в другой раздел). */
const STORAGE_KEY = "hr-manager.queue-dashboard";

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

interface DashboardFilters {
  position: string;
  stage: CandidateStage | "";
  source: CandidateSource | "";
  ownerId: string;
}

const EMPTY_FILTERS: DashboardFilters = { position: "", stage: "", source: "", ownerId: "" };

interface MyQueuePageProps {
  /** Открыть карточку кандидата (cross-section hand-off). */
  onOpenCandidate?: (id: string) => void;
  /** Перейти в центр уведомлений (раздел шелла). */
  onOpenNotifications?: () => void;
  /** Текущий пользователь: только руководитель и администратор видят фильтр
   *  «Ответственный» (HR он не нужен — его область всегда личная). */
  user?: User | null;
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

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function formatDate(iso: string, timezone: string): string {
  try {
    return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short", timeZone: timezone });
  } catch {
    return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
  }
}

/** Период и фильтры переживают перезагрузку и переход в другой раздел. */
function readStored(): { period: QueueDashboardPeriodKey; filters: DashboardFilters } {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { period: "week", filters: EMPTY_FILTERS };
    const parsed = JSON.parse(raw) as Partial<{ period: string; filters: Partial<DashboardFilters> }>;
    const period = PERIODS.some((item) => item.key === parsed.period)
      ? (parsed.period as QueueDashboardPeriodKey)
      : "week";
    return { period, filters: { ...EMPTY_FILTERS, ...(parsed.filters ?? {}) } };
  } catch {
    return { period: "week", filters: EMPTY_FILTERS };
  }
}

function browserTimezone(): string {
  const resolved = new Intl.DateTimeFormat().resolvedOptions().timeZone;
  return resolved && resolved.length > 0 ? resolved : "UTC";
}

export default function MyQueuePage({ onOpenCandidate, onOpenNotifications, user }: MyQueuePageProps) {
  const stored = useRef(readStored()).current;
  const [period, setPeriod] = useState<QueueDashboardPeriodKey>(stored.period);
  const [filters, setFilters] = useState<DashboardFilters>(stored.filters);
  const [showFilters, setShowFilters] = useState(false);
  /** Текстовый фильтр живёт в черновике: запрос уходит по «Применить» или
   *  по потере фокуса, а не на каждое нажатие клавиши. */
  const [draftPosition, setDraftPosition] = useState(stored.filters.position);
  const [data, setData] = useState<QueueDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  /** Сводка уже была загружена, но обновление не удалось: показываем отметку
   *  об устаревших данных, а не молча старые цифры. */
  const [stale, setStale] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const hasData = useRef(false);

  const timezone = useMemo(browserTimezone, []);
  const canSwitchOwner = user?.role === "manager" || user?.role === "admin";
  const [directory, setDirectory] = useState<UserListItem[]>([]);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ period, filters }));
    } catch {
      // Приватный режим / переполненное хранилище — не повод ломать экран.
    }
  }, [period, filters]);

  // Список ответственных нужен только руководителю и администратору: HR
  // лишний запрос не делаем вовсе (область у него всегда личная).
  useEffect(() => {
    if (!canSwitchOwner) return;
    let cancelled = false;
    listHrUsers()
      .then((items) => {
        if (!cancelled) setDirectory(items.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setDirectory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canSwitchOwner]);

  const positionOptions = usePositionOptions({ owner_id: filters.ownerId || undefined });

  const { position, stage, source, ownerId } = filters;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getQueueDashboard({
      period,
      timezone,
      owner_id: ownerId || undefined,
      position: position || undefined,
      stage: stage || undefined,
      source: source || undefined,
    })
      .then((payload) => {
        if (cancelled) return;
        hasData.current = true;
        setData(payload);
        setForbidden(false);
        setStale(false);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        if (cause instanceof ApiError && cause.status === 403) {
          // Нет прав: отдельное состояние, без повторных запросов и без
          // данных предыдущего пользователя на экране.
          setForbidden(true);
          setData(null);
          hasData.current = false;
          return;
        }
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
  }, [period, timezone, ownerId, position, stage, source, reloadTick]);

  const reload = useCallback(() => setReloadTick((tick) => tick + 1), []);
  const resetFilters = useCallback(() => {
    setFilters((current) => ({ ...EMPTY_FILTERS, ownerId: current.ownerId }));
    setDraftPosition("");
  }, []);
  const applyPosition = useCallback(() => {
    setFilters((current) =>
      current.position === draftPosition ? current : { ...current, position: draftPosition },
    );
  }, [draftPosition]);

  const buckets = useMemo(() => data?.period.buckets ?? [], [data]);
  const labels = useMemo(() => buckets.map((bucket) => bucket.label), [buckets]);

  const funnel = useMemo(() => {
    const counts = new Map((data?.funnel ?? []).map((row) => [row.stage, row.count]));
    const rows = WORK_STAGES.map((stage) => ({
      stage,
      label: STAGE_LABELS[stage],
      count: counts.get(stage) ?? 0,
      cat: STAGE_CAT[stage],
    }));
    return { rows, max: Math.max(1, ...rows.map((row) => row.count)) };
  }, [data]);

  const periodLabel = useMemo(() => {
    if (!data) return "";
    const { from, to, timezone: tz } = data.period;
    const end = new Date(new Date(to).getTime() - 1);
    return `${formatDate(from, tz)} — ${formatDate(end.toISOString(), tz)}`;
  }, [data]);

  const kpis = data?.kpis;
  const events: QueueUpcomingEvent[] = data?.upcoming_events ?? [];
  const eventsTotal = data?.upcoming_events_total ?? 0;
  const eventsTruncated = data?.upcoming_events_truncated ?? false;
  const stuckSample: QueueStuckCandidate[] = data?.attention_candidates ?? [];
  const stuckTotal = data?.attention_candidates_total ?? 0;
  const stuckTruncated = data?.attention_truncated ?? false;
  const activeFilters = [position, stage, source].filter(Boolean).length;

  if (forbidden) {
    return (
      <section className="queue-page" aria-label="Моя очередь">
        <PermissionDeniedState />
      </section>
    );
  }

  if (loading && !data) {
    return (
      <section className="queue-page" aria-label="Моя очередь">
        <span className="sr-only">Загружаем сводку очереди…</span>
        <div className="queue-kpis" aria-hidden="true">
          {Array.from({ length: 8 }, (_, index) => (
            <span key={index} className="queue-skeleton" />
          ))}
        </div>
        <div className="queue-dash" aria-hidden="true">
          <span className="queue-skeleton queue-skeleton-chart" />
          <span className="queue-skeleton queue-skeleton-chart" />
          <span className="queue-skeleton queue-skeleton-chart" />
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
          <p className="queue-eyebrow">
            {periodLabel ? `Период: ${periodLabel}` : "Сводка очереди"}
            {data?.period.capped ? " · показаны последние 24 месяца" : ""}
          </p>
          <h2 className="queue-title">Моя очередь</h2>
          <p className="queue-subtitle">
            {data ? (
              <>
                В работе {formatInt(kpis?.in_work ?? 0)} из {formatInt(kpis?.total_candidates ?? 0)} ·{" "}
                {data.scope.personal ? "личная область" : `область: ${data.scope.owner_username}`}
              </>
            ) : (
              "—"
            )}
          </p>
        </div>
        <div className="queue-head-actions">
          <div className="queue-seg" role="group" aria-label="Период сводки">
            {PERIODS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`queue-seg-btn ${item.key === period ? "is-on" : ""}`}
                aria-pressed={item.key === period}
                onClick={() => setPeriod(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <Button
            variant="secondary"
            size="sm"
            icon="filter"
            onClick={() => setShowFilters((value) => !value)}
            aria-expanded={showFilters}
          >
            Фильтры{activeFilters > 0 ? ` (${activeFilters})` : ""}
          </Button>
          <Button variant="secondary" size="sm" icon="loader" onClick={reload} disabled={loading}>
            Обновить
          </Button>
        </div>
      </header>

      {showFilters && (
        <form
          className="queue-filters"
          role="search"
          aria-label="Фильтры сводки"
          onSubmit={(event) => {
            event.preventDefault();
            applyPosition();
          }}
        >
          <Field label="Должность" hint="Как в списке кандидатов">
            {(id, describedBy) => (
              <>
                <TextInput
                  id={id}
                  list="queue-position-options"
                  value={draftPosition}
                  placeholder="Любая"
                  aria-describedby={describedBy}
                  onChange={(event) => setDraftPosition(event.target.value)}
                  onBlur={applyPosition}
                />
                <datalist id="queue-position-options">
                  {positionOptions.positions.map((item) => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
              </>
            )}
          </Field>
          <Field label="Этап">
            {(id) => (
              <SelectInput
                id={id}
                value={stage}
                onChange={(event) =>
                  setFilters((current) => ({
                    ...current,
                    stage: event.target.value as CandidateStage | "",
                  }))
                }
              >
                <option value="">Любой</option>
                {CANDIDATE_STAGE_ORDER.map((item) => (
                  <option key={item} value={item}>
                    {STAGE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Источник">
            {(id) => (
              <SelectInput
                id={id}
                value={source}
                onChange={(event) =>
                  setFilters((current) => ({
                    ...current,
                    source: event.target.value as CandidateSource | "",
                  }))
                }
              >
                <option value="">Любой</option>
                {(Object.keys(SOURCE_LABELS) as CandidateSource[]).map((item) => (
                  <option key={item} value={item}>
                    {SOURCE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          {canSwitchOwner && (
            <Field label="Ответственный" hint="По умолчанию — своя очередь">
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  value={ownerId}
                  aria-describedby={describedBy}
                  onChange={(event) =>
                    setFilters((current) => ({ ...current, ownerId: event.target.value }))
                  }
                >
                  <option value="">Я</option>
                  {directory.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.full_name || item.username}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          )}
          <div className="queue-filters-actions">
            <Button type="submit" variant="primary" size="sm">
              Применить
            </Button>
            <Button variant="ghost" size="sm" onClick={resetFilters}>
              Сбросить
            </Button>
          </div>
          {positionOptions.error && (
            <p className="queue-empty" role="status">
              {positionOptions.error}{" "}
              <Button variant="ghost" size="sm" onClick={positionOptions.reload}>
                Повторить
              </Button>
            </p>
          )}
        </form>
      )}

      {stale && (
        <div className="queue-stale" role="status">
          <span>
            Не удалось обновить сводку — показаны данные на{" "}
            {data ? formatDayTime(data.generated_at) : "—"}. Цифры могут быть неактуальными.
          </span>
          <Button variant="secondary" size="sm" onClick={reload} disabled={loading}>
            Повторить
          </Button>
        </div>
      )}

      <dl className={`queue-kpis ${loading ? "is-refreshing" : ""}`}>
        <KpiCard
          label="Мои задачи"
          value={formatInt(kpis?.my_tasks ?? 0)}
          hint="Активные напоминания, назначенные мне, с сроком в периоде"
        />
        <KpiCard
          label="Просроченные задачи"
          value={formatInt(kpis?.overdue_tasks ?? 0)}
          hint="Активные напоминания со сроком в прошлом"
          tone="warning"
        />
        <KpiCard
          label="Новые отклики"
          value={formatInt(kpis?.new_candidates ?? 0)}
          hint="Кандидатов создано за период"
        />
        <KpiCard
          label="Собеседования"
          value={formatInt(kpis?.interviews ?? 0)}
          hint="Событий типа «собеседование» в периоде (кроме отменённых)"
        />
        <KpiCard
          label="Конверсия в собеседование"
          value={formatPercent(kpis?.interview_conversion.rate ?? null)}
          hint={`Дошли до собеседования: ${formatInt(kpis?.interview_conversion.numerator ?? 0)} из ${formatInt(
            kpis?.interview_conversion.denominator ?? 0,
          )} созданных`}
        />
        <KpiCard
          label="Средний срок найма"
          value={formatDays(kpis?.average_hiring_days.value ?? null)}
          hint={`По ${formatInt(kpis?.average_hiring_days.sample ?? 0)} нанятым: от создания до выхода`}
        />
        <KpiCard
          label="Активные вакансии"
          value={kpis?.active_vacancies === null || kpis?.active_vacancies === undefined ? "—" : formatInt(kpis.active_vacancies)}
          hint={data?.kpi_notes.active_vacancies ?? "Показатель недоступен"}
          unavailable={
            kpis?.active_vacancies === null || kpis?.active_vacancies === undefined
          }
        />
        <KpiCard
          label="Выходы на неделе"
          value={formatInt(kpis?.weekly_exits ?? 0)}
          hint="Запланированных выходов в ближайшие 7 дней"
        />
      </dl>

      <div className="queue-dash">
        <article className="queue-card queue-dash-main">
          <h3 className="queue-card-title">Динамика найма</h3>
          <p className="queue-card-hint">Выходы на работу и средний срок найма по периоду</p>
          <QueueChart
            title="Динамика найма"
            description="Выходы на работу (шт.) и средний срок найма (дн.) по бакетам периода."
            labels={labels}
            emptyText="За период выходов и наймов не было."
            height={260}
            series={[
              {
                key: "exits",
                label: "Выходы",
                unit: "шт.",
                kind: "area",
                tone: "accent",
                axis: "left",
                format: formatInt,
                values: (data?.hiring_dynamics ?? []).map((row) => row.exits),
              },
              {
                key: "days",
                label: "Срок найма",
                unit: "дн.",
                kind: "line",
                tone: "warning",
                axis: "right",
                format: formatDays,
                values: (data?.hiring_dynamics ?? []).map((row) => row.avg_hiring_days),
              },
            ]}
          />
        </article>

        <article className="queue-card queue-dash-side">
          <h3 className="queue-card-title">Источники</h3>
          <p className="queue-card-hint">Новые кандидаты за период по источникам</p>
          <QueueBarChart
            title="Источники"
            description="Новые кандидаты за период по источникам."
            items={(data?.sources ?? []).map((row) => ({
              key: row.source,
              label: row.label,
              value: row.count,
            }))}
            emptyText="За период новых кандидатов не было."
          />
        </article>

        <article className="queue-card queue-dash-feed">
          <h3 className="queue-card-title">Новые кандидаты</h3>
          <p className="queue-card-hint">Сколько кандидатов создано в каждом бакете периода</p>
          <QueueChart
            title="Новые кандидаты"
            description="Количество созданных кандидатов по бакетам периода."
            labels={labels}
            emptyText="За период новых кандидатов не было."
            height={200}
            series={[
              {
                key: "created",
                label: "Создано",
                kind: "area",
                tone: "info",
                format: formatInt,
                values: (data?.created_candidates_series ?? []).map((row) => row.value),
              },
            ]}
          />
        </article>

        <article className="queue-card queue-dash-feed">
          <h3 className="queue-card-title">Конверсия в собеседование</h3>
          <p className="queue-card-hint">
            Доля созданных кандидатов, дошедших до собеседования (по когорте создания)
          </p>
          <QueueChart
            title="Конверсия в собеседование"
            description="Доля кандидатов когорты, дошедших до собеседования."
            labels={labels}
            emptyText="За период новых кандидатов не было — конверсию считать не из чего."
            height={200}
            series={[
              {
                key: "conversion",
                label: "Конверсия",
                unit: "%",
                kind: "line",
                tone: "success",
                format: formatPercent,
                values: (data?.interview_conversion_series ?? []).map((row) => row.rate),
              },
            ]}
          />
        </article>

        <article className="queue-card queue-dash-feed">
          <h3 className="queue-card-title">Средний срок найма</h3>
          <p className="queue-card-hint">От создания кандидата до найма/выхода, в днях</p>
          <QueueChart
            title="Средний срок найма"
            description="Среднее время от создания кандидата до найма или выхода, в днях."
            labels={labels}
            emptyText="За период наймов не было — срок считать не из чего."
            height={200}
            series={[
              {
                key: "days",
                label: "Срок найма",
                unit: "дн.",
                kind: "line",
                tone: "warning",
                format: formatDays,
                values: (data?.average_hiring_days_series ?? []).map((row) => row.value),
              },
            ]}
          />
        </article>

        <article className="queue-card queue-dash-feed">
          <h3 className="queue-card-title">Задачи на период</h3>
          <p className="queue-card-hint">Мои активные напоминания со сроком в периоде</p>
          <TaskList
            tasks={data?.tasks_due ?? []}
            emptyText="На период задач нет."
            total={data?.kpis.my_tasks ?? 0}
          />
          <h4 className="queue-card-subtitle">Просроченные</h4>
          <TaskList
            tasks={data?.tasks_overdue ?? []}
            emptyText="Просроченных задач нет."
            total={data?.kpis.overdue_tasks ?? 0}
          />
        </article>

        <article className="queue-card queue-dash-rail">
          <h3 className="queue-card-title">Ближайшие события</h3>
          <p className="queue-card-hint">Запланированные события на 7 дней вперёд</p>
          {events.length === 0 ? (
            <p className="queue-empty">На ближайшие 7 дней событий нет.</p>
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
              Показаны первые {Math.min(events.length, EVENTS_CARD_LIMIT)} из {eventsTotal} — полный
              список в разделе «Календарь».
            </p>
          )}
        </article>

        <article className="queue-card queue-dash-main">
          <h3 className="queue-card-title">Воронка моих кандидатов</h3>
          <p className="queue-card-hint">Текущее распределение по этапам (вся личная область)</p>
          <ul className="queue-funnel">
            {funnel.rows.map((row) => (
              <li key={row.stage} className="queue-funnel-row">
                <span className="queue-funnel-label">{row.label}</span>
                <span className="queue-funnel-track">
                  <span
                    className="queue-funnel-fill"
                    style={{
                      width: `${Math.round((row.count / funnel.max) * 100)}%`,
                      background: `linear-gradient(135deg, var(--cat-${row.cat}-1), var(--cat-${row.cat}-2))`,
                    }}
                  />
                </span>
                <span className="queue-funnel-count">{row.count}</span>
              </li>
            ))}
          </ul>
        </article>

        <article className="queue-card queue-dash-side">
          <h3 className="queue-card-title">Требуют внимания</h3>
          <p className="queue-card-hint">Кандидаты без движения 3+ дня</p>
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
              Показаны {Math.min(stuckSample.length, STUCK_CARD_LIMIT)} из {stuckTotal} — это самые
              давние, остальные видны в списке ниже.
            </p>
          )}
          {data && data.unread_notifications > 0 && onOpenNotifications && (
            <button type="button" className="queue-link" onClick={onOpenNotifications}>
              <Icon name="bell" size={14} />
              Непрочитанных уведомлений: {data.unread_notifications}
            </button>
          )}
        </article>
      </div>
    </section>
  );
}

/* --- Плитка KPI -------------------------------------------------------------- */

interface KpiCardProps {
  label: string;
  value: string;
  hint: string;
  tone?: "warning";
  /** Метрику вычислить нельзя: показываем «—» и объяснение, а не ноль. */
  unavailable?: boolean;
}

function KpiCard({ label, value, hint, tone, unavailable }: KpiCardProps) {
  return (
    <div className={`queue-kpi ${tone === "warning" ? "queue-kpi-warning" : ""}`}>
      <dt className="queue-kpi-label">{label}</dt>
      <dd className="queue-kpi-value">
        {value}
        {/* Пояснение «почему —» видно на экране, а не только в title. */}
        {unavailable && <span className="sr-only"> (показатель недоступен)</span>}
      </dd>
      <p className="queue-kpi-hint">{hint}</p>
    </div>
  );
}

/* --- Список задач ----------------------------------------------------------- */

function TaskList({
  tasks,
  emptyText,
  total,
}: {
  tasks: { id: string; title: string; due_at: string }[];
  emptyText: string;
  total: number;
}) {
  if (tasks.length === 0) {
    return <p className="queue-empty">{emptyText}</p>;
  }
  return (
    <>
      <ul className="queue-tasks">
        {tasks.map((task) => (
          <li key={task.id} className="queue-task">
            <span className="queue-task-time">{formatTime(task.due_at)}</span>
            <span className="queue-task-title">{task.title}</span>
          </li>
        ))}
      </ul>
      {total > tasks.length && (
        <p className="queue-card-more">
          Показаны {tasks.length} из {total} — полный список в разделе «Напоминания».
        </p>
      )}
    </>
  );
}

/** Пустой список кандидатов: экран сводки не должен выглядеть сломанным. */
export function QueueEmptyHint() {
  return <EmptyState title="Очередь пуста" description="Новых кандидатов пока нет." />;
}
