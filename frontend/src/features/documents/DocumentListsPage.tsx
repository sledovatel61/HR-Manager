import { useCallback, useMemo, useState } from "react";
import { documentRequest as request } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Badge } from "../../design-system/components/StatusChip";
import { CANDIDATE_STAGE_ORDER, STAGE_LABELS, type CandidateStage } from "../../types";
import type { DocumentItem, DocumentList, DocumentLists, DocumentVersion } from "./types";
import { LoadState } from "./shared";
import { errorText, useResource } from "./hooks";
import { autoKey } from "./itemKey";
import "./documents.css";

/**
 * «Списки документов» — мастер вместо технического редактора
 * (UX feedback 2026-09-29, блок D).
 *
 * Прежняя форма требовала разбираться в «стабильных ключах», «области:
 * этап» и ручном управлении версиями. Теперь это три понятных шага
 * («Для чего список?» → «Какие документы нужны?» → предпросмотр), а
 * технические ключи и версионирование убраны в «Расширенные сведения».
 *
 * Ключи документов не исчезают: сервер требует их уникальными и по ним
 * хранит состояние документов у кандидатов, поэтому ключ генерируется
 * один раз при создании строки и больше не меняется автоматически — иначе
 * переименование документа «потеряло» бы его у уже прикреплённых
 * кандидатов.
 */

const states = {
  draft: "Черновик",
  published: "Опубликована",
  archived: "Архив",
} as const;

const MAX_ITEMS = 20;

function newItem(): DocumentItem {
  return { key: "", name: "", explanation: "", required: true };
}

interface EditorState {
  parent?: DocumentList;
  content: { name: string; description: string; items: DocumentItem[] };
  /** 0 — назначение, 1 — документы, 2 — предпросмотр. */
  step: number;
}

const STEPS = [
  { title: "Для чего список?", hint: "Название, описание и этап воронки, на котором он нужен." },
  { title: "Какие документы нужны?", hint: "Строки в том порядке, в котором их запрашивают." },
  { title: "Проверьте и сохраните", hint: "Так список увидят остальные. Публикация — отдельный шаг." },
] as const;

export function DocumentListsPage() {
  const load = useCallback(() => request<DocumentLists>("/document-lists"), []);
  const resource = useResource(load);
  const [editor, setEditor] = useState<EditorState | null>(null);
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
      step: 0,
      content: version
        ? {
            name: version.name,
            description: version.description,
            // Ключи сохраняем как есть: от них зависят документы кандидатов.
            items: version.items.map((i) => ({ ...i })),
          }
        : { name: "", description: "", items: [newItem()] },
    });
  };

  const patchItem = (index: number, patch: Partial<DocumentItem>) => {
    if (!editor) return;
    setEditor({
      ...editor,
      content: {
        ...editor.content,
        items: editor.content.items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
      },
    });
  };

  const addItem = () => {
    if (!editor) return;
    setEditor({
      ...editor,
      content: {
        ...editor.content,
        items: [...editor.content.items, newItem()],
      },
    });
  };

  const removeItem = (index: number) => {
    if (!editor) return;
    setEditor({
      ...editor,
      content: {
        ...editor.content,
        items: editor.content.items.filter((_, i) => i !== index),
      },
    });
  };

  const moveItem = (index: number, delta: number) => {
    if (!editor) return;
    const target = index + delta;
    if (target < 0 || target >= editor.content.items.length) return;
    const items = [...editor.content.items];
    [items[index], items[target]] = [items[target], items[index]];
    setEditor({ ...editor, content: { ...editor.content, items } });
  };

  /** Ключи проставляются один раз — при первом сохранении, не на каждый ввод. */
  const withKeys = useMemo(
    () => (items: DocumentItem[]) => {
      const taken = new Set<string>();
      return items.map((item) => {
        if (item.key) {
          taken.add(item.key);
          return item;
        }
        const key = autoKey(item.name, taken);
        taken.add(key);
        return { ...item, key };
      });
    },
    []
  );

  const itemsReady = (items: DocumentItem[]) =>
    items.length > 0 && items.every((item) => item.name.trim() !== "");
  const stepReady = useMemo(() => {
    if (!editor) return [false, false, false];
    const byPurpose = editor.content.name.trim() !== "";
    return [byPurpose, itemsReady(editor.content.items), byPurpose && itemsReady(editor.content.items)];
  }, [editor]);

  const submit = () => {
    if (!editor) return;
    const items = withKeys(editor.content.items);
    void mutate(() =>
      request(editor.parent ? `/document-lists/${editor.parent.id}/versions` : "/document-lists", {
        method: "POST",
        body: {
          name: editor.content.name.trim(),
          description: editor.content.description.trim(),
          items,
          ...(editor.parent ? { expected_version: editor.parent.version } : { stage: stage || null }),
        },
      }),
    );
  };

  return (
    <section className="documents-page">
      <h1>Списки документов</h1>
      <p>
        Список отвечает на вопрос «что нужно попросить у кандидата». Опубликованная
        версия неизменяемо: изменения создают новую версию, а уже применённые
        списки у кандидатов не меняются сами — их можно заменить вручную.
      </p>
      <LoadState {...resource} />
      {error && <p role="alert">{error}</p>}
      <button onClick={() => void resource.reload()}>Обновить данные</button>
      {resource.data?.can_manage && <button onClick={() => edit()}>Новый список</button>}
      {!resource.loading && !resource.error && resource.data?.items.length === 0 && (
        <p>Списков пока нет. Создайте первый — это займёт минуту.</p>
      )}

      {editor && (
        <form
          className="document-panel document-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <h2>{editor.parent ? "Новая версия списка" : "Новый список документов"}</h2>

          <ol className="wizard-steps" aria-label="Шаги создания списка">
            {STEPS.map((step, index) => (
              <li
                key={step.title}
                className={index === editor.step ? "is-current" : index < editor.step ? "is-done" : ""}
                aria-current={index === editor.step ? "step" : undefined}
              >
                <button
                  type="button"
                  disabled={index > 0 && !stepReady[index - 1]}
                  onClick={() => setEditor({ ...editor, step: index })}
                >
                  <span className="wizard-steps-index">{index + 1}</span>
                  {step.title}
                </button>
              </li>
            ))}
          </ol>

          {/* --- Шаг 1. Для чего список? --- */}
          {editor.step === 0 && (
            <section className="wizard-step" aria-label={STEPS[0].title}>
              <Field label="Название списка" required hint="Например: «Оформление на работу»">
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    required
                    maxLength={120}
                    value={editor.content.name}
                    onChange={(e) =>
                      setEditor({ ...editor, content: { ...editor.content, name: e.target.value } })
                    }
                  />
                )}
              </Field>
              <Field
                label="Описание"
                hint="Зачем нужен и для какой вакансии. Видно сотрудникам, применяющим список."
              >
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    maxLength={500}
                    value={editor.content.description}
                    onChange={(e) =>
                      setEditor({
                        ...editor,
                        content: { ...editor.content, description: e.target.value },
                      })
                    }
                  />
                )}
              </Field>
              <Field
                label="Когда применять"
                hint="Этап воронки, на котором список становится актуальным. Общий список подходит любому этапу."
              >
                {(id, describedBy) => (
                  <SelectInput
                    id={id}
                    aria-describedby={describedBy}
                    value={stage}
                    disabled={!!editor.parent}
                    onChange={(e) => setStage(e.target.value)}
                  >
                    <option value="">Общий список — для любого этапа</option>
                    {CANDIDATE_STAGE_ORDER.map((s) => (
                      <option key={s} value={s}>
                        {STAGE_LABELS[s]}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
            </section>
          )}

          {/* --- Шаг 2. Какие документы нужны? --- */}
          {editor.step === 1 && (
            <section className="wizard-step" aria-label={STEPS[1].title}>
              <p className="document-meta">
                Порядок сохраняется — так документы и запрашиваются. Технические ключи
                создаются автоматически, менять их не нужно.
              </p>
              {editor.content.items.map((item, index) => (
                <fieldset className="document-row" key={`row-${index}`}>
                  <legend>Документ {index + 1}</legend>
                  <Field label="Название" required>
                    {(id) => (
                      <TextInput
                        id={id}
                        required
                        maxLength={120}
                        value={item.name}
                        onChange={(e) => patchItem(index, { name: e.target.value })}
                      />
                    )}
                  </Field>
                  <Field label="Пояснение" hint="Что именно прислать: оригинал, копия, с печатью.">
                    {(id, describedBy) => (
                      <TextInput
                        id={id}
                        aria-describedby={describedBy}
                        maxLength={300}
                        value={item.explanation}
                        onChange={(e) => patchItem(index, { explanation: e.target.value })}
                      />
                    )}
                  </Field>
                  <label className="documents-checkbox">
                    <input
                      type="checkbox"
                      checked={item.required}
                      onChange={(e) => patchItem(index, { required: e.target.checked })}
                    />
                    Обязательный — без него оформление не завершится
                  </label>
                  <div className="document-actions">
                    <Button
                      type="button"
                      size="sm"
                      disabled={index === 0}
                      onClick={() => moveItem(index, -1)}
                    >
                      Выше
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      disabled={index === editor.content.items.length - 1}
                      onClick={() => moveItem(index, 1)}
                    >
                      Ниже
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      disabled={editor.content.items.length === 1}
                      onClick={() => removeItem(index)}
                    >
                      Убрать
                    </Button>
                  </div>
                </fieldset>
              ))}
              <Button
                type="button"
                icon="plus"
                disabled={editor.content.items.length >= MAX_ITEMS}
                onClick={addItem}
              >
                Добавить документ
              </Button>
            </section>
          )}

          {/* --- Шаг 3. Предпросмотр и сохранение --- */}
          {editor.step === 2 && (
            <section className="wizard-step" aria-label={STEPS[2].title}>
              <div className="list-preview">
                <h3>{editor.content.name || "Без названия"}</h3>
                <p className="document-meta">
                  {editor.content.description || "Без описания"} ·{" "}
                  {stage ? `этап «${STAGE_LABELS[stage as CandidateStage]}»` : "общий список"}
                </p>
                <ol>
                  {editor.content.items.map((item, index) => (
                    <li key={`preview-${index}`}>
                      <strong>{item.name || `Документ ${index + 1}`}</strong>{" "}
                      {item.required ? "— обязательный" : "— по желанию"}
                      {item.explanation ? ` (${item.explanation})` : ""}
                    </li>
                  ))}
                </ol>
              </div>
              <p className="document-meta">
                Сохранится <strong>черновик</strong>. Его увидят только сотрудники с
                правом управления списками; чтобы список стал доступен всем и
                появился в «Моих правилах», нажмите «Опубликовать» в списке ниже.
              </p>
            </section>
          )}

          <div className="wizard-nav">
            {editor.step > 0 && (
              <Button type="button" onClick={() => setEditor({ ...editor, step: editor.step - 1 })}>
                Назад
              </Button>
            )}
            {editor.step < STEPS.length - 1 ? (
              <Button
                type="button"
                variant="primary"
                disabled={!stepReady[editor.step]}
                onClick={() => setEditor({ ...editor, step: editor.step + 1 })}
              >
                Далее
              </Button>
            ) : (
              <Button type="submit" variant="primary" loading={busy} disabled={!stepReady[2]}>
                Сохранить черновик
              </Button>
            )}
            <Button type="button" variant="ghost" onClick={() => setEditor(null)}>
              Отмена
            </Button>
          </div>

          {/* Технические детали — по запросу, а не по умолчанию. */}
          <details className="rule-howto">
            <summary>Расширенные сведения: технические ключи</summary>
            <p className="document-meta">
              Ключ связывает строку с состоянием документа у кандидатов. Он
              создаётся автоматически и меняется только вручную — переименование
              документа не должно «терять» его у уже прикреплённых кандидатов.
            </p>
            {editor.content.items.map((item, index) => (
              <Field key={`key-${index}`} label={`Ключ: ${item.name || `документ ${index + 1}`}`}>
                {(id) => (
                  <TextInput
                    id={id}
                    maxLength={64}
                    value={item.key}
                    placeholder="создастся автоматически"
                    onChange={(e) => patchItem(index, { key: e.target.value })}
                  />
                )}
              </Field>
            ))}
          </details>
        </form>
      )}

      {resource.data?.items.map((list) => {
        const current = list.versions.find((v) => v.state === "published") ?? list.versions[0];
        return (
          <article key={list.id} className="document-panel">
            <header className="document-panel-head">
              <div>
                <h2>{current?.name}</h2>
                <p className="document-meta">
                  {list.stage ? STAGE_LABELS[list.stage as CandidateStage] : "Общий список"} ·{" "}
                  {list.versions.length}{" "}
                  {plural(list.versions.length, "версия", "версии", "версий")}
                </p>
              </div>
              {current && <Badge tone={current.state === "published" ? "success" : "neutral"}>{states[current.state]}</Badge>}
            </header>
            {current && (
              <details>
                <summary>Что увидит сотрудник</summary>
                <ol>
                  {current.items.map((item) => (
                    <li key={item.key}>
                      <strong>{item.name}</strong> {item.required ? "— обязательный" : "— по желанию"}
                      {item.explanation ? ` (${item.explanation})` : ""}
                      <span className="document-meta"> · ключ: {item.key}</span>
                    </li>
                  ))}
                </ol>
              </details>
            )}
            {list.versions.map((v) => (
              <details key={v.id}>
                <summary>
                  Версия {v.number} — {states[v.state]}
                </summary>
                <p>{v.description}</p>
                <ol>
                  {v.items.map((i) => (
                    <li key={i.key}>
                      {i.name} {i.required ? "(обязательный)" : "(необязательный)"}{" "}
                      {i.explanation}
                    </li>
                  ))}
                </ol>
                {resource.data?.can_manage && (
                  <div className="document-actions">
                    <button onClick={() => edit(list, v)}>Создать новую версию</button>
                    {v.state === "draft" && (
                      <button
                        disabled={busy}
                        onClick={() =>
                          void mutate(() =>
                            request(`/document-lists/${list.id}/versions/${v.id}/publish`, {
                              method: "POST",
                              body: { expected_version: list.version },
                            }),
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
                            request(`/document-lists/${list.id}/versions/${v.id}/archive`, {
                              method: "POST",
                              body: { expected_version: list.version },
                            }),
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
        );
      })}
    </section>
  );
}

function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = count % 100;
  if (mod100 >= 11 && mod100 <= 14) return many;
  const mod10 = count % 10;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}
