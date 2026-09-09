import { useCallback, useState } from "react";
import { documentRequest as request } from "../../api";
import {
  CANDIDATE_STAGE_ORDER,
  STAGE_LABELS,
  type CandidateStage,
} from "../../types";
import type {
  DocumentList,
  DocumentLists,
  DocumentVersion,
  VersionInput,
} from "./types";
import { LoadState } from "./shared";
import { errorText, useResource } from "./hooks";
import "./documents.css";
const states = {
  draft: "Черновик",
  published: "Опубликована",
  archived: "Архив",
};
const blank: VersionInput = {
  name: "",
  description: "",
  items: [{ key: "document_1", name: "", explanation: "", required: true }],
};
export function DocumentListsPage() {
  const load = useCallback(() => request<DocumentLists>("/document-lists"), []);
  const resource = useResource(load);
  const [editor, setEditor] = useState<{
    parent?: DocumentList;
    content: VersionInput;
  } | null>(null);
  const [stage, setStage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      setEditor(null);
      await resource.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const edit = (parent?: DocumentList, version?: DocumentVersion) => {
    setError("");
    setStage(parent?.stage ?? "");
    setEditor({
      parent,
      content: version
        ? {
            name: version.name,
            description: version.description,
            items: version.items.map((i) => ({ ...i })),
          }
        : structuredClone(blank),
    });
  };
  return (
    <section className="documents-page">
      <h1>Списки документов</h1>
      <p>
        Опубликованное содержимое неизменяемо. Изменения создают новый черновик;
        списки кандидатов не обновляются автоматически.
      </p>
      <LoadState {...resource} />
      {error && <p role="alert">{error}</p>}
      <button onClick={() => void resource.reload()}>Обновить данные</button>
      {resource.data?.can_manage && (
        <button onClick={() => edit()}>Новый список</button>
      )}
      {!resource.loading &&
        !resource.error &&
        resource.data?.items.length === 0 && <p>Списков пока нет.</p>}
      {editor && (
        <form
          className="document-panel document-form"
          onSubmit={(e) => {
            e.preventDefault();
            void mutate(() =>
              request(
                editor.parent
                  ? `/document-lists/${editor.parent.id}/versions`
                  : "/document-lists",
                {
                  method: "POST",
                  body: {
                    ...editor.content,
                    ...(editor.parent
                      ? { expected_version: editor.parent.version }
                      : { stage: stage || null }),
                  },
                },
              ),
            );
          }}
        >
          <h2>{editor.parent ? "Новая версия" : "Новый список"}</h2>
          <label>
            Название
            <input
              required
              maxLength={120}
              value={editor.content.name}
              onChange={(e) =>
                setEditor({
                  ...editor,
                  content: { ...editor.content, name: e.target.value },
                })
              }
            />
          </label>
          <label>
            Описание
            <textarea
              maxLength={500}
              value={editor.content.description}
              onChange={(e) =>
                setEditor({
                  ...editor,
                  content: { ...editor.content, description: e.target.value },
                })
              }
            />
          </label>
          <label>
            Область: этап
            <select
              value={stage}
              disabled={!!editor.parent}
              onChange={(e) => setStage(e.target.value)}
            >
              <option value="">Общий список</option>
              {CANDIDATE_STAGE_ORDER.map((s) => (
                <option key={s} value={s}>
                  {STAGE_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          {editor.content.items.map((item, index) => (
            <fieldset key={index}>
              <legend>Документ {index + 1}</legend>
              {(["key", "name", "explanation"] as const).map((field) => (
                <label key={field}>
                  {field === "key"
                    ? "Стабильный ключ"
                    : field === "name"
                      ? "Русское название"
                      : "Пояснение"}
                  <input
                    required={field !== "explanation"}
                    maxLength={
                      field === "key" ? 64 : field === "name" ? 120 : 300
                    }
                    value={item[field]}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        content: {
                          ...editor.content,
                          items: editor.content.items.map((v, i) =>
                            i === index ? { ...v, [field]: e.target.value } : v,
                          ),
                        },
                      })
                    }
                  />
                </label>
              ))}
              <label>
                <input
                  type="checkbox"
                  checked={item.required}
                  onChange={(e) =>
                    setEditor({
                      ...editor,
                      content: {
                        ...editor.content,
                        items: editor.content.items.map((v, i) =>
                          i === index
                            ? { ...v, required: e.target.checked }
                            : v,
                        ),
                      },
                    })
                  }
                />
                Обязательный
              </label>
              <div className="document-actions">
                <button
                  type="button"
                  disabled={index === 0}
                  onClick={() => {
                    const items = [...editor.content.items];
                    [items[index - 1], items[index]] = [
                      items[index],
                      items[index - 1],
                    ];
                    setEditor({
                      ...editor,
                      content: { ...editor.content, items },
                    });
                  }}
                >
                  Выше
                </button>
                <button
                  type="button"
                  disabled={editor.content.items.length === 1}
                  onClick={() =>
                    setEditor({
                      ...editor,
                      content: {
                        ...editor.content,
                        items: editor.content.items.filter(
                          (_, i) => i !== index,
                        ),
                      },
                    })
                  }
                >
                  Убрать позицию
                </button>
              </div>
            </fieldset>
          ))}
          <button
            type="button"
            disabled={editor.content.items.length >= 20}
            onClick={() =>
              setEditor({
                ...editor,
                content: {
                  ...editor.content,
                  items: [
                    ...editor.content.items,
                    {
                      key: `doc_${crypto.randomUUID().slice(0, 8)}`,
                      name: "",
                      explanation: "",
                      required: true,
                    },
                  ],
                },
              })
            }
          >
            Добавить документ
          </button>
          <div className="document-actions">
            <button disabled={busy}>Сохранить черновик</button>
            <button type="button" onClick={() => setEditor(null)}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {resource.data?.items.map((list) => (
        <article key={list.id} className="document-panel">
          <h2>{list.versions[0]?.name}</h2>
          <p>
            {list.stage
              ? STAGE_LABELS[list.stage as CandidateStage]
              : "Общий список"}{" "}
            · ревизия {list.version}
          </p>
          {list.versions.map((v) => (
            <details key={v.id}>
              <summary>
                Версия {v.number} — {states[v.state]}
              </summary>
              <p>{v.description}</p>
              <ol>
                {v.items.map((i) => (
                  <li key={i.key}>
                    {i.name}{" "}
                    {i.required ? "(обязательный)" : "(необязательный)"}{" "}
                    {i.explanation}
                  </li>
                ))}
              </ol>
              {resource.data?.can_manage && (
                <div className="document-actions">
                  <button onClick={() => edit(list, v)}>
                    Создать новую версию
                  </button>
                  {v.state === "draft" && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void mutate(() =>
                          request(
                            `/document-lists/${list.id}/versions/${v.id}/publish`,
                            {
                              method: "POST",
                              body: { expected_version: list.version },
                            },
                          ),
                        )
                      }
                    >
                      Опубликовать
                    </button>
                  )}
                  {v.state !== "archived" && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void mutate(() =>
                          request(
                            `/document-lists/${list.id}/versions/${v.id}/archive`,
                            {
                              method: "POST",
                              body: { expected_version: list.version },
                            },
                          ),
                        )
                      }
                    >
                      Архивировать
                    </button>
                  )}
                </div>
              )}
            </details>
          ))}
        </article>
      ))}
    </section>
  );
}
