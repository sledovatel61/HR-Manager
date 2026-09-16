/** «Проверить готовность пилота» (Phase 14).
 *
 * Read-only отчёт администратора перед первым запуском. Backend — граница
 * безопасности: список проверок, формулировки и вердикт приходят с сервера,
 * клиент ничего не досчитывает и не «исправляет» автоматически. UI обязан
 * честно показывать loading/empty/offline/error/retry, состояния
 * pass | warning | fail и итог «готово | готово с предупреждениями |
 * запуск запрещён».
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchPilotReadiness } from "../../api";
import { Button } from "../../design-system/components/Button";
import { SkeletonRows } from "../../design-system/components/StateViews";
import type { PilotReadiness, PilotReadinessState } from "../../types";
import "./readiness.css";

const STATE_LABELS: Record<PilotReadinessState, string> = {
  pass: "Проверено",
  warning: "Предупреждение",
  fail: "Блокирует запуск",
};

const VERDICT_TONES: Record<PilotReadiness["verdict"], "ok" | "warn" | "bad"> = {
  "готово": "ok",
  "готово с предупреждениями": "warn",
  "запуск запрещён": "bad",
};

interface PilotReadinessPageProps {
  /** Инъекция для тестов; по умолчанию — реальный API-вызов. */
  fetcher?: () => Promise<PilotReadiness>;
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ru-RU");
}

export function PilotReadinessPage({ fetcher }: PilotReadinessPageProps) {
  const [report, setReport] = useState<PilotReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [denied, setDenied] = useState(false);
  const verdictRef = useRef<HTMLParagraphElement | null>(null);
  const firstLoad = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const next = await (fetcher ?? fetchPilotReadiness)();
      setReport(next);
      setDenied(false);
    } catch (err) {
      if (err && typeof err === "object" && "status" in err && (err as { status: number }).status === 403) {
        setDenied(true);
      } else {
        setError(true);
      }
    } finally {
      setLoading(false);
    }
  }, [fetcher]);

  useEffect(() => {
    void load();
  }, [load]);

  // После загрузки переводим фокус на вердикт: клавиатурный пользователь
  // сразу слышит/видит итог, а не список из десятков проверок.
  useEffect(() => {
    if (!loading && report && !firstLoad.current) {
      verdictRef.current?.focus();
    }
    firstLoad.current = false;
  }, [loading, report]);

  if (loading) {
    return (
      <section className="panel readiness-panel" aria-busy="true" aria-label="Проверка готовности пилота">
        <SkeletonRows rows={5} />
      </section>
    );
  }

  if (denied) {
    return (
      <section className="panel readiness-panel" aria-labelledby="readiness-title">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <p className="readiness-message warn" role="alert">
          Недостаточно прав: нужен администратор с подтверждённым правом
          <code> update_channel_manage</code>.
        </p>
      </section>
    );
  }

  if (error || !report) {
    return (
      <section className="panel readiness-panel" aria-labelledby="readiness-title">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <p className="readiness-message warn" role="alert">
          Не удалось получить отчёт готовности. Приложение продолжает работать; проверьте
          соединение с backend и повторите попытку.
        </p>
        <div className="readiness-actions">
          <Button variant="secondary" icon="loader" onClick={() => void load()}>
            Повторить попытку
          </Button>
        </div>
      </section>
    );
  }

  const { counts, checks } = report;
  const tone = VERDICT_TONES[report.verdict] ?? "warn";

  return (
    <section className="panel readiness-panel" aria-labelledby="readiness-title">
      <h2 id="readiness-title">Проверка готовности пилота</h2>
      <p className="readiness-generated">
        Отчёт сформирован сервером {formatDate(report.generated_at)} · версия {report.server_version}
        {report.host_evidence_fresh
          ? " · данные о хосте актуальны"
          : " · свежих данных о хосте нет (проверки хоста — предупреждение)"}
      </p>

      <p
        className={`readiness-verdict ${tone}`}
        role="status"
        aria-live="polite"
        tabIndex={-1}
        ref={verdictRef}
      >
        <strong>Вердикт: {report.verdict}.</strong>{" "}
        Проверено: {counts.pass}, предупреждений: {counts.warning}, блокирующих: {counts.fail}.
      </p>

      {counts.fail > 0 && (
        <p className="readiness-message warn" role="alert">
          Запуск запрещён: сначала устраните блокирующие проверки. Автоматических
          «исправлений» нет — каждое действие выполняет администратор осознанно.
        </p>
      )}

      {checks.length === 0 ? (
        <p className="readiness-message warn" role="alert">
          Сервер вернул пустой список проверок — это не считается готовностью. Обновите
          страницу или проверьте версию backend.
        </p>
      ) : (
        <ol className="readiness-list">
          {checks.map((check) => (
            <li key={check.code} className={`readiness-item ${check.state}`}>
              <div className="readiness-item-head">
                <h3 className="readiness-item-title">{check.title}</h3>
                <span className={`readiness-pill ${check.state}`}>
                  {STATE_LABELS[check.state] ?? check.state}
                </span>
              </div>
              <p className="readiness-detail">{check.detail}</p>
              <p className="readiness-action">
                <span className="readiness-action-label">Следующее действие:</span> {check.action}
              </p>
            </li>
          ))}
        </ol>
      )}

      <div className="readiness-actions">
        <Button variant="secondary" icon="loader" onClick={() => void load()}>
          Проверить снова
        </Button>
      </div>
      <p className="readiness-note">
        Отчёт только читает состояние: ни база, ни контейнеры, ни канал обновлений здесь не
        меняются. Мутирующие операции — отдельные эндпоинты с подтверждением и CSRF.
      </p>
    </section>
  );
}
