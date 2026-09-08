import { useCallback, useState } from "react";
import { documentRequest as request } from "../../api";
import {
  CANDIDATE_STAGE_ORDER,
  STAGE_LABELS,
  type CandidateStage,
} from "../../types";
import type {
  DocumentLists,
  DocumentRule,
  RuleExecution,
  RuleInput,
  RuleParams,
} from "./types";
import { LoadState } from "./shared";
import { errorText, useResource } from "./hooks";
import "./documents.css";
const actions = {
  apply_list: "Применить актуальный список, если списка ещё нет",
  document_request: "Поставить запрос документов",
  document_reminder: "Поставить напоминание о документах",
};
const outcomes: Record<string, string> = {
  applied: "Список применён",
  queued: "Сообщение в очереди",
  skipped: "Условия не выполнены",
  cancelled: "Отменено",
  failed: "Ошибка исполнения",
};
const blank: RuleInput = {
  name: "",
  enabled: true,
  params: {
    trigger: "stage_transition",
    action: "apply_list",
    stage: "new",
    list_id: "",
    list_version_id: null,
    missing_required: true,
    channel: null,
    days: null,
  },
};
export function MyRulesPage() {
  const load = useCallback(async () => {
    const [rules, lists] = await Promise.all([
      request<DocumentRule[]>("/document-rules"),
      request<DocumentLists>("/document-lists"),
    ]);
    return { rules, lists };
  }, []);
  const resource = useResource(load);
  const [editor, setEditor] = useState<{
    id?: string;
    version?: number;
    input: RuleInput;
  } | null>(null);
  const [history, setHistory] = useState<{
    name: string;
    rows: RuleExecution[];
  } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const mutate = async (action: () => Promise<unknown>, reload = true) => {
    setBusy(true);
    setError("");
    try {
      await action();
      if (reload) {
        setEditor(null);
        await resource.reload();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const params = (change: Partial<RuleParams>) => {
    if (editor)
      setEditor({
        ...editor,
        input: {
          ...editor.input,
          params: { ...editor.input.params, ...change },
        },
      });
  };
  const published =
    resource.data?.lists.items.flatMap((l) =>
      l.versions.filter((v) => v.state === "published"),
    ) ?? [];
  return (
    <section className="documents-page">
      <h1>Мои правила</h1>
      <p>
        Правила работают только с доступными вам кандидатами. Тихие часы,
        рабочие дни и часовой пояс берутся из ваших настроек уведомлений.
      </p>
      <LoadState {...resource} />
      {error && <p role="alert">{error}</p>}
      <div className="document-actions">
        <button
          onClick={() => {
            setEditor({ input: structuredClone(blank) });
            setError("");
          }}
        >
          Создать правило
        </button>
        <button onClick={() => void resource.reload()}>Обновить правила</button>
      </div>
      {!resource.loading &&
        !resource.error &&
        resource.data?.rules.length === 0 && <p>Правил пока нет.</p>}
      {editor && (
        <form
          className="document-panel document-form"
          onSubmit={(e) => {
            e.preventDefault();
            void mutate(() =>
              request(
                editor.id ? `/document-rules/${editor.id}` : "/document-rules",
                {
                  method: editor.id ? "PUT" : "POST",
                  body: {
                    ...editor.input,
                    ...(editor.id ? { expected_version: editor.version } : {}),
                  },
                },
              ),
            );
          }}
        >
          <h2>{editor.id ? "Редактирование правила" : "Новое правило"}</h2>
          <label>
            Название правила
            <input
              required
              maxLength={120}
              value={editor.input.name}
              onChange={(e) =>
                setEditor({
                  ...editor,
                  input: { ...editor.input, name: e.target.value },
                })
              }
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={editor.input.enabled}
              onChange={(e) =>
                setEditor({
                  ...editor,
                  input: { ...editor.input, enabled: e.target.checked },
                })
              }
            />
            Включено
          </label>
          <label>
            Триггер
            <select
              value={editor.input.params.trigger}
              onChange={(e) =>
                params(
                  e.target.value === "scheduled_reminder"
                    ? {
                        trigger: "scheduled_reminder",
                        action: "document_reminder",
                        days: 1,
                        channel: "email",
                      }
                    : { trigger: "stage_transition" },
                )
              }
            >
              <option value="stage_transition">Кандидат перешёл на этап</option>
              <option value="scheduled_reminder">
                Наступил срок, документы не получены
              </option>
            </select>
          </label>
          <label>
            Этап
            <select
              value={editor.input.params.stage}
              onChange={(e) =>
                params({
                  stage: e.target.value as CandidateStage,
                  list_id: "",
                  list_version_id: null,
                })
              }
            >
              {CANDIDATE_STAGE_ORDER.map((s) => (
                <option key={s} value={s}>
                  {STAGE_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Действие
            <select
              value={editor.input.params.action}
              onChange={(e) => {
                const action = e.target.value as RuleParams["action"];
                params({
                  action,
                  channel: action === "apply_list" ? null : "email",
                  days: action === "document_reminder" ? 1 : null,
                  list_version_id: null,
                });
              }}
            >
              {Object.entries(actions)
                .filter(
                  ([key]) =>
                    editor.input.params.trigger !== "scheduled_reminder" ||
                    key === "document_reminder",
                )
                .map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Список
            <select
              required
              value={editor.input.params.list_id}
              onChange={(e) =>
                params({ list_id: e.target.value, list_version_id: null })
              }
            >
              <option value="">Выберите опубликованный список</option>
              {published
                .filter(
                  (v) => !v.stage || v.stage === editor.input.params.stage,
                )
                .map((v) => (
                  <option key={v.id} value={v.list_id}>
                    {v.name}
                  </option>
                ))}
            </select>
          </label>
          {editor.input.params.action !== "apply_list" && (
            <>
              <label>
                Условие версии
                <select
                  value={editor.input.params.list_version_id ?? ""}
                  onChange={(e) =>
                    params({ list_version_id: e.target.value || null })
                  }
                >
                  <option value="">
                    Любая применённая версия выбранного списка
                  </option>
                  {published
                    .filter((v) => v.list_id === editor.input.params.list_id)
                    .map((v) => (
                      <option key={v.id} value={v.id}>
                        Только версия {v.number}
                      </option>
                    ))}
                </select>
              </label>
              <p>
                Обязательное условие: есть недостающие обязательные документы.
              </p>
              <label>
                Канал
                <select
                  value={editor.input.params.channel ?? "email"}
                  onChange={(e) =>
                    params({ channel: e.target.value as "email" | "telegram" })
                  }
                >
                  <option value="email">Электронная почта</option>
                  <option value="telegram">Telegram</option>
                </select>
              </label>
            </>
          )}
          {editor.input.params.action === "document_reminder" && (
            <label>
              Через сколько дней (1–30)
              <input
                type="number"
                min={1}
                max={30}
                step={1}
                required
                value={editor.input.params.days ?? 1}
                onChange={(e) => params({ days: Number(e.target.value) })}
              />
            </label>
          )}
          <p>
            Напоминание исполняется один раз для каждого снимка и версии
            правила. Редактирование отменяет ожидающие задания предыдущей
            версии.
          </p>
          <div className="document-actions">
            <button disabled={busy}>Сохранить правило</button>
            <button type="button" onClick={() => setEditor(null)}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {resource.data?.rules.map((rule) => (
        <article className="document-panel" key={rule.id}>
          <h2>{rule.name}</h2>
          <p>
            {rule.enabled ? "Включено" : "Выключено"} · версия {rule.version} ·{" "}
            {STAGE_LABELS[rule.params.stage]}
          </p>
          <p>
            {actions[rule.params.action]}
            {rule.params.days ? ` через ${rule.params.days} дн.` : ""}
          </p>
          <div className="document-actions">
            <button
              disabled={busy}
              onClick={() =>
                setEditor({
                  id: rule.id,
                  version: rule.version,
                  input: {
                    name: rule.name,
                    enabled: rule.enabled,
                    params: structuredClone(rule.params),
                  },
                })
              }
            >
              Редактировать
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void mutate(() =>
                  request(`/document-rules/${rule.id}`, {
                    method: "PUT",
                    body: {
                      name: rule.name,
                      enabled: !rule.enabled,
                      params: rule.params,
                      expected_version: rule.version,
                    },
                  }),
                )
              }
            >
              {rule.enabled ? "Выключить" : "Включить"}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void mutate(
                  async () =>
                    setHistory({
                      name: rule.name,
                      rows: await request<RuleExecution[]>(
                        `/document-rules/${rule.id}/history`,
                      ),
                    }),
                  false,
                )
              }
            >
              История срабатываний
            </button>
          </div>
        </article>
      ))}
      {history && (
        <section className="document-panel">
          <h2>История: {history.name}</h2>
          {history.rows.length === 0 ? (
            <p>Срабатываний в текущей области доступа нет.</p>
          ) : (
            <table className="document-table">
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Версия правила</th>
                  <th>Действие</th>
                  <th>Результат</th>
                </tr>
              </thead>
              <tbody>
                {history.rows.map((row) => (
                  <tr key={row.id}>
                    <td>{new Date(row.created_at).toLocaleString("ru")}</td>
                    <td>{row.rule_version}</td>
                    <td>{actions[row.action as keyof typeof actions]}</td>
                    <td>{outcomes[row.outcome] ?? row.outcome}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <button onClick={() => setHistory(null)}>Закрыть историю</button>
        </section>
      )}
    </section>
  );
}
