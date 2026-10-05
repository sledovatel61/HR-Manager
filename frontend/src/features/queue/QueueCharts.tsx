import { useEffect, useId, useRef, useState } from "react";
import { formatInt, formatPercent, niceScale, type Scale } from "./chartFormat";

/**
 * Графики «Моя очередь» — inline SVG без внешних библиотек.
 *
 * Почему так: в проекте нет ни одной библиотеки графиков (dependencies —
 * только react/react-dom), а тянуть новую ради четырёх диаграмм значит
 * увеличить бандл и получить чужую недоступную разметку. Поэтому кривые
 * считаются здесь, а требования доступности закрыты явно:
 *
 *   • у графика есть `role="img"` и `aria-label` со сводкой (минимум,
 *     максимум, последнее значение) — не только визуальный ряд;
 *   • рядом всегда есть <details> с настоящей таблицей значений: значения
 *     доступны без наведения, их читает скринридер и видит пользователь без
 *     мыши (зависимости от hover нет);
 *   • ряды различаются не только цветом: разный тип линии (сплошная /
 *     пунктир) и разные маркеры (круг / квадрат) — кодирование не на одном
 *     цвете;
 *   • пустой период не рисует «пустой квадрат», а объясняет себя текстом;
 *   • анимация появления гасится через prefers-reduced-motion (в queue.css).
 */

export interface ChartSeries {
  key: string;
  label: string;
  /** `null` — значения нет (не ноль: ноль рисуется, отсутствие — нет). */
  values: (number | null)[];
  kind: "area" | "line";
  /** Своя шкала справа (единицы измерения разные: штуки и дни). */
  axis?: "left" | "right";
  unit?: string;
  tone?: "accent" | "warning" | "info" | "success";
  format?: (value: number | null) => string;
}

interface QueueChartProps {
  /** Название графика (заголовок карточки). */
  title: string;
  /** Что именно измеряется — подпись под заголовком. */
  description: string;
  labels: string[];
  series: ChartSeries[];
  emptyText: string;
  height?: number;
}

/* --- Геометрия -------------------------------------------------------------- */

const PAD = { left: 46, right: 46, top: 16, bottom: 28 };
const FALLBACK_WIDTH = 720;

/** Ширина контейнера в пикселях: viewBox считается в реальных координатах,
 *  иначе на узком экране подписи оси уезжают и текст становится нечитаемым.
 *  В jsdom (тесты) ResizeObserver нет — остаётся запасная ширина. */
function useChartWidth(fallback = FALLBACK_WIDTH): [React.RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const value = entries[0]?.contentRect.width ?? 0;
      if (value > 0) setWidth(Math.round(value));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, width];
}

function tickValues(scale: Scale): number[] {
  const values: number[] = [];
  for (let value = scale.min; value <= scale.max + scale.step / 1000; value += scale.step) {
    values.push(Number(value.toFixed(6)));
  }
  return values;
}

/* --- Линейный график (площадь + линия, до двух осей) ------------------------ */

export function QueueChart({
  title,
  description,
  labels,
  series,
  emptyText,
  height = 240,
}: QueueChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const [ref, width] = useChartWidth();
  const hasData = series.some((item) =>
    item.values.some((value) => value !== null && value !== 0),
  );

  const plotWidth = Math.max(120, width - PAD.left - PAD.right);
  const plotHeight = height - PAD.top - PAD.bottom;
  const count = labels.length;

  const leftSeries = series.filter((item) => (item.axis ?? "left") === "left");
  const rightSeries = series.filter((item) => item.axis === "right");
  const scaleFor = (items: ChartSeries[]): Scale => {
    const values = items.flatMap((item) => item.values).filter((v): v is number => v !== null);
    return niceScale(values.length ? Math.min(...values) : 0, values.length ? Math.max(...values) : 1);
  };
  const leftScale = scaleFor(leftSeries);
  const rightScale = scaleFor(rightSeries);

  const x = (index: number) =>
    PAD.left + (count <= 1 ? plotWidth / 2 : (plotWidth * index) / (count - 1));
  const yOn = (value: number, scale: Scale) =>
    PAD.top + plotHeight - ((value - scale.min) / (scale.max - scale.min || 1)) * plotHeight;

  const summary = series
    .map((item) => {
      const values = item.values.filter((v): v is number => v !== null);
      if (values.length === 0) return `${item.label}: нет данных`;
      const last = values[values.length - 1];
      return `${item.label}: последнее ${(item.format ?? formatInt)(last)}`;
    })
    .join("; ");

  return (
    <div className="queue-chart" ref={ref}>
      {!hasData ? (
        <p className="queue-empty">{emptyText}</p>
      ) : (
        <>
          <svg
            className="queue-chart-svg"
            width="100%"
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            role="img"
            aria-label={`${title}. ${description} ${summary}`}
          >
            {tickValues(leftScale).map((value) => (
              <g key={`l-${value}`}>
                <line
                  className="queue-chart-grid"
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={yOn(value, leftScale)}
                  y2={yOn(value, leftScale)}
                />
                <text className="queue-chart-axis" x={PAD.left - 8} y={yOn(value, leftScale) + 4} textAnchor="end">
                  {formatInt(value)}
                </text>
              </g>
            ))}
            {rightSeries.length > 0 &&
              tickValues(rightScale).map((value) => (
                <text
                  key={`r-${value}`}
                  className="queue-chart-axis queue-chart-axis-right"
                  x={width - PAD.right + 8}
                  y={yOn(value, rightScale) + 4}
                  textAnchor="start"
                >
                  {formatInt(value)}
                </text>
              ))}

            {series.map((item) => {
              const scale = (item.axis ?? "left") === "left" ? leftScale : rightScale;
              const points = item.values
                .map((value, index) => (value === null ? null : { index, value }))
                .filter((point): point is { index: number; value: number } => point !== null);
              if (points.length === 0) return null;
              const path = points
                .map((point, position) => `${position === 0 ? "M" : "L"}${x(point.index)},${yOn(point.value, scale)}`)
                .join(" ");
              const areaPath =
                points.length > 1
                  ? `${path} L${x(points[points.length - 1].index)},${PAD.top + plotHeight} L${x(points[0].index)},${PAD.top + plotHeight} Z`
                  : "";
              const last = points[points.length - 1];
              return (
                <g key={item.key} className={`queue-series queue-series-${item.tone ?? "accent"}`}>
                  {item.kind === "area" && areaPath && (
                    <path className="queue-chart-area" d={areaPath} fill={`url(#${gradientId}-${item.key})`} />
                  )}
                  <defs>
                    <linearGradient id={`${gradientId}-${item.key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop className="queue-chart-grad-from" offset="0%" />
                      <stop className="queue-chart-grad-to" offset="100%" />
                    </linearGradient>
                  </defs>
                  <path
                    className={`queue-chart-line ${item.axis === "right" ? "queue-chart-line-dashed" : ""}`}
                    d={path}
                    fill="none"
                  />
                  {points.map((point) => (
                    <g key={point.index}>
                      <title>
                        {`${labels[point.index]}: ${(item.format ?? formatInt)(point.value)}`}
                      </title>
                      {item.axis === "right" ? (
                        <rect
                          className="queue-chart-dot"
                          x={x(point.index) - 3}
                          y={yOn(point.value, scale) - 3}
                          width={6}
                          height={6}
                        />
                      ) : (
                        <circle
                          className="queue-chart-dot"
                          cx={x(point.index)}
                          cy={yOn(point.value, scale)}
                          r={point.index === last.index ? 4.5 : 3}
                        />
                      )}
                    </g>
                  ))}
                </g>
              );
            })}

            {labels.map((label, index) => {
              const step = Math.ceil(count / 7);
              if (index % step !== 0 && index !== count - 1) return null;
              return (
                <text
                  key={label}
                  className="queue-chart-axis"
                  x={x(index)}
                  y={height - 8}
                  textAnchor={index === 0 ? "start" : index === count - 1 ? "end" : "middle"}
                >
                  {label}
                </text>
              );
            })}
          </svg>

          <ul className="queue-chart-legend">
            {series.map((item) => (
              <li key={item.key} className={`queue-legend-item queue-series-${item.tone ?? "accent"}`}>
                {/* Форма маркера повторяет форму точки на графике: цвет — не
                    единственный способ отличить ряды. */}
                <span
                  className={`queue-legend-marker ${item.axis === "right" ? "is-square" : "is-circle"} ${
                    item.axis === "right" ? "is-dashed" : ""
                  }`}
                  aria-hidden="true"
                />
                {item.label}
                {item.unit ? <span className="queue-legend-unit">, {item.unit}</span> : null}
              </li>
            ))}
          </ul>

          {/* Значения без наведения: раскрывающаяся таблица (доступна и
              скринридеру, и пользователю без мыши). */}
          <details className="queue-chart-fallback">
            <summary>Таблица значений</summary>
            <table className="queue-chart-table">
              <caption>{title}</caption>
              <thead>
                <tr>
                  <th scope="col">Период</th>
                  {series.map((item) => (
                    <th key={item.key} scope="col">
                      {item.label}
                      {item.unit ? `, ${item.unit}` : ""}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {labels.map((label, index) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    {series.map((item) => (
                      <td key={item.key}>{(item.format ?? formatInt)(item.values[index] ?? null)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
    </div>
  );
}

/* --- Столбчатая диаграмма (источники) --------------------------------------- */

interface QueueBarChartProps {
  title: string;
  description: string;
  items: { key: string; label: string; value: number }[];
  emptyText: string;
  format?: (value: number) => string;
}

export function QueueBarChart({
  title,
  description,
  items,
  emptyText,
  format = formatInt,
}: QueueBarChartProps) {
  const total = items.reduce((sum, item) => sum + item.value, 0);
  const hasData = items.some((item) => item.value > 0);
  const max = Math.max(1, ...items.map((item) => item.value));

  if (!hasData) {
    return (
      <div className="queue-chart">
        <p className="queue-empty">{emptyText}</p>
      </div>
    );
  }

  return (
    <div className="queue-chart">
      <ul
        className="queue-bars"
        role="img"
        aria-label={`${title}. ${description} Всего: ${format(total)}. ${items
          .map((item) => `${item.label}: ${format(item.value)}`)
          .join("; ")}`}
      >
        {items.map((item) => (
          <li key={item.key} className="queue-bar-row">
            <span className="queue-bar-label">{item.label}</span>
            <span className="queue-bar-track">
              <span
                className="queue-bar-fill"
                style={{ width: `${Math.round((item.value / max) * 100)}%` }}
              />
            </span>
            <span className="queue-bar-value">{format(item.value)}</span>
          </li>
        ))}
      </ul>
      {/* Значения видны и без наведения: они подписаны рядом с полосой. */}
      <details className="queue-chart-fallback">
        <summary>Таблица значений</summary>
        <table className="queue-chart-table">
          <caption>{title}</caption>
          <thead>
            <tr>
              <th scope="col">Источник</th>
              <th scope="col">Количество</th>
              <th scope="col">Доля</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.key}>
                <th scope="row">{item.label}</th>
                <td>{format(item.value)}</td>
                <td>{total > 0 ? formatPercent(Math.round((item.value / total) * 1000) / 10) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
