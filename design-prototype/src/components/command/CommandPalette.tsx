import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Icon, type IconName } from "../../icons/Icon";
import { useFocusTrap } from "../ui/useFocusTrap";
import { useRouter, type RouteName } from "../../router";
import { useAppState } from "../../state/AppState";
import { CANDIDATES } from "../../data/mockData";
import { STAGE_LABELS } from "../../types";
import "./commandPalette.css";

interface CommandEntry {
  id: string;
  label: string;
  hint?: string;
  icon: IconName;
  group: "Навигация" | "Кандидаты" | "Действия";
  onRun: () => void;
  keywords?: string;
}

/**
 * Command palette (Ctrl/Cmd+K): единая точка входа для навигации, поиска
 * кандидатов и быстрых действий. См. design/DESIGN_SYSTEM.md → "Wow effect"
 * для обоснования. Реализует ARIA combobox-паттерн: input сохраняет DOM
 * focus, активный пункт указывается через aria-activedescendant.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { navigate } = useRouter();
  const { pushToast } = useAppState();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useFocusTrap(open, onClose, inputRef);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActiveIndex(0);
    }
  }, [open]);

  const navCommands: CommandEntry[] = useMemo(
    () =>
      (
        [
          ["home", "Перейти: Главная", "home"],
          ["queue", "Перейти: Моя очередь", "inbox"],
          ["candidates", "Перейти: Кандидаты", "users"],
          ["kanban", "Перейти: Kanban", "kanban"],
          ["calendar", "Перейти: Календарь", "calendar"],
          ["analytics", "Перейти: Аналитика", "bar-chart"],
          ["templates", "Перейти: Шаблоны", "file-text"],
          ["users", "Перейти: Пользователи", "shield"],
          ["audit", "Перейти: Журнал аудита", "clock"],
          ["settings", "Перейти: Настройки", "settings"],
        ] as [RouteName, string, IconName][]
      ).map(([route, label, icon]) => ({
        id: `nav-${route}`,
        label,
        icon,
        group: "Навигация",
        onRun: () => {
          navigate(route);
          onClose();
        },
      })),
    [navigate, onClose],
  );

  const actionCommands: CommandEntry[] = useMemo(
    () => [
      {
        id: "action-new-candidate",
        label: "Создать кандидата",
        icon: "user-plus",
        group: "Действия",
        onRun: () => {
          pushToast("info", "Форма создания кандидата — предмет этапа 2 роадмапа (мок-действие).");
          onClose();
        },
      },
      {
        id: "action-export",
        label: "Экспортировать текущий список",
        icon: "download",
        group: "Действия",
        onRun: () => {
          pushToast("success", "Экспорт запущен (мок): файл появится в загрузках через несколько секунд.");
          onClose();
        },
      },
    ],
    [pushToast, onClose],
  );

  const candidateCommands: CommandEntry[] = useMemo(
    () =>
      CANDIDATES.filter((c) => !c.isDeleted)
        .slice(0, 200)
        .map((c) => ({
          id: `cand-${c.id}`,
          label: c.fullName,
          hint: `${c.position} · ${STAGE_LABELS[c.stage]}`,
          icon: "users" as IconName,
          group: "Кандидаты" as const,
          keywords: `${c.fullName} ${c.position} ${c.city}`,
          onRun: () => {
            navigate("candidates", { id: c.id });
            onClose();
          },
        })),
    [navigate, onClose],
  );

  const allCommands = useMemo(() => [...navCommands, ...actionCommands, ...candidateCommands], [navCommands, actionCommands, candidateCommands]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [...navCommands, ...actionCommands, ...candidateCommands.slice(0, 5)];
    return allCommands.filter((c) => (c.keywords ?? c.label).toLowerCase().includes(q)).slice(0, 30);
  }, [query, allCommands, navCommands, actionCommands, candidateCommands]);

  const grouped = useMemo(() => {
    const groups: Record<string, CommandEntry[]> = {};
    for (const cmd of filtered) {
      groups[cmd.group] ??= [];
      groups[cmd.group].push(cmd);
    }
    return groups;
  }, [filtered]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  if (!open) return null;

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      filtered[activeIndex]?.onRun();
    }
  }

  const activeId = filtered[activeIndex]?.id;

  return createPortal(
    <div className="palette-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={containerRef} className="palette-panel" role="dialog" aria-modal="true" aria-label="Командная палитра">
        <div className="palette-input-row">
          <Icon name="search" size={16} />
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-listbox"
            aria-activedescendant={activeId}
            autoComplete="off"
            className="palette-input"
            placeholder="Поиск кандидатов, разделов, действий…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <span className="palette-hint-close">Esc</span>
        </div>

        <div className="palette-results" id="palette-listbox" role="listbox" aria-label="Результаты поиска">
          {filtered.length === 0 && (
            <div className="palette-empty">Ничего не найдено. Попробуйте другой запрос.</div>
          )}
          {Object.entries(grouped).map(([group, items]) => (
            <div key={group} className="palette-group">
              <div className="palette-group-label">{group}</div>
              {items.map((item) => {
                const globalIndex = filtered.indexOf(item);
                const active = globalIndex === activeIndex;
                return (
                  <button
                    key={item.id}
                    id={item.id}
                    role="option"
                    aria-selected={active}
                    type="button"
                    className={`palette-item ${active ? "is-active" : ""}`}
                    onMouseEnter={() => setActiveIndex(globalIndex)}
                    onClick={() => item.onRun()}
                  >
                    <Icon name={item.icon} size={15} />
                    <span className="palette-item-label">{item.label}</span>
                    {item.hint && <span className="palette-item-hint">{item.hint}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="palette-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> навигация</span>
          <span><kbd>Enter</kbd> выбрать</span>
          <span><kbd>Esc</kbd> закрыть</span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
