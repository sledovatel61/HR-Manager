import { PageHeader } from "../components/ui/PageHeader";
import { Button } from "../components/ui/Button";
import { useAppState } from "../state/AppState";
import "./settingsPage.css";

/**
 * Раздел "Настройки" также служит демонстрационной панелью для обязательных
 * состояний интерфейса (loading/error/permission/session), которые сложно
 * органично встроить в реальные экраны, не имитируя реальный backend.
 */
export function SettingsPage() {
  const { theme, toggleTheme, density, setDensity, simStatus, setSimStatus, expireSession, pushToast } = useAppState();

  return (
    <div>
      <PageHeader title="Настройки" description="Внешний вид и демонстрация состояний интерфейса." />

      <section className="settings-section">
        <h3>Оформление</h3>
        <div className="settings-row">
          <span>Тема</span>
          <Button variant="secondary" onClick={toggleTheme}>{theme === "dark" ? "Тёмная (переключить на светлую)" : "Светлая (переключить на тёмную)"}</Button>
        </div>
        <div className="settings-row">
          <span>Плотность интерфейса</span>
          <div className="settings-density-group" role="group" aria-label="Плотность интерфейса">
            <Button variant={density === "comfortable" ? "primary" : "secondary"} size="sm" onClick={() => setDensity("comfortable")}>Комфортная</Button>
            <Button variant={density === "compact" ? "primary" : "secondary"} size="sm" onClick={() => setDensity("compact")}>Компактная</Button>
          </div>
        </div>
      </section>

      <section className="settings-section">
        <h3>Демонстрация состояний (только для прототипа)</h3>
        <p className="settings-note">
          Эти переключатели не относятся к продукту — они нужны, чтобы показать deграded/offline-состояния
          интерфейса без реального backend.
        </p>
        <div className="settings-row">
          <span>Состояние соединения</span>
          <div className="settings-density-group" role="group" aria-label="Состояние соединения">
            <Button variant={simStatus === "online" ? "primary" : "secondary"} size="sm" onClick={() => setSimStatus("online")}>В сети</Button>
            <Button variant={simStatus === "degraded" ? "primary" : "secondary"} size="sm" onClick={() => setSimStatus("degraded")}>Нестабильно</Button>
            <Button variant={simStatus === "offline" ? "primary" : "secondary"} size="sm" onClick={() => setSimStatus("offline")}>Backend недоступен</Button>
          </div>
        </div>
        <div className="settings-row">
          <span>Сессия</span>
          <Button
            variant="danger"
            size="sm"
            onClick={() => {
              expireSession();
              pushToast("danger", "Сессия истекла (демо).");
            }}
          >
            Смоделировать истечение сессии
          </Button>
        </div>
      </section>
    </div>
  );
}
