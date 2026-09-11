/**
 * Предпусковая проверка готовности пилота (Phase 14).
 *
 * Admin read-only, server-owned список с кодом, состоянием pass|warning|fail,
 * русским объяснением и следующим действием. Вердикт: готово | готово с
 * предупреждениями | запуск запрещён.
 *
 * Loading/empty/offline/error/retry, keyboard navigation, aria-live.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchPilotReadiness } from "../../api";
import { Button } from "../../design-system/components/Button";
import { SkeletonRows } from "../../design-system/components/StateViews";
import type { PilotReadiness, PilotReadinessCheck } from "../../types";
import "./readiness.css";

const STATUS_LABEL: Record<string, string> = {
  pass: "Готово",
  warning: "Предупреждение",
  fail: "Ошибка",
};

const STATUS_CLASS: Record<string, string> = {
  pass: "ok",
  warning: "warn",
  fail: "fail",
};

const VERDICT_LABEL: Record<string, { text: string; cls: string }> = {
  ready: { text: "Готово к пилоту", cls: "ok" },
  ready_with_warnings: { text: "Готово с предупреждениями", cls: "warn" },
  blocked: { text: "Запуск запрещён", cls: "fail" },
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU");
}

function CheckRow({ check }: { check: PilotReadinessCheck }) {
  return (
    <li className={`readiness-check readiness-${check.status}`} tabIndex={0} aria-label={`${check.code}: ${STATUS_LABEL[check.status]}`}>
      <div className="readiness-check-header">
        <span className={`readiness-pill ${STATUS_CLASS[check.status]}`} aria-hidden="true">
          {STATUS_LABEL[check.status]}
        </span>
        <code className="readiness-code">{check.code}</code>
      </div>
      <p className="readiness-message">{check.message_ru}</p>
      <p className="readiness-action">
        <strong>Действие:</strong> {check.next_action_ru}
      </p>
      {check.details && Object.keys(check.details).length > 0 && (
        <details className="readiness-details">
          <summary>Детали (без секретов)</summary>
          <pre className="readiness-pre">{JSON.stringify(check.details, null, 2)}</pre>
        </details>
      )}
    </li>
  );
}

export function ReadinessPage() {
  const [data, setData] = useState<PilotReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setForbidden(false);
    try {
      const res = await fetchPilotReadiness();
      setData(res);
    } catch (err) {
      if (err && typeof err === "object" && "status" in err) {
        const status = (err as { status: number }).status;
        if (status === 403) {
          setForbidden(true);
          setError("Недостаточно прав: требуется роль администратора и scope update_channel_manage или pilot_full_access.");
        } else if (status === 401) {
          setError("Требуется вход в систему.");
        } else if (status === 0) {
          setError("Сеть недоступна. Установленное приложение продолжает работать offline; повторите проверку позже.");
        } else {
          setError(`Не удалось загрузить проверку (HTTP ${status}).`);
        }
      } else {
        setError("Не удалось связаться с сервером.");
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) {
    return (
      <section className="panel readiness-panel" aria-busy="true" aria-labelledby="readiness-title">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <SkeletonRows rows={6} />
      </section>
    );
  }

  if (forbidden) {
    return (
      <section className="panel readiness-panel" aria-labelledby="readiness-title">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <p role="alert" className="readiness-result fail">
          {error}
        </p>
        <p className="muted">Обратитесь к администратору для выдачи scope.</p>
      </section>
    );
  }

  if (error && !data) {
    return (
      <section className="panel readiness-panel" aria-labelledby="readiness-title">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <p role="alert" className="readiness-result warn">
          {error}
        </p>
        <div className="readiness-actions">
          <Button variant="secondary" onClick={() => void load()}>
            Повторить попытку
          </Button>
        </div>
        <p className="muted">Офлайн не блокирует приложение: вы можете продолжать работу, проверка повторится позже.</p>
      </section>
    );
  }

  if (!data || data.checks.length === 0) {
    return (
      <section className="panel readiness-panel" aria-labelledby="readiness-title">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <p className="muted">Нет данных для отображения. Нажмите «Проверить».</p>
        <div className="readiness-actions">
          <Button variant="secondary" onClick={() => void load()}>
            Проверить
          </Button>
        </div>
      </section>
    );
  }

  const verdict = VERDICT_LABEL[data.verdict] ?? { text: data.verdict, cls: "warn" };

  return (
    <section className="panel readiness-panel" aria-labelledby="readiness-title">
      <h2 id="readiness-title">Проверка готовности пилота</h2>
      <p className="muted">Серверная проверка перед первым пилотом. Backend — граница безопасности; список формирует сервер.</p>

      <div className={`readiness-verdict readiness-verdict-${verdict.cls}`} role="status" aria-live="polite" tabIndex={0}>
        <strong>{verdict.text}</strong>
        <span className="muted"> • {formatTime(data.generated_at)}</span>
      </div>

      <ul className="readiness-list" role="list" aria-label="Проверки готовности">
        {data.checks.map((c) => (
          <CheckRow key={c.code} check={c} />
        ))}
      </ul>

      <div className="readiness-actions">
        <Button variant="secondary" onClick={() => void load()}>
          Проверить снова
        </Button>
      </div>

      <p className="muted" style={{ marginTop: "1rem" }}>
        Мутации (исправления) требуют явного подтверждения, CSRF и аудита. Эта страница только читает состояние.
      </p>
    </section>
  );
}
