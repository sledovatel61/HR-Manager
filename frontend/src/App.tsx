import { useCallback, useEffect, useRef, useState } from "react";

import { DegradedHealthError, fetchHealth, type HealthResponse } from "./api/health";
import "./App.css";

const POLL_INTERVAL_MS = 5000;

/** Состояние, которое страница знает о backend. */
type BackendProbe =
  | { kind: "loading" }
  | { kind: "ok"; data: HealthResponse }
  /** Backend ответил, но зависимость недоступна (HTTP 503). */
  | { kind: "degraded"; data: HealthResponse }
  /** Backend недоступен вовсе (сеть/процесс). */
  | { kind: "unreachable" };

interface Card {
  title: string;
  value: string;
  tone: "up" | "down" | "unknown";
  hint: string;
}

function buildCards(probe: BackendProbe): Card[] {
  switch (probe.kind) {
    case "loading":
      return [
        {
          title: "Backend",
          value: "Проверяется…",
          tone: "unknown",
          hint: "Идёт запрос к /health",
        },
        {
          title: "База данных",
          value: "—",
          tone: "unknown",
          hint: "Нет данных",
        },
      ];
    case "ok":
      return [
        {
          title: "Backend",
          value: "Работает",
          tone: "up",
          hint: `Версия ${probe.data.version}`,
        },
        {
          title: "База данных",
          value: "Доступна",
          tone: "up",
          hint: "PostgreSQL отвечает на запросы",
        },
      ];
    case "degraded":
      return [
        {
          title: "Backend",
          value: "Работает",
          tone: "up",
          hint: `Версия ${probe.data.version}, HTTP 503`,
        },
        {
          title: "База данных",
          value: "Недоступна",
          tone: "down",
          hint: "Приложение живо, но PostgreSQL не отвечает",
        },
      ];
    case "unreachable":
      return [
        {
          title: "Backend",
          value: "Недоступен",
          tone: "down",
          hint: "Сервис не отвечает или ещё запускается",
        },
        {
          title: "База данных",
          value: "Неизвестно",
          tone: "unknown",
          hint: "Нет ответа от backend — состояние БД проверить нельзя",
        },
      ];
  }
}

const TONE_LABEL: Record<Card["tone"], string> = {
  up: "норма",
  down: "сбой",
  unknown: "нет данных",
};

export default function App() {
  const [probe, setProbe] = useState<BackendProbe>({ kind: "loading" });
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null);
  const requestCounter = useRef(0);

  const check = useCallback(async () => {
    const requestId = ++requestCounter.current;
    try {
      const data = await fetchHealth();
      if (requestId !== requestCounter.current) return;
      setProbe({ kind: "ok", data });
    } catch (error) {
      if (requestId !== requestCounter.current) return;
      if (error instanceof DegradedHealthError) {
        setProbe({ kind: "degraded", data: error.payload });
      } else if (error instanceof DOMException && error.name === "AbortError") {
        return;
      } else {
        setProbe({ kind: "unreachable" });
      }
    } finally {
      if (requestId === requestCounter.current) {
        setLastCheckedAt(new Date());
      }
    }
  }, []);

  useEffect(() => {
    // check() асинхронен: setState происходит только после завершения fetch,
    // поэтому синхронного обновления состояния в эффекте здесь нет.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void check();
    const timer = window.setInterval(() => void check(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [check]);

  const cards = buildCards(probe);

  return (
    <div className="page">
      <header className="header">
        <h1>HR Manager</h1>
        <p className="subtitle">
          Сетевая система подбора персонала — технический каркас (этап 1)
        </p>
      </header>

      <main>
        <section aria-label="Состояние сервисов" className="cards">
          {cards.map((card) => (
            <article key={card.title} className={`card card--${card.tone}`}>
              <h2>{card.title}</h2>
              <p className="card__value">{card.value}</p>
              <p className="card__hint">
                <span className={`badge badge--${card.tone}`}>{TONE_LABEL[card.tone]}</span>{" "}
                {card.hint}
              </p>
            </article>
          ))}
        </section>

        <section className="controls" aria-label="Управление проверкой">
          <button type="button" onClick={() => void check()}>
            Проверить сейчас
          </button>
          <p className="muted">
            {lastCheckedAt
              ? `Последняя проверка: ${lastCheckedAt.toLocaleTimeString("ru-RU")}`
              : "Проверка ещё не выполнялась"}
            {" · "}автоматически каждые {POLL_INTERVAL_MS / 1000} с
          </p>
        </section>
      </main>

      <footer className="footer muted">
        Контракт полей <code>/health</code>: status, database, version, checked_at
      </footer>
    </div>
  );
}
