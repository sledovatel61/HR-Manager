import { useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { Tabs } from "../components/ui/Tabs";
import { SelectInput } from "../components/ui/Field";
import { Button } from "../components/ui/Button";
import { FunnelChart } from "../features/analytics/FunnelChart";
import { KpiStrip } from "../features/analytics/KpiStrip";
import { SourceBreakdown } from "../features/analytics/SourceBreakdown";
import { useAppState } from "../state/AppState";
import { KPI_PERSONAL, KPI_TEAM, USERS } from "../data/mockData";
import "./analyticsPage.css";

export function AnalyticsPage() {
  const { pushToast } = useAppState();
  const [period, setPeriod] = useState("month");
  const [scope, setScope] = useState("team");

  const kpi = scope === "team" ? KPI_TEAM : KPI_PERSONAL[scope] ?? KPI_TEAM;

  return (
    <div>
      <PageHeader
        title="Аналитика"
        description="Общие и персональные KPI, воронка и конверсии. Фильтры по HR, источнику и периоду."
        actions={
          <Button variant="secondary" icon="download" onClick={() => pushToast("success", "Отчёт экспортирован (мок): report.csv")}>
            Экспорт отчёта
          </Button>
        }
      />

      <div className="analytics-filters">
        <Tabs
          ariaLabel="Период аналитики"
          activeId={period}
          onChange={setPeriod}
          items={[
            { id: "day", label: "День" },
            { id: "week", label: "Неделя" },
            { id: "month", label: "Месяц" },
            { id: "quarter", label: "Квартал" },
          ]}
        />
        <label className="analytics-scope">
          <span className="sr-only">Разрез аналитики</span>
          <SelectInput value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="team">Вся команда</option>
            {USERS.filter((u) => u.role === "hr").map((u) => (
              <option key={u.id} value={u.id}>{u.fullName}</option>
            ))}
          </SelectInput>
        </label>
      </div>

      <KpiStrip kpi={kpi} />

      <div className="analytics-grid">
        <section className="analytics-panel" aria-labelledby="funnel-title">
          <h3 id="funnel-title">Воронка подбора</h3>
          <p className="analytics-panel-sub">Единое определение конверсии между этапами для всей команды.</p>
          <FunnelChart />
        </section>

        <section className="analytics-panel" aria-labelledby="source-title">
          <h3 id="source-title">По источникам</h3>
          <p className="analytics-panel-sub">Доля кандидатов и оформленных офферов по источнику.</p>
          <SourceBreakdown />
        </section>
      </div>
    </div>
  );
}
