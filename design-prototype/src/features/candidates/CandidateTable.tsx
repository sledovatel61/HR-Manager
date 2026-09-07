import { Icon } from "../../icons/Icon";
import { Avatar } from "../../components/ui/Avatar";
import { StageChip } from "../../components/ui/StatusChip";
import { IconButton } from "../../components/ui/Button";
import { userById } from "../../data/mockData";
import { SOURCE_LABELS, type Candidate } from "../../types";
import { formatRelative } from "../../utils/format";
import { CURRENT_DATE } from "../../data/mockData";
import "./candidateTable.css";

interface CandidateTableProps {
  candidates: Candidate[];
  sortKey: "lastActivityAt" | "createdAt" | "fullName";
  sortDir: "asc" | "desc";
  onSort: (key: "lastActivityAt" | "createdAt" | "fullName") => void;
  onOpen: (id: string) => void;
  onTransfer: (id: string) => void;
  onQuickStage: (id: string) => void;
  selectedId?: string;
}

export function CandidateTable({ candidates, sortKey, sortDir, onSort, onOpen, onTransfer, onQuickStage, selectedId }: CandidateTableProps) {
  return (
    <div className="candidate-table-wrap" role="region" aria-label="Таблица кандидатов" tabIndex={0}>
      <table className="candidate-table">
        <caption className="sr-only">
          Список кандидатов с сортировкой по столбцам. Всего строк: {candidates.length}.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="col-checkbox">
              <span className="sr-only">Выбор строки</span>
            </th>
            <th scope="col">
              <SortableHeader label="Кандидат" active={sortKey === "fullName"} dir={sortDir} onClick={() => onSort("fullName")} />
            </th>
            <th scope="col">Вакансия</th>
            <th scope="col">Этап</th>
            <th scope="col">Ответственный</th>
            <th scope="col">Источник</th>
            <th scope="col">
              <SortableHeader label="Создан" active={sortKey === "createdAt"} dir={sortDir} onClick={() => onSort("createdAt")} />
            </th>
            <th scope="col">
              <SortableHeader label="Активность" active={sortKey === "lastActivityAt"} dir={sortDir} onClick={() => onSort("lastActivityAt")} />
            </th>
            <th scope="col" className="col-actions">
              <span className="sr-only">Действия</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {candidates.map((c) => {
            const owner = userById(c.ownerId);
            return (
              <tr
                key={c.id}
                className={selectedId === c.id ? "is-selected" : ""}
                onClick={() => onOpen(c.id)}
                tabIndex={0}
                role="button"
                aria-label={`Открыть карточку кандидата ${c.fullName}`}
                onKeyDown={(e) => {
                  if (e.key === "Enter") onOpen(c.id);
                }}
              >
                <td className="col-checkbox" onClick={(e) => e.stopPropagation()}>
                  <input type="checkbox" aria-label={`Выбрать ${c.fullName}`} />
                </td>
                <td>
                  <div className="candidate-cell">
                    <Avatar initials={c.initials} color={c.avatarColor} size="sm" />
                    <div className="candidate-cell-text">
                      <span className="candidate-name">{c.fullName}</span>
                      <span className="candidate-meta">{c.city}</span>
                    </div>
                  </div>
                </td>
                <td>{c.position}</td>
                <td><StageChip stage={c.stage} size="sm" /></td>
                <td>
                  {owner && (
                    <div className="owner-cell">
                      <Avatar initials={owner.initials} color={owner.avatarColor} size="sm" />
                      <span>{owner.fullName}</span>
                    </div>
                  )}
                </td>
                <td className="muted-cell">{SOURCE_LABELS[c.source]}</td>
                <td className="muted-cell">{formatRelative(c.createdAt, CURRENT_DATE)}</td>
                <td className="muted-cell">{formatRelative(c.lastActivityAt, CURRENT_DATE)}</td>
                <td className="col-actions" onClick={(e) => e.stopPropagation()}>
                  <div className="row-actions">
                    <IconButton icon="arrow-right-left" label={`Передать кандидата ${c.fullName}`} size="sm" onClick={() => onTransfer(c.id)} />
                    <IconButton icon="check-circle" label={`Изменить статус ${c.fullName}`} size="sm" onClick={() => onQuickStage(c.id)} />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SortableHeader({ label, active, dir, onClick }: { label: string; active: boolean; dir: "asc" | "desc"; onClick: () => void }) {
  return (
    <button type="button" className={`th-sort-btn ${active ? "is-active" : ""}`} onClick={onClick} aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}>
      {label}
      <Icon name={active ? (dir === "asc" ? "chevron-up-down" : "chevron-up-down") : "chevron-up-down"} size={12} />
    </button>
  );
}
