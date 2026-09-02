import { useCallback, useEffect, useState } from "react";
import { fetchHealth } from "./api";
import type { BackendEnvironment, HealthResponse } from "./types";

const ENVIRONMENT_LABELS: Record<BackendEnvironment, string> = {
  development: "development",
  test: "test",
  production: "production",
};

/** Overall state shown to the user. */
type OverallStatus = "checking" | "online" | "database-error" | "offline";

interface AppProps {
  /** Injectable for tests; defaults to the real API call. */
  healthFetcher?: () => Promise<HealthResponse | null>;
}

export default function App({ healthFetcher = fetchHealth }: AppProps) {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [status, setStatus] = useState<OverallStatus>("checking");
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const check = useCallback(async () => {
    const report = await healthFetcher();
    setLastUpdated(new Date());
    if (report === null) {
      setHealth(null);
      setStatus("offline");
      return;
    }
    setHealth(report);
    const database = report.checks.database;
    setStatus(database?.status === "ok" ? "online" : "database-error");
  }, [healthFetcher]);

  useEffect(() => {
    void check();
  }, [check]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await check();
    } finally {
      setRefreshing(false);
    }
  };

  const database = health?.checks.database;

  const statusText: Record<OverallStatus, { tone: string; title: string; detail: string }> = {
    checking: {
      tone: "neutral",
      title: "Проверяем доступность…",
      detail: "Опрашиваем backend и базу данных.",
    },
    online: {
      tone: "good",
      title: "Система работает",
      detail: "Backend и база данных доступны.",
    },
    "database-error": {
      tone: "bad",
      title: "Проблема с базой данных",
      detail: "Backend отвечает, но PostgreSQL недоступен.",
    },
    offline: {
      tone: "bad",
      title: "Backend недоступен",
      detail: "Сервер не отвечает на запрос о состоянии.",
    },
  };

  const current = statusText[status];

  return (
    <div className="page">
      <header className="header">
        <h1>HR Manager</h1>
        <p>Технический каркас — этап 1: запускаемый скелет приложения</p>
      </header>

      <main className="panel">
        <section className={`status-card ${current.tone}`}>
          <span className="status-dot" aria-hidden="true" />
          <div>
            <h2 className="status-title">{current.title}</h2>
            <p className="status-detail">{current.detail}</p>
          </div>
        </section>

        <section className="details">
          <h3>Состояние компонентов</h3>
          <dl className="detail-list">
            <div className="detail-row">
              <dt>Backend</dt>
              <dd>{health === null && status === "offline" ? "недоступен" : "доступен"}</dd>
            </div>
            <div className="detail-row">
              <dt>База данных</dt>
              <dd className={database?.status === "ok" ? "good" : "bad"}>
                {database?.status === "ok"
                  ? `доступна (запрос занял ${database.latency_ms} мс)`
                  : database === undefined
                    ? "нет данных"
                    : "недоступна"}
              </dd>
            </div>
            {health && (
              <>
                <div className="detail-row">
                  <dt>Сервис</dt>
                  <dd>{health.service}</dd>
                </div>
                <div className="detail-row">
                  <dt>Версия</dt>
                  <dd>{health.version}</dd>
                </div>
                <div className="detail-row">
                  <dt>Окружение</dt>
                  <dd>
                    {ENVIRONMENT_LABELS[health.environment as BackendEnvironment]}
                    {health.environment === "production" && (
                      <span className="badge">production</span>
                    )}
                  </dd>
                </div>
              </>
            )}
          </dl>
        </section>

        <footer className="footer">
          <button
            type="button"
            className="refresh-button"
            onClick={() => void handleRefresh()}
            disabled={refreshing || status === "checking"}
          >
            {refreshing ? "Проверяем…" : "Проверить снова"}
          </button>
          <span className="last-updated">
            {lastUpdated
              ? `Последняя проверка: ${lastUpdated.toLocaleTimeString("ru-RU")}`
              : "Проверка ещё не выполнялась"}
          </span>
        </footer>
      </main>
    </div>
  );
}
