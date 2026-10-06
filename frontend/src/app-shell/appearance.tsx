/* eslint-disable react-refresh/only-export-components --
 * модуль контекста: экспортирует и провайдер, и хук, и утилиту SegmentedToggle. */
/**
 * Глобальная «внешняя» тема приложения (light/dark) и плотность
 * интерфейса (comfortable/compact).
 *
 * Это единственный источник theme-state в продакшене: никакой экран не
 * хранит собственное локальное состояние темы. Провайдер выставляет
 * `data-theme` и `data-density` на `document.documentElement`, поэтому
 * тема применяется ко всему приложению сразу (shell, таблицы, drawer,
 * модалки, Kanban, библиотека).
 *
 * Сохранение — в namespaced-ключ `hrm-theme` (localStorage). Значение
 * валидируется при чтении: битый/некорректный storage трактуется как
 * отсутствие выбора, и тема берётся из `prefers-color-scheme`.
 *
 * См. docs/MOCKUP_PARITY_AGENT_PROMPT_RU.md — раздел «Настройки и тема».
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Icon, type IconName } from "../design-system/icons/Icon";

export type ThemeMode = "light" | "dark";
export type DensityMode = "comfortable" | "compact";

export interface Appearance {
  theme: ThemeMode;
  density: DensityMode;
}

export const THEME_STORAGE_KEY = "hrm-theme";

function isTheme(value: unknown): value is ThemeMode {
  return value === "light" || value === "dark";
}

function isDensity(value: unknown): value is DensityMode {
  return value === "comfortable" || value === "compact";
}

function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/** Читает и валидирует сохранённые значения. null = ничего не сохранено. */
function readStored(): Partial<Appearance> | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed !== "object" || parsed === null) return null;
    const obj = parsed as Record<string, unknown>;
    const result: Partial<Appearance> = {};
    if (isTheme(obj.theme)) result.theme = obj.theme;
    if (isDensity(obj.density)) result.density = obj.density;
    return result;
  } catch {
    return null;
  }
}

function resolveInitial(): Appearance {
  const stored = readStored();
  return {
    theme: stored?.theme ?? (systemPrefersDark() ? "dark" : "light"),
    density: stored?.density ?? "comfortable",
  };
}

export interface AppearanceContextValue {
  theme: ThemeMode;
  density: DensityMode;
  setTheme: (theme: ThemeMode) => void;
  setDensity: (density: DensityMode) => void;
  toggleTheme: () => void;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [appearance, setAppearance] = useState<Appearance>(resolveInitial);

  // Применяем к <html> и сохраняем в localStorage при каждом изменении.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const root = document.documentElement;
    root.setAttribute("data-theme", appearance.theme);
    root.setAttribute("data-density", appearance.density);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(appearance));
    } catch {
      /* storage может быть недоступен (приватный режим, квота) — нефатально */
    }
  }, [appearance]);

  // Следим за системной темой только пока пользователь не выбрал свою.
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      if (readStored()?.theme) return; // явный выбор важнее системы
      setAppearance((prev) => ({ ...prev, theme: event.matches ? "dark" : "light" }));
    };
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);

  const setTheme = useCallback((theme: ThemeMode) => {
    setAppearance((prev) => ({ ...prev, theme }));
  }, []);

  const setDensity = useCallback((density: DensityMode) => {
    setAppearance((prev) => ({ ...prev, density }));
  }, []);

  const toggleTheme = useCallback(() => {
    setAppearance((prev) => ({
      ...prev,
      theme: prev.theme === "dark" ? "light" : "dark",
    }));
  }, []);

  const value = useMemo<AppearanceContextValue>(
    () => ({
      theme: appearance.theme,
      density: appearance.density,
      setTheme,
      setDensity,
      toggleTheme,
    }),
    [appearance.theme, appearance.density, setTheme, setDensity, toggleTheme],
  );

  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

export function useAppearance(): AppearanceContextValue {
  const ctx = useContext(AppearanceContext);
  if (!ctx) {
    throw new Error("useAppearance должен использоваться внутри <AppearanceProvider>");
  }
  return ctx;
}

/* ---------------------------------------------------------------------------
 * Универсальный сегментированный переключатель (role="group" + aria-pressed).
 * Используется в секции «Внешний вид» и потенциально в других local controls.
 * Клавиатурно доступен «из коробки»: кнопки фокусируемы, Enter/Space активируют.
 * ------------------------------------------------------------------------- */

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  icon?: IconName;
}

export function SegmentedToggle<T extends string>({
  label,
  value,
  options,
  onChange,
  size = "md",
}: {
  label: string;
  value: T;
  options: ReadonlyArray<SegmentedOption<T>>;
  onChange: (value: T) => void;
  size?: "sm" | "md";
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((opt) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            className={`segmented-option segmented-${size} ${selected ? "is-selected" : ""}`}
            aria-pressed={selected}
            onClick={() => onChange(opt.value)}
          >
            {opt.icon && <Icon name={opt.icon} size={size === "sm" ? 14 : 16} />}
            <span>{opt.label}</span>
          </button>
        );
      })}
    </div>
  );
}
