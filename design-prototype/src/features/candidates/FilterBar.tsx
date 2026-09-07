import { useState } from "react";
import { Icon } from "../../icons/Icon";
import { IconButton } from "../../components/ui/Button";
import { SelectInput } from "../../components/ui/Field";
import { USERS, SAVED_VIEWS } from "../../data/mockData";
import { SOURCE_LABELS, STAGE_LABELS, STAGE_ORDER } from "../../types";
import type { CandidateFilters } from "./useCandidateFilters";
import "./filterBar.css";

interface FilterBarProps {
  filters: CandidateFilters;
  onChange: (next: CandidateFilters) => void;
  resultCount: number;
  showOnlyMineToggle?: boolean;
}

export function FilterBar({ filters, onChange, resultCount, showOnlyMineToggle }: FilterBarProps) {
  const [panelOpen, setPanelOpen] = useState(false);
  const hrUsers = USERS.filter((u) => u.role === "hr");

  return (
    <div className="filter-bar">
      <div className="filter-bar-row">
        <div className="filter-search">
          <Icon name="search" size={15} />
          <input
            type="search"
            placeholder="Найти по имени, должности, городу…"
            value={filters.query}
            onChange={(e) => onChange({ ...filters, query: e.target.value })}
            aria-label="Поиск кандидатов"
          />
        </div>

        <IconButton
          icon="filter"
          label={panelOpen ? "Скрыть фильтры" : "Показать фильтры"}
          active={panelOpen}
          onClick={() => setPanelOpen((v) => !v)}
        />

        {showOnlyMineToggle && (
          <button
            type="button"
            className={`filter-chip-toggle ${filters.onlyMine ? "is-active" : ""}`}
            aria-pressed={filters.onlyMine}
            onClick={() => onChange({ ...filters, onlyMine: !filters.onlyMine })}
          >
            Только мои
          </button>
        )}

        <div className="filter-saved-views">
          <label htmlFor="saved-view-select" className="sr-only">Сохранённые представления</label>
          <SelectInput id="saved-view-select" defaultValue={SAVED_VIEWS[0].id}>
            {SAVED_VIEWS.map((v) => (
              <option key={v.id} value={v.id}>{v.name}</option>
            ))}
          </SelectInput>
        </div>

        <span className="filter-result-count" aria-live="polite">
          {resultCount} {pluralizeCandidate(resultCount)}
        </span>
      </div>

      {panelOpen && (
        <div className="filter-panel" role="region" aria-label="Расширенные фильтры">
          <label className="filter-field">
            <span>Этап</span>
            <SelectInput
              value={filters.stage}
              onChange={(e) => onChange({ ...filters, stage: e.target.value as CandidateFilters["stage"] })}
            >
              <option value="all">Все этапы</option>
              {STAGE_ORDER.map((s) => (
                <option key={s} value={s}>{STAGE_LABELS[s]}</option>
              ))}
            </SelectInput>
          </label>

          <label className="filter-field">
            <span>Ответственный</span>
            <SelectInput
              value={filters.ownerId}
              onChange={(e) => onChange({ ...filters, ownerId: e.target.value })}
            >
              <option value="all">Все HR</option>
              {hrUsers.map((u) => (
                <option key={u.id} value={u.id}>{u.fullName}</option>
              ))}
            </SelectInput>
          </label>

          <label className="filter-field">
            <span>Источник</span>
            <SelectInput
              value={filters.source}
              onChange={(e) => onChange({ ...filters, source: e.target.value as CandidateFilters["source"] })}
            >
              <option value="all">Все источники</option>
              {Object.entries(SOURCE_LABELS).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </SelectInput>
          </label>

          <button
            type="button"
            className="filter-reset"
            onClick={() => onChange({ query: filters.query, stage: "all", ownerId: "all", source: "all", onlyMine: filters.onlyMine })}
          >
            Сбросить фильтры
          </button>
        </div>
      )}
    </div>
  );
}

function pluralizeCandidate(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod100 >= 11 && mod100 <= 14) return "кандидатов";
  if (mod10 === 1) return "кандидат";
  if (mod10 >= 2 && mod10 <= 4) return "кандидата";
  return "кандидатов";
}
