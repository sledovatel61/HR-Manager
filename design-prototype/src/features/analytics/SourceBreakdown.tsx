import { CANDIDATES } from "../../data/mockData";
import { SOURCE_LABELS, type CandidateSource } from "../../types";
import "./sourceBreakdown.css";

export function SourceBreakdown() {
  const counts = new Map<CandidateSource, number>();
  for (const c of CANDIDATES) {
    if (c.isDeleted) continue;
    counts.set(c.source, (counts.get(c.source) ?? 0) + 1);
  }
  const total = Array.from(counts.values()).reduce((a, b) => a + b, 0) || 1;
  const rows = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);

  return (
    <table className="source-table">
      <caption className="sr-only">Распределение кандидатов по источникам</caption>
      <thead>
        <tr>
          <th scope="col">Источник</th>
          <th scope="col">Кандидатов</th>
          <th scope="col">Доля</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([source, count]) => (
          <tr key={source}>
            <td>{SOURCE_LABELS[source]}</td>
            <td className="source-count">{count}</td>
            <td>
              <div className="source-bar-track">
                <div className="source-bar-fill" style={{ width: `${Math.round((count / total) * 100)}%` }} />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
