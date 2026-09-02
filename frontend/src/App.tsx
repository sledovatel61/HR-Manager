import { useCallback, useEffect, useState } from "react";
import { Activity, Database, RefreshCw, Server } from "lucide-react";
import { classifyHealth, fetchHealth, type ServiceHealth } from "./api/health";

const POLL_INTERVAL_MS = 10_000;

const INITIAL_STATE: ServiceHealth = { backend: "checking", database: "unknown" };

interface StatusTone {
  label: string;
  className: string;
}

const BACKEND_TONES: Record<ServiceHealth["backend"], StatusTone> = {
  checking: { label: "Проверка…", className: "tone-checking" },
  ok: { label: "Работает", className: "tone-ok" },
  degraded: { label: "Неисправен (БД недоступна)", className: "tone-bad" },
  unreachable: { label: "Недоступен", className: "tone-bad" },
};

const DATABASE_TONES: Record<ServiceHealth["database"], StatusTone> = {
  unknown: { label: "Неизвестно", className: "tone-checking" },
  ok: { label: "Подключена", className: "tone-ok" },
  unavailable: { label: "Недоступна", className: "tone-bad" },
};

function formatTime(date: Date | null): string {
  return date ? date.toLocaleTimeString("ru-RU") : "—";
}

export default function App() {
  const [health, setHealth] = useState<ServiceHealth>(INITIAL_STATE);
  const [detail, setDetail] = useState("Проверяем связь с backend…");
  const [checkedAt, setCheckedAt] = useState<Date | null>(null);

  const runCheck = useCallback(async () => {
    try {
      const payload = await fetchHealth();
      setHealth(classifyHealth(payload));
      setDetail(
        payload.status === "ok"
          ? `HR Manager API · версия ${payload.version} · окружение ${payload.environment}`
          : `API ответил, но состояние нездоровое (HTTP 503)`,
      );
    } catch (error) {
      setHealth({ backend: "unreachable", database: "unknown" });
      setDetail(error instanceof Error ? error.message : "Backend недоступен");
    } finally {
      setCheckedAt(new Date());
    }
  }, []);

  useEffect(() => {
    void runCheck();
    const timer = window.setInterval(() => void runCheck(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [runCheck]);

  const backendTone = BACKEND_TONES[health.backend];
  const databaseTone = DATABASE_TONES[health.database];
  const overallOk = health.backend === "ok" && health.database === "ok";

  return (
    <main className="page">
      <header className="hero">
        <h1 className="hero-title">
          <Activity aria-hidden /> HR Manager
        </h1>
        <p className="hero-subtitle">Технический каркас · Этап 1</p>
      </header>

      <section
        className="status-card"
        aria-live="polite"
        data-testid="overall-status"
        data-overall={overallOk ? "ok" : health.backend === "checking" ? "checking" : "bad"}
      >
        <div className="status-row">
          <div className="service">
            <Server aria-hidden />
            <div>
              <span className="service-name">Backend API</span>
              <span className={`pill ${backendTone.className}`}>
                <span className="dot" aria-hidden />
                {backendTone.label}
              </span>
            </div>
          </div>

          <div className="service">
            <Database aria-hidden />
            <div>
              <span className="service-name">База данных (PostgreSQL)</span>
              <span className={`pill ${databaseTone.className}`}>
                <span className="dot" aria-hidden />
                {databaseTone.label}
              </span>
            </div>
          </div>
        </div>

        <div className="status-footer">
          <span className="detail" data-testid="health-detail">
            {detail}
          </span>
          <span className="checked-at">Проверено: {formatTime(checkedAt)}</span>
          <button type="button" className="refresh-button" onClick={() => void runCheck()}>
            <RefreshCw size={16} aria-hidden /> Проверить снова
          </button>
        </div>
      </section>

      <footer className="hint">
        Автообновление каждые {POLL_INTERVAL_MS / 1000} секунд. Backend доступен по
        относительному пути <code>/health</code> через dev-прокси.
      </footer>
    </main>
  );
}
