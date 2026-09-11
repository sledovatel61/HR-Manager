/** Предпусковая проверка готовности пилота (Phase 14): read-only отчёт.

 * Сервер формирует список проверок {код, pass|warning|fail, русское
 * объяснение, следующее действие} и итоговый вердикт «готово | готово с
 * предупреждениями | запуск запрещён». Страница только отображает и
 * перечитывает результат: никаких автоисправлений и мутаций. SMTP/Telegram
 * необязательны (отсутствие = warning), офлайн-канал не блокирует работу.
 * Клавиатурная навигация: порядок фокуса повторяет порядок блоков, итог —
 * region role=status, каждая проверка читается скринридером.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchReadinessReport } from "../../api";
import { Button } from "../../design-system/components/Button";
import { SkeletonRows } from "../../design-system/components/StateViews";
import type { ReadinessReport } from "../../types";

const VERDICT_LABELS: Record<string, string> = {
  ready: "Готово к запуску",
  ready_with_warnings: "Готово с предупреждениями",
  blocked: "Запуск запрещён",
};

const STATE_LABELS: Record<string, string> = {
  pass: "ОК",
  warning: "Предупреждение",
  fail: "Ошибка",
};

export function ReadinessPage() {
  const [report, setReport] = useState<ReadinessReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [conflict, setConflict] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setConflict(null);
    try {
      const next = await fetchReadinessReport();
      setReport(next);
    } catch (err) {
      // 403/429 показываются отдельным сообщением; прочие сбои и офлайн
      // остаются без отчёта (report === null) — единый честный экран ниже.
      if (err && typeof err === "object" && "status" in err) {
        const code = (err as { status: number }).status;
        if (code === 403) {
          setConflict(
            "Недостаточно прав: проверка готовности доступна администратору с правом управления каналом обновлений."
          );
        } else if (code === 429) {
          setConflict("Слишком много запросов — повторите попытку позже.");
        }
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
      <section className="panel updates-panel" aria-busy="true">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <SkeletonRows rows={4} />
      </section>
    );
  }

  if (!report) {
    // Офлайн/ошибка/права: единый честный экран без отчёта, с повтором.
    return (
      <section className="panel updates-panel">
        <h2 id="readiness-title">Проверка готовности пилота</h2>
        <p className="update-result warn" role="alert">
          {conflict ??
            "Не удалось получить отчёт о готовности. Установленное приложение продолжает работать."}
        </p>
        <div className="update-actions">
          <Button variant="secondary" icon="loader" onClick={() => void load()}>
            Повторить попытку
          </Button>
        </div>
      </section>
    );
  }

  const verdict = report.verdict;
  const failed = report.checks.filter((check) => check.state === "fail");
  const warned = report.checks.filter((check) => check.state === "warning");
  const passed = report.checks.filter((check) => check.state === "pass");

  return (
    <section className="panel updates-panel" aria-labelledby="readiness-title">
      <h2 id="readiness-title">Проверка готовности пилота</h2>

      <div
        className={`update-pill ${verdict === "blocked" ? "warn" : verdict === "ready" ? "ok" : "info"}`}
        role="status"
        aria-live="polite"
      >
        {VERDICT_LABELS[verdict] ?? verdict}
      </div>
      <p className="muted" role="status">
        {report.verdict_ru}
      </p>

      <dl className="update-facts">
        <div>
          <dt>Версия релиза</dt>
          <dd>
            {report.release_version}
            <span className="update-sha"> (commit {report.release_sha.slice(0, 12)})</span>
          </dd>
        </div>
        <div>
          <dt>Проверок выполнено</dt>
          <dd>
            {passed.length} ОК, {warned.length} предупреждений, {failed.length} ошибок
          </dd>
        </div>
      </dl>

      <ul className="readiness-checks" aria-label="Результаты проверок">
        {report.checks.map((check) => (
          <li
            key={check.code}
            className={`readiness-check ${check.state}`}
            aria-label={`${check.title_ru}: ${STATE_LABELS[check.state] ?? check.state}`}
          >
            <div className="readiness-check-head">
              <span
                className={`update-pill ${check.state === "fail" ? "warn" : check.state === "pass" ? "ok" : "info"}`}
              >
                {STATE_LABELS[check.state] ?? check.state}
              </span>
              <strong>{check.title_ru}</strong>
            </div>
            <p className="readiness-explanation">{check.explanation_ru}</p>
            <p className="readiness-action muted">Следующее действие: {check.action_ru}</p>
            {check.details.length > 0 && (
              <p className="readiness-details muted">{check.details.join("; ")}</p>
            )}
          </li>
        ))}
      </ul>

      <p className="muted">
        Проверка доступна только для чтения: она ничего не изменяет и не
        выполняет автоисправлений. Отчёт не содержит путей, адресов, ключей и
        других данных.
      </p>

      <div className="update-actions">
        <Button variant="secondary" icon="loader" onClick={() => void load()}>
          Проверить снова
        </Button>
      </div>
    </section>
  );
}
