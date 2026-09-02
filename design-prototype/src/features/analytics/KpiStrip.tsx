import type { KpiSummary } from "../../types";
import "./kpiStrip.css";

const ITEMS: Array<{ key: keyof KpiSummary; label: string; suffix?: string }> = [
  { key: "newCandidates", label: "Новых кандидатов" },
  { key: "calls", label: "Звонков" },
  { key: "interviewsDone", label: "Собеседований проведено" },
  { key: "offers", label: "Офферов" },
  { key: "hired", label: "Оформлено" },
  { key: "conversionToHire", label: "Конверсия в найм", suffix: "%" },
];

/**
 * Намеренно НЕ решётка из одинаковых карточек-плиток (антипаттерн из
 * промпта): это горизонтальная "лента" с разной визуальной ролью первого
 * (акцентного) значения и подсказками — как в приборной панели, а не набор
 * дублирующихся квадратов.
 */
export function KpiStrip({ kpi }: { kpi: KpiSummary }) {
  return (
    <div className="kpi-strip" role="group" aria-label="Ключевые показатели">
      {ITEMS.map((item, index) => (
        <div className={`kpi-item ${index === 0 ? "kpi-item-primary" : ""}`} key={item.key}>
          <span className="kpi-value">
            {kpi[item.key]}
            {item.suffix ?? ""}
          </span>
          <span className="kpi-label">{item.label}</span>
        </div>
      ))}
    </div>
  );
}
