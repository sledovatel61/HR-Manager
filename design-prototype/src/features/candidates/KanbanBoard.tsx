import { useState } from "react";
import { Avatar } from "../../components/ui/Avatar";
import { userById } from "../../data/mockData";
import { STAGE_LABELS, STAGE_ORDER, type Candidate, type CandidateStage } from "../../types";
import { formatRelative } from "../../utils/format";
import { CURRENT_DATE } from "../../data/mockData";
import "./kanbanBoard.css";

const BOARD_STAGES: CandidateStage[] = [
  "new",
  "contacted",
  "reached",
  "interview_scheduled",
  "interview_done",
  "offer",
  "hired",
];

interface KanbanBoardProps {
  candidates: Candidate[];
  onOpen: (id: string) => void;
  onMove: (id: string, stage: CandidateStage) => void;
}

/**
 * Kanban с drag-and-drop (мышь) и keyboard-first альтернативой: у каждой
 * карточки есть select "Переместить в", т.к. drag-and-drop без клавиатурной
 * альтернативы не проходит WCAG 2.5.7 (Dragging Movements, AA).
 */
export function KanbanBoard({ candidates, onOpen, onMove }: KanbanBoardProps) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverStage, setDragOverStage] = useState<CandidateStage | null>(null);

  const columns = BOARD_STAGES.map((stage) => ({
    stage,
    items: candidates.filter((c) => c.stage === stage),
  }));

  return (
    <div className="kanban-board" role="region" aria-label="Доска кандидатов по этапам">
      {columns.map((col) => (
        <div
          key={col.stage}
          className={`kanban-column ${dragOverStage === col.stage ? "is-drag-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOverStage(col.stage);
          }}
          onDragLeave={() => setDragOverStage((s) => (s === col.stage ? null : s))}
          onDrop={(e) => {
            e.preventDefault();
            if (dragId) onMove(dragId, col.stage);
            setDragId(null);
            setDragOverStage(null);
          }}
        >
          <div className="kanban-column-head">
            <span>{STAGE_LABELS[col.stage]}</span>
            <span className="kanban-column-count">{col.items.length}</span>
          </div>
          <div className="kanban-column-body">
            {col.items.length === 0 && <p className="kanban-empty">Нет кандидатов</p>}
            {col.items.map((c) => {
              const owner = userById(c.ownerId);
              return (
                <article
                  key={c.id}
                  className="kanban-card"
                  draggable
                  onDragStart={() => setDragId(c.id)}
                  onDragEnd={() => setDragId(null)}
                  tabIndex={0}
                  role="button"
                  aria-label={`Кандидат ${c.fullName}, этап ${STAGE_LABELS[c.stage]}. Enter — открыть карточку.`}
                  onClick={() => onOpen(c.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") onOpen(c.id);
                  }}
                >
                  <div className="kanban-card-top">
                    <Avatar initials={c.initials} color={c.avatarColor} size="sm" />
                    <span className="kanban-card-name">{c.fullName}</span>
                  </div>
                  <p className="kanban-card-position">{c.position}</p>
                  <div className="kanban-card-foot">
                    {owner && <span className="kanban-card-owner">{owner.fullName.split(" ")[0]}</span>}
                    <span className="kanban-card-time">{formatRelative(c.lastActivityAt, CURRENT_DATE)}</span>
                  </div>
                  <label className="kanban-move-select sr-only-focusable" onClick={(e) => e.stopPropagation()}>
                    <span className="sr-only">Переместить кандидата {c.fullName} на этап</span>
                    <select
                      value={c.stage}
                      onChange={(e) => onMove(c.id, e.target.value as CandidateStage)}
                    >
                      {STAGE_ORDER.map((s) => (
                        <option key={s} value={s}>{STAGE_LABELS[s]}</option>
                      ))}
                    </select>
                  </label>
                </article>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
