import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  exportAnalyticsCsv,
  fetchAnalyticsFunnel,
  fetchAnalyticsKpi,
  listHrUsers,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput } from "../../design-system/components/Field";
import {
  EmptyState,
  ErrorState,
  PermissionDeniedState,
  SkeletonRows,
} from "../../design-system/components/StateViews";
import { Tabs } from "../../design-system/components/Tabs";
import { useToast } from "../../design-system/components/ToastContext";
import {
  ANALYTICS_PRESET_LABELS,
  KPI_DEFINITIONS,
  KPI_LABELS,
  SOURCE_LABELS,
  STAGE_LABELS,
  type AnalyticsFunnelReport,
  type AnalyticsKpiReport,
  type AnalyticsKpis,
  type AnalyticsPreset,
  type AnalyticsView,
  type CandidateSource,
  type CandidateStage,
  type User,
  type UserListItem,
} from "../../types";
import { customDayBounds, presetBounds, timeZoneChoices } from "./time";
import "./analytics.css";

interface AnalyticsPageProps {
  user: User;
}

const PRESETS: AnalyticsPreset[] = ["day", "week", "month", "quarter", "custom"];
const VIEWS: { id: AnalyticsView; label: string }[] = [
  { id: "kpi", label: "KPI" },
  { id: "funnel", label: "Воронка" },
  { id: "breakdowns", label: "Разрезы" },
];

/** The KPIs are fixed contract; ordering is part of the UI tests. */
const KPI_KEYS = Object.keys(KPI_LABELS) as (keyof AnalyticsKpis)[];

function rateText(rate: number | null): string {
  return rate === null ? "N/A" : `${rate.toFixed(2)}%`;
}

/**
 * Analytics section (manager/admin only — the workspace never renders it for
 * HR). All metrics come from the backend /analytics endpoints; the UI never
 * recomputes numbers from raw data.
 */
export default function AnalyticsPage({ user }: AnalyticsPageProps) {
  const { pushToast } = useToast();

  const [preset, setPreset] = useState<AnalyticsPreset>("month");
  const [timezone, setTimezone] = useState<string>(() => {
    const resolved = new Intl.DateTimeFormat().resolvedOptions().timeZone;
    return resolved && resolved.length > 0 ? resolved : "UTC";
  });
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [hrFilter, setHrFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [view, setView] = useState<AnalyticsView>("kpi");

  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [kpi, setKpi] = useState<AnalyticsKpiReport | null>(null);
  const [funnel, setFunnel] = useState<AnalyticsFunnelReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const [exporting, setExporting] = useState(false);

  // Stale-response guard: only the newest request may write state.
  const requestId = useRef(0);

  const period = useMemo(() => {
    if (preset === "custom") {
      const from = customFrom ? customDayBounds(customFrom, timezone) : null;
      const to = customTo ? customDayBounds(customTo, timezone) : null;
      if (!from || !to) return null;
      return { from: from.from, to: to.to };
    }
    return presetBounds(preset, new Date(), timezone);
  }, [preset, customFrom, customTo, timezone]);

  const queryParams = useMemo(
    () => ({
      hr_id: hrFilter || undefined,
      source: (sourceFilter || undefined) as CandidateSource | undefined,
    }),
    [hrFilter, sourceFilter]
  );

  useEffect(() => {
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {
        // The HR filter is optional — a directory failure must not block the
        // report itself; the select simply stays empty.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const load = useCallback(async () => {
    if (!period) return;
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    setForbidden(false);
    try {
      const [kpiReport, funnelReport] = await Promise.all([
        fetchAnalyticsKpi({ from: period.from, to: period.to, timezone, ...queryParams }),
        fetchAnalyticsFunnel({ from: period.from, to: period.to, timezone, ...queryParams }),
      ]);
      if (requestId.current !== id) return; // stale response
      setKpi(kpiReport);
      setFunnel(funnelReport);
    } catch (err) {
      if (requestId.current !== id) return;
      if (err instanceof ApiError && err.status === 403) {
        setForbidden(true);
      } else {
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить аналитику.");
      }
    } finally {
      if (requestId.current === id) setLoading(false);
    }
  }, [period, timezone, queryParams]);

  useEffect(() => {
    void load();
  }, [load, reloadTick]);

  const handleExport = async () => {
    if (!period || exporting) return;
    setExporting(true);
    try {
      const { blob, filename } = await exportAnalyticsCsv({
        from: period.from,
        to: period.to,
        timezone,
        ...queryParams,
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      pushToast("success", "Отчёт выгружен.");
    } catch (err) {
      pushToast(
        "danger",
        err instanceof ApiError ? err.message : "Не удалось выгрузить отчёт."
      );
    } finally {
      setExporting(false);
    }
  };

  const allZero = useMemo(() => kpi !== null && KPI_KEYS.every((key) => kpi.kpis[key] === 0), [kpi]);

  return (
    <section className="analytics" aria-label="Аналитика и отчёты">
      <div className="analytics-controls">
        <div className="analytics-period">
          <Tabs
            items={PRESETS.map((id) => ({ id, label: ANALYTICS_PRESET_LABELS[id] }))}
            activeId={preset}
            onChange={(id) => setPreset(id as AnalyticsPreset)}
            ariaLabel="Период отчёта"
          />
          {preset === "custom" && (
            <div className="analytics-custom">
              <Field label="С даты">
                {(id) => (
                  <input
                    id={id}
                    type="date"
                    className="text-input"
                    value={customFrom}
                    onChange={(event) => setCustomFrom(event.target.value)}
                  />
                )}
              </Field>
              <Field label="По дату (включительно)">
                {(id) => (
                  <input
                    id={id}
                    type="date"
                    className="text-input"
                    value={customTo}
                    onChange={(event) => setCustomTo(event.target.value)}
                  />
                )}
              </Field>
            </div>
          )}
        </div>

        <div className="analytics-filters">
          <Field label="Таймзона">
            {(id) => (
              <SelectInput id={id} value={timezone} onChange={(event) => setTimezone(event.target.value)}>
                {timeZoneChoices().map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          {user.role !== "hr" && (
            <>
              <Field label="Ответственный">
                {(id) => (
                  <SelectInput
                    id={id}
                    value={hrFilter}
                    onChange={(event) => setHrFilter(event.target.value)}
                  >
                    <option value="">Все HR</option>
                    {directory.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.username}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              <Field label="Источник">
                {(id) => (
                  <SelectInput
                    id={id}
                    value={sourceFilter}
                    onChange={(event) => setSourceFilter(event.target.value)}
                  >
                    <option value="">Все источники</option>
                    {(Object.keys(SOURCE_LABELS) as CandidateSource[]).map((source) => (
                      <option key={source} value={source}>
                        {SOURCE_LABELS[source]}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
            </>
          )}
          <div className="analytics-export">
            <Button
              variant="secondary"
              icon="download"
              disabled={!period || exporting || loading}
              onClick={() => void handleExport()}
            >
              {exporting ? "Выгрузка…" : "Экспорт CSV"}
            </Button>
          </div>
        </div>
      </div>

      {forbidden ? (
        <PermissionDeniedState />
      ) : error ? (
        <ErrorState onRetry={() => setReloadTick((tick) => tick + 1)} />
      ) : loading || !kpi || !funnel ? (
        <SkeletonRows rows={6} columns={5} />
      ) : (
        <>
          {allZero && (
            <p className="analytics-empty-note" role="status">
              За выбранный период нет зафиксированных фактов — выберите другой период или снимите фильтры.
            </p>
          )}
          <div className="analytics-tabs">
            <Tabs
              items={VIEWS}
              activeId={view}
              onChange={(id) => setView(id as AnalyticsView)}
              ariaLabel="Вид отчёта"
            />
          </div>

          {view === "kpi" && <KpiView kpi={kpi} />}
          {view === "funnel" && <FunnelView funnel={funnel} />}
          {view === "breakdowns" && <BreakdownsView kpi={kpi} />}
        </>
      )}
    </section>
  );
}

// --- KPI ----------------------------------------------------------------------

function KpiView({ kpi }: { kpi: AnalyticsKpiReport }) {
  return (
    <div className="analytics-view">
      <dl className="kpi-strip" aria-label="Ключевые показатели за период">
        {KPI_KEYS.map((key) => (
          <div key={key} className="kpi-card" title={KPI_DEFINITIONS[key]}>
            <dt className="kpi-label">{KPI_LABELS[key]}</dt>
            <dd className="kpi-value">{kpi.kpis[key]}</dd>
          </div>
        ))}
      </dl>

      <section className="analytics-block" aria-labelledby="rejections-title">
        <h3 id="rejections-title" className="analytics-block-title">
          Отказы и увольнения
        </h3>
        <div className="rejection-pair">
          <div className="kpi-card kpi-card-warning" title={KPI_DEFINITIONS.dismissed}>
            <span className="kpi-value">{kpi.kpis.dismissed}</span>
            <span className="kpi-label">{KPI_LABELS.dismissed}</span>
          </div>
          <div className="kpi-card kpi-card-warning" title={KPI_DEFINITIONS.terminated}>
            <span className="kpi-value">{kpi.kpis.terminated}</span>
            <span className="kpi-label">{KPI_LABELS.terminated}</span>
          </div>
        </div>
        <p className="analytics-block-note">
          Отказ — переход на этап «Отказ» в периоде. Увольнение — отдельное событие с датой и причиной;
          статус «Уволен» без даты увольнения не учитывается.
        </p>
      </section>

      <ConversionsBlock conversions={kpi.conversions} />
    </div>
  );
}

// --- Funnel -------------------------------------------------------------------

function FunnelView({ funnel }: { funnel: AnalyticsFunnelReport }) {
  return (
    <div className="analytics-view">
      <section className="analytics-block" aria-labelledby="funnel-title">
        <h3 id="funnel-title" className="analytics-block-title">
          Воронка найма
        </h3>
        <table className="data-table">
          <caption className="sr-only">Сколько уникальных кандидатов достигли каждого этапа воронки за период</caption>
          <thead>
            <tr>
              <th scope="col">Этап</th>
              <th scope="col" className="num-cell">
                Достигли этапа
              </th>
            </tr>
          </thead>
          <tbody>
            {funnel.stages.map((stage) => (
              <tr key={stage.stage}>
                <th scope="row">{STAGE_LABELS[stage.stage as CandidateStage] ?? stage.stage}</th>
                <td className="num-cell">{stage.reached}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <ConversionsBlock conversions={funnel.conversions} />
    </div>
  );
}

// --- Conversions --------------------------------------------------------------

function ConversionsBlock({ conversions }: { conversions: AnalyticsFunnelReport["conversions"] }) {
  return (
    <section className="analytics-block" aria-labelledby="conversions-title">
      <h3 id="conversions-title" className="analytics-block-title">
        Конверсии между этапами
      </h3>
      <table className="data-table">
        <caption className="sr-only">
          Конверсии: числитель — кандидаты, дошедшие до следующего этапа после предыдущего в периоде;
          знаменатель — кандидаты, достигшие предыдущего этапа; N/A — нет данных (нулевой знаменатель)
        </caption>
        <thead>
          <tr>
            <th scope="col">Переход</th>
            <th scope="col" className="num-cell">
              Дошли
            </th>
            <th scope="col" className="num-cell">
              Были на этапе
            </th>
            <th scope="col" className="num-cell">
              Конверсия
            </th>
          </tr>
        </thead>
        <tbody>
          {conversions.map((conversion) => (
            <tr key={`${conversion.from_stage}-${conversion.to_stage}`}>
              <th scope="row">
                {STAGE_LABELS[conversion.from_stage as CandidateStage] ?? conversion.from_stage} →{" "}
                {STAGE_LABELS[conversion.to_stage as CandidateStage] ?? conversion.to_stage}
              </th>
              <td className="num-cell">{conversion.numerator}</td>
              <td className="num-cell">{conversion.denominator}</td>
              <td className="num-cell">
                {conversion.rate === null ? (
                  <span className="rate-na" title="Нет данных: за период никто не достиг начального этапа">
                    N/A
                  </span>
                ) : (
                  <span>{rateText(conversion.rate)}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

// --- Breakdowns ----------------------------------------------------------------

function BreakdownsView({ kpi }: { kpi: AnalyticsKpiReport }) {
  const hasHr = kpi.by_hr.length > 0;
  const hasSource = kpi.by_source.length > 0;
  if (!hasHr && !hasSource) {
    return (
      <EmptyState
        icon="bar-chart"
        title="Разрезы пусты"
        description="За выбранный период нет данных по ответственным и источникам."
      />
    );
  }
  return (
    <div className="analytics-view">
      <section className="analytics-block" aria-labelledby="by-hr-title">
        <h3 id="by-hr-title" className="analytics-block-title">
          По ответственным
        </h3>
        {hasHr ? (
          <table className="data-table">
            <caption className="sr-only">
              Метрики по ответственным HR; факты относятся к ответственному на момент события
            </caption>
            <thead>
              <tr>
                <th scope="col">Ответственный</th>
                <th scope="col" className="num-cell">
                  Создано
                </th>
                <th scope="col" className="num-cell">
                  В работе
                </th>
                <th scope="col" className="num-cell">
                  Наймы
                </th>
                <th scope="col" className="num-cell">
                  Отказы
                </th>
                <th scope="col" className="num-cell">
                  Увольнения
                </th>
              </tr>
            </thead>
            <tbody>
              {kpi.by_hr.map((row) => (
                <tr key={row.hr_id}>
                  <th scope="row">{row.username}</th>
                  <td className="num-cell">{row.created}</td>
                  <td className="num-cell">{row.processed}</td>
                  <td className="num-cell">{row.hired}</td>
                  <td className="num-cell">{row.dismissed}</td>
                  <td className="num-cell">{row.terminated}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="analytics-block-note">Нет данных по ответственным за период.</p>
        )}
      </section>

      <section className="analytics-block" aria-labelledby="by-source-title">
        <h3 id="by-source-title" className="analytics-block-title">
          По источникам
        </h3>
        {hasSource ? (
          <table className="data-table">
            <caption className="sr-only">
              Метрики по источникам кандидатов; источник зафиксирован на момент факта
            </caption>
            <thead>
              <tr>
                <th scope="col">Источник</th>
                <th scope="col" className="num-cell">
                  Создано
                </th>
                <th scope="col" className="num-cell">
                  Наймы
                </th>
                <th scope="col" className="num-cell">
                  Отказы
                </th>
                <th scope="col" className="num-cell">
                  Увольнения
                </th>
              </tr>
            </thead>
            <tbody>
              {kpi.by_source.map((row) => (
                <tr key={row.source}>
                  <th scope="row">{SOURCE_LABELS[row.source] ?? row.source}</th>
                  <td className="num-cell">{row.created}</td>
                  <td className="num-cell">{row.hired}</td>
                  <td className="num-cell">{row.dismissed}</td>
                  <td className="num-cell">{row.terminated}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="analytics-block-note">Нет данных по источникам за период.</p>
        )}
      </section>
    </div>
  );
}