/**
 * «Внешний вид» — заметная, всегда доступная (не зависит от роли) секция
 * в «Настройках». Меняет глобальную тему и плотность через useAppearance,
 * не ломая role-filtered группы настроек ниже.
 *
 * Контролы клавиатурно доступны (role="group" + aria-pressed), управляют
 * единым состоянием оболочки и переживают перезагрузку (см. appearance.tsx).
 */
import { Icon } from "../design-system/icons/Icon";
import {
  SegmentedToggle,
  useAppearance,
  type DensityMode,
  type ThemeMode,
} from "./appearance";

const THEME_OPTIONS: ReadonlyArray<{ value: ThemeMode; label: string; icon: "sun" | "moon" }> = [
  { value: "light", label: "Светлая", icon: "sun" },
  { value: "dark", label: "Тёмная", icon: "moon" },
];

const DENSITY_OPTIONS: ReadonlyArray<{ value: DensityMode; label: string }> = [
  { value: "comfortable", label: "Комфортная" },
  { value: "compact", label: "Компактная" },
];

export function AppearanceControls() {
  const { theme, density, setTheme, setDensity } = useAppearance();

  return (
    <section className="appearance-card" aria-labelledby="appearance-heading">
      <div className="appearance-card-head">
        <span className="appearance-card-icon" aria-hidden="true">
          <Icon name="layout-grid" size={18} />
        </span>
        <div>
          <h2 id="appearance-heading" className="appearance-card-title">
            Внешний вид
          </h2>
          <p className="appearance-card-description">
            Тема оформления и плотность интерфейса применяются ко всему приложению
            и сохраняются на этом устройстве.
          </p>
        </div>
      </div>
      <div className="appearance-rows">
        <div className="appearance-row">
          <span className="appearance-row-label" id="appearance-theme-label">
            Тема
          </span>
          <SegmentedToggle<ThemeMode>
            label="Тема оформления"
            value={theme}
            onChange={setTheme}
            options={THEME_OPTIONS}
          />
        </div>
        <div className="appearance-row">
          <span className="appearance-row-label" id="appearance-density-label">
            Плотность
          </span>
          <SegmentedToggle<DensityMode>
            label="Плотность интерфейса"
            value={density}
            onChange={setDensity}
            options={DENSITY_OPTIONS}
          />
        </div>
      </div>
    </section>
  );
}
