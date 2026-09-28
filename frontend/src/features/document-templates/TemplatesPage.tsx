import { useCallback, useEffect, useState } from "react";
import {
  activateDocumentTemplateVersion,
  addDocumentTemplateVersion,
  archiveDocumentTemplateVersion,
  createDocumentTemplate,
  listDocumentTemplates,
  listTemplatePlaceholders,
  renameDocumentTemplate,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { Badge } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import {
  CANDIDATE_STAGE_ORDER,
  STAGE_LABELS,
  type CandidateStage,
  type DocumentTemplate,
  type DocumentTemplateVersion,
  type StageTone,
  type TemplatePlaceholder,
  type TemplateVersionState,
} from "../../types";
import { errorText } from "../documents/hooks";
import "./documentTemplates.css";

const STATE_LABELS: Record<TemplateVersionState, string> = {
  draft: "Черновик",
  active: "Опубликована",
  archived: "Архив",
};

const STATE_TONE: Record<TemplateVersionState, StageTone> = {
  draft: "amber",
  active: "success",
  archived: "neutral",
};

/** Kinds stay a controlled key list: a free-text kind would break reports. */
const KIND_OPTIONS: { value: string; label: string }[] = [
  { value: "offer", label: "Оффер" },
  { value: "anketa", label: "Анкета" },
  { value: "dogovor", label: "Договор" },
  { value: "script", label: "Скрипт" },
  { value: "form", label: "Форма" },
];

interface VersionDraft {
  title: string;
  body: string;
}

type EditorState =
  | { mode: "create"; kind: string; name: string; scope: string; draft: VersionDraft }
  | { mode: "version"; template: DocumentTemplate; draft: VersionDraft };

export function TemplatesPage() {
  const { pushToast } = useToast();
  const [templates, setTemplates] = useState<DocumentTemplate[] | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [placeholders, setPlaceholders] = useState<TemplatePlaceholder[]>([]);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [rename, setRename] = useState<{ template: DocumentTemplate; name: string } | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const [list, catalog] = await Promise.all([
        listDocumentTemplates(),
        listTemplatePlaceholders(),
      ]);
      setTemplates(list.items);
      setCanManage(list.can_manage);
      setPlaceholders(catalog.items);
    } catch (error) {
      setLoadError(errorText(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async (action: () => Promise<unknown>, notice: string) => {
    setBusy(true);
    try {
      await action();
      pushToast("success", notice);
      setEditor(null);
      setRename(null);
      await load();
    } catch (error) {
      pushToast("danger", errorText(error));
    } finally {
      setBusy(false);
    }
  };

  const kindLabel = (kind: string) =>
    KIND_OPTIONS.find((option) => option.value === kind)?.label ?? kind;

  return (
    <section className="templates-page">
      <h1>Шаблоны документов</h1>
      <p>
        Текстовые шаблоны с версиями. Опубликованная версия неизменяема: правка — это
        новая версия. Файлы не загружаются, документы кандидату не отправляются.
      </p>

      {loadError && <ErrorState onRetry={() => void load()} />}
      {!templates && !loadError && <SkeletonRows rows={4} columns={3} />}

      {templates && (
        <>
          <div className="templates-toolbar">
            <Button
              variant="primary"
              icon="plus"
              disabled={!canManage || busy}
              onClick={() =>
                setEditor({
                  mode: "create",
                  kind: "offer",
                  name: "",
                  scope: "",
                  draft: { title: "", body: "" },
                })
              }
            >
              Новый шаблон
            </Button>
            <span className="template-meta">
              {canManage
                ? "Управление доступно: администратор или право document_lists_manage."
                : "Только просмотр опубликованных версий: для правок нужно право document_lists_manage."}
            </span>
          </div>

          {templates.length === 0 && <p>Шаблонов пока нет.</p>}

          {templates.map((template) => {
            const active = template.versions.find((v) => v.state === "active");
            const latest = template.versions[template.versions.length - 1];
            return (
              <article key={template.id} className="template-card">
                <header className="template-card-head">
                  <div>
                    <h2>{template.name}</h2>
                    <p className="template-meta">
                      {kindLabel(template.kind)} ·{" "}
                      {template.scope
                        ? STAGE_LABELS[template.scope as CandidateStage]
                        : "Все этапы"}{" "}
                      · ревизия {template.revision} · версий {template.versions.length}
                    </p>
                  </div>
                  {active ? (
                    <Badge tone="success">Опубликована v{active.number}</Badge>
                  ) : (
                    <Badge tone="neutral">Нет опубликованной версии</Badge>
                  )}
                </header>

                <div className="template-actions">
                  <Button
                    size="sm"
                    disabled={!canManage || busy}
                    onClick={() =>
                      setEditor({
                        mode: "version",
                        template,
                        draft: { title: latest?.title ?? "", body: latest?.body ?? "" },
                      })
                    }
                  >
                    Новая версия
                  </Button>
                  <Button
                    size="sm"
                    disabled={!canManage || busy}
                    onClick={() => setRename({ template, name: template.name })}
                  >
                    Переименовать
                  </Button>
                </div>

                <details>
                  <summary>Версии и статусы</summary>
                  <table className="templates-table">
                    <thead>
                      <tr>
                        <th>№</th>
                        <th>Заголовок</th>
                        <th>Статус</th>
                        <th>Плейсхолдеры</th>
                        <th>Действия</th>
                      </tr>
                    </thead>
                    <tbody>
                      {template.versions.map((version) => (
                        <VersionRow
                          key={version.id}
                          version={version}
                          canManage={canManage}
                          busy={busy}
                          onActivate={() =>
                            void run(
                              () =>
                                activateDocumentTemplateVersion(
                                  template.id,
                                  version.id,
                                  template.revision,
                                ),
                              `Версия ${version.number} опубликована`,
                            )
                          }
                          onArchive={() =>
                            void run(
                              () =>
                                archiveDocumentTemplateVersion(
                                  template.id,
                                  version.id,
                                  template.revision,
                                ),
                              `Версия ${version.number} в архиве`,
                            )
                          }
                        />
                      ))}
                    </tbody>
                  </table>
                </details>

                <details>
                  <summary>Текст последней версии</summary>
                  <pre className="template-body">{latest?.body ?? ""}</pre>
                </details>
              </article>
            );
          })}

          <details className="template-card">
            <summary>Доступные плейсхолдеры ({placeholders.length})</summary>
            <p>
              Значения подставляются только при создании документа и всегда экранируются.
              Свободный HTML, выражения и SQL не поддерживаются.
            </p>
            <ul className="template-catalog">
              {placeholders.map((item) => (
                <li key={item.token}>
                  <code>{`{{ ${item.token} }}`}</code> — {item.description}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}

      {editor && (
        <TemplateEditor
          editor={editor}
          busy={busy}
          placeholders={placeholders}
          onChange={setEditor}
          onCancel={() => setEditor(null)}
          onSubmit={() => {
            if (editor.mode === "create") {
              void run(
                () =>
                  createDocumentTemplate({
                    kind: editor.kind,
                    scope: editor.scope || null,
                    name: editor.name,
                    title: editor.draft.title,
                    body: editor.draft.body,
                  }),
                "Шаблон создан (черновик)",
              );
              return;
            }
            void run(
              () =>
                addDocumentTemplateVersion(editor.template.id, {
                  title: editor.draft.title,
                  body: editor.draft.body,
                  expected_revision: editor.template.revision,
                }),
              "Создана новая версия (черновик)",
            );
          }}
        />
      )}

      {rename && (
        <form
          className="template-card template-form"
          onSubmit={(event) => {
            event.preventDefault();
            void run(
              () =>
                renameDocumentTemplate(rename.template.id, {
                  name: rename.name,
                  expected_revision: rename.template.revision,
                }),
              "Шаблон переименован",
            );
          }}
        >
          <h2>Переименовать шаблон</h2>
          <p>
            Имя меняется отдельно от версий и не влияет на созданные документы: они
            хранят имя на момент создания.
          </p>
          <Field label="Название" required>
            {(id) => (
              <TextInput
                id={id}
                required
                maxLength={120}
                value={rename.name}
                onChange={(e) => setRename({ ...rename, name: e.target.value })}
              />
            )}
          </Field>
          <div className="template-actions">
            <Button type="submit" variant="primary" disabled={busy}>
              Сохранить имя
            </Button>
            <Button type="button" onClick={() => setRename(null)}>
              Отмена
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}

function VersionRow({
  version,
  canManage,
  busy,
  onActivate,
  onArchive,
}: {
  version: DocumentTemplateVersion;
  canManage: boolean;
  busy: boolean;
  onActivate: () => void;
  onArchive: () => void;
}) {
  return (
    <tr>
      <td>{version.number}</td>
      <td>
        {version.title}
        {version.state === "active" && version.activated_at && (
          <p className="template-meta">
            опубликована: {new Date(version.activated_at).toLocaleDateString("ru")}
          </p>
        )}
      </td>
      <td>
        <Badge tone={STATE_TONE[version.state]}>{STATE_LABELS[version.state]}</Badge>
      </td>
      <td className="template-tokens">
        {version.placeholders.length === 0
          ? "—"
          : version.placeholders.map((token) => `{{${token}}}`).join(", ")}
      </td>
      <td>
        {!canManage && <span className="template-meta">только просмотр</span>}
        {canManage && version.state === "draft" && (
          <Button size="sm" disabled={busy} onClick={onActivate}>
            Опубликовать
          </Button>
        )}
        {canManage && version.state === "active" && (
          <Button size="sm" disabled={busy} onClick={onArchive}>
            В архив
          </Button>
        )}
        {canManage && version.state === "archived" && (
          <span className="template-meta">правка — новая версия</span>
        )}
      </td>
    </tr>
  );
}

function TemplateEditor({
  editor,
  busy,
  placeholders,
  onChange,
  onCancel,
  onSubmit,
}: {
  editor: EditorState;
  busy: boolean;
  placeholders: TemplatePlaceholder[];
  onChange: (next: EditorState) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const insertToken = (token: string) => {
    const value = `{{ ${token} }}`;
    onChange({
      ...editor,
      draft: { ...editor.draft, body: `${editor.draft.body}${value}` },
    });
  };

  return (
    <form
      className="template-card template-form"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <h2>
        {editor.mode === "create" ? "Новый шаблон" : `Новая версия: ${editor.template.name}`}
      </h2>

      {editor.mode === "create" && (
        <>
          <Field label="Тип документа" required>
            {(id) => (
              <SelectInput
                id={id}
                value={editor.kind}
                onChange={(e) => onChange({ ...editor, kind: e.target.value })}
              >
                {KIND_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Название" required>
            {(id) => (
              <TextInput
                id={id}
                required
                maxLength={120}
                value={editor.name}
                onChange={(e) => onChange({ ...editor, name: e.target.value })}
              />
            )}
          </Field>
          <Field label="Этап (область действия)">
            {(id) => (
              <SelectInput
                id={id}
                value={editor.scope}
                onChange={(e) => onChange({ ...editor, scope: e.target.value })}
              >
                <option value="">Все этапы</option>
                {CANDIDATE_STAGE_ORDER.map((stage) => (
                  <option key={stage} value={stage}>
                    {STAGE_LABELS[stage]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
        </>
      )}

      <Field label="Заголовок документа" required>
        {(id) => (
          <TextInput
            id={id}
            required
            maxLength={200}
            value={editor.draft.title}
            onChange={(e) =>
              onChange({ ...editor, draft: { ...editor.draft, title: e.target.value } })
            }
          />
        )}
      </Field>

      <Field
        label="Текст шаблона"
        required
        hint="Разрешены абзацы, список «- » и **полужирный**. Значения плейсхолдеров экранируются."
      >
        {(id) => (
          <textarea
            id={id}
            required
            rows={10}
            maxLength={20000}
            value={editor.draft.body}
            onChange={(e) =>
              onChange({ ...editor, draft: { ...editor.draft, body: e.target.value } })
            }
          />
        )}
      </Field>

      <div className="template-token-picker">
        <span className="template-meta">Вставить плейсхолдер:</span>
        {placeholders.map((item) => (
          <button
            key={item.token}
            type="button"
            title={item.description}
            onClick={() => insertToken(item.token)}
          >
            {`{{ ${item.token} }}`}
          </button>
        ))}
      </div>

      <div className="template-actions">
        <Button type="submit" variant="primary" disabled={busy}>
          Сохранить черновик
        </Button>
        <Button type="button" onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </form>
  );
}
