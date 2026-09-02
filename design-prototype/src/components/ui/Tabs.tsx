import { useId } from "react";
import "./tabs.css";

export interface TabItem {
  id: string;
  label: string;
  count?: number;
}

interface TabsProps {
  items: TabItem[];
  activeId: string;
  onChange: (id: string) => void;
  ariaLabel: string;
}

/** Roving-tabindex tabs pattern (WAI-ARIA APG "Tabs"). */
export function Tabs({ items, activeId, onChange, ariaLabel }: TabsProps) {
  const baseId = useId();

  function handleKeyDown(event: React.KeyboardEvent, index: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const dir = event.key === "ArrowRight" ? 1 : -1;
    const next = (index + dir + items.length) % items.length;
    onChange(items[next].id);
    const el = document.getElementById(`${baseId}-tab-${items[next].id}`);
    el?.focus();
  }

  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item, index) => {
        const selected = item.id === activeId;
        return (
          <button
            key={item.id}
            id={`${baseId}-tab-${item.id}`}
            role="tab"
            type="button"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            className={`tab-item ${selected ? "is-active" : ""}`}
            onClick={() => onChange(item.id)}
            onKeyDown={(e) => handleKeyDown(e, index)}
          >
            {item.label}
            {item.count !== undefined && <span className="tab-count">{item.count}</span>}
          </button>
        );
      })}
    </div>
  );
}
