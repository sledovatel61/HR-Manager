import { FUNNEL } from "../../data/mockData";
import { STAGE_LABELS } from "../../types";
import "./funnelChart.css";

/**
 * Воронка как горизонтальные полосы + явный % конверсии между шагами
 * текстом (не только длиной полосы) — так значение доступно и без
 * восприятия цвета/длины (WCAG 1.4.1).
 */
export function FunnelChart() {
  const max = FUNNEL[0]?.count ?? 1;

  return (
    <div className="funnel-chart">
      {FUNNEL.map((point, index) => {
        const width = Math.max(6, Math.round((point.count / max) * 100));
        const prev = FUNNEL[index - 1];
        const conversion = prev ? Math.round((point.count / prev.count) * 100) : null;
        return (
          <div className="funnel-row" key={point.stage}>
            <div className="funnel-row-head">
              <span className="funnel-stage-label">{STAGE_LABELS[point.stage]}</span>
              <span className="funnel-count">{point.count}</span>
            </div>
            <div className="funnel-bar-track">
              <div className="funnel-bar-fill" style={{ width: `${width}%` }} />
            </div>
            {conversion !== null && (
              <span className="funnel-conversion">
                {conversion}% от предыдущего этапа
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
