import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  activateDocumentTemplateVersion,
  addDocumentTemplateVersion,
  archiveDocumentTemplateVersion,
  createDocumentTemplate,
  importDocumentTemplate,
  listDocumentTemplates,
  listTemplatePlaceholders,
  renameDocumentTemplate,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import {
  EmptyState,
  ErrorState,
  PermissionDeniedState,
  SkeletonRows,
} from "../../design-system/components/StateViews";
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
import {
  KIND_OPTIONS,
  REVIEW_KINDS,
  TEMPLATE_IMPORT_EXTENSIONS,
  TEMPLATE_IMPORT_MAX_BYTES,
  describeImportFile,
  friendlyPlaceholderName,
  kindLabel as kindLabelOf,
} from "./vocabulary";
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

interface ImportState {
  kind: string;
  scope: string;
  name: string;
  file: File | null;
}

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "Все шаблоны" },
  { value: "published", label: "С опубликованной версией" },
  { value: "unpublished", label: "Без опубликованной версии" },
] as const;

type StatusFilter = (typeof STATUS_FILTER_OPTIONS)[number]["value"];

interface VersionDraft {
  title: string;
  body: string;
}

type EditorState =
  | { mode: "create"; kind: string; name: string; scope: string; draft: VersionDraft }
  | { mode: "version"; template: DocumentTemplate; draft: VersionDraft };

/** Опасные действия над версией — проводятся только после подтверждения. */
type PendingVersionAction = {
  operation: "activate" | "archive";
  template: DocumentTemplate;
  version: DocumentTemplateVersion;
};

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("ru-RU");
}

function hasActiveVersion(template: DocumentTemplate): boolean {
  return template.versions.some((version) => version.state === "active");
}

/**
 * Сообщение об ошибке на русском. Backend отдаёт готовые русские detail,
 * pydantic-валидация приходит массивом — извлекаем текст первого нарушения
 * («Value error, …» — префикс pydantic). Остальное — как в documents/hooks.
 */
function templateError(error: unknown): string {
  if (
    error instanceof ApiError &&
    error.status === 422 &&
    Array.isArray(error.rawDetail) &&
    error.rawDetail.length > 0 &&
    typeof error.rawDetail[0] === "object" &&
    error.rawDetail[0] !== null &&
    "msg" in error.rawDetail[0]
  ) {
    const message = String((error.rawDetail[0] as { msg: unknown }).msg)
      .replace(/^Value error,\s*/, "")
      .trim();
    if (message) return message;
  }
  return errorText(error);
}

export function TemplatesPage() {
  const { pushToast } = useToast();
  const [templates, setTemplates] = useState<DocumentTemplate[] | null>(null);
  const [canManage, setCanManage] = useState(false);
  const [placeholders, setPlaceholders] = useState<TemplatePlaceholder[]>([]);
  const [loadError, setLoadError] = useState("");
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editor, setEditor] = useState<EditorState | null>(null);
  const [rename, setRename] = useState<{ template: DocumentTemplate; name: string } | null>(
    null,
  );
  const [pendingAction, setPendingAction] = useState<PendingVersionAction | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [importer, setImporter] = useState<ImportState | null>(null);

  const load = useCallback(async () => {
    setLoadError("");
    setForbidden(false);
    try {
      const [list, catalog] = await Promise.all([
        listDocumentTemplates(),
        listTemplatePlaceholders(),
      ]);
      setTemplates(list.items);
      setCanManage(list.can_manage);
      setPlaceholders(catalog.items);
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setForbidden(true);
      } else {
        setLoadError(errorText(error));
      }
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
      pushToast("danger", templateError(error));
    } finally {
      setBusy(false);
    }
  };

  const visible = useMemo(() => {
    if (!templates) return [];
    const query = search.trim().toLowerCase();
    return templates.filter((template) => {
      if (statusFilter === "published" && !hasActiveVersion(template)) return false;
      if (statusFilter === "unpublished" && hasActiveVersion(template)) return false;
      if (!query) return true;
      const scopeLabel = template.scope
        ? (STAGE_LABELS[template.scope as CandidateStage] ?? template.scope)
        : "все этапы";
      const haystack = [
        template.name,
        template.kind,
        kindLabelOf(template.kind),
        scopeLabel,
        ...template.versions.map((version) => version.title),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [search, statusFilter, templates]);

  const filtersActive = search.trim() !== "" || statusFilter !== "all";

  if (forbidden) {
    return (
      <PermissionDeniedState />
    );
  }
  if (!templates && loadError) {
    return <ErrorState onRetry={() => void load()} />;
  }
  if (!templates) {
    return <SkeletonRows rows={4} columns={3} />;
  }

  return (
    <section className="templates-page">
      <p className="templates-intro">
        Методическая база: чек-листы, вопросники, памятки и скрипты. Опубликованная
        версия неизменяема — правка это новая версия. Материал можно загрузить из
        текстового файла: на сервер уйдёт только проверенный текст черновика.
        Документы кандидату не отправляются.
      </p>

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
        <Button
          variant="secondary"
          icon="file-text"
          disabled={!canManage || busy}
          onClick={() =>
            setImporter({ kind: "checklist", scope: "", name: "", file: null })
          }
        >
          Загрузить из файла
        </Button>
        <Button
          variant="ghost"
          icon="table"
          onClick={() => {
            window.location.hash = "#/documents";
          }}
        >
          Списки документов
        </Button>
        <span className="template-meta">
          {canManage
            ? "Управление доступно: администратор или право document_lists_manage."
            : "Только просмотр опубликованных версий: для правок нужно право document_lists_manage."}
        </span>
      </div>

      {templates.length > 0 && (
        <div className="templates-filters">
          <Field label="Поиск шаблона">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="search"
                placeholder="Название, тип или заголовок версии"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            )}
          </Field>
          <Field label="Статус">
            {(id, describedBy) => (
              <SelectInput
                id={id}
                aria-describedby={describedBy}
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}
              >
                {STATUS_FILTER_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          {filtersActive && (
            <span className="template-meta templates-count" role="status">
              Показано {visible.length} из {templates.length}
            </span>
          )}
        </div>
      )}

      {templates.length === 0 && (
        <EmptyState
          icon="file-text"
          title="Шаблонов пока нет"
          description="Создайте шаблон вручную или загрузите готовый материал из файла — после публикации версии шаблон станет доступен в карточках кандидатов."
          action={
            canManage && (
              <Button
                variant="primary"
                icon="plus"
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
                Создать первый шаблон
              </Button>
            )
          }
        />
      )}

      {templates.length > 0 && visible.length === 0 && (
        <EmptyState
          icon="search"
          title="Ничего не найдено"
          description="По запросу нет совпадений. Измените текст поиска или фильтр статуса."
        />
      )}

      {visible.map((template) => {
        const active = template.versions.find((v) => v.state === "active");
        const latest = template.versions[template.versions.length - 1];
        return (
          <article key={template.id} className="template-card">
            <header className="template-card-head">
              <div>
                <h2>{template.name}</h2>
                <p className="template-meta">
                  {kindLabelOf(template.kind)} ·{" "}
                  {template.scope
                    ? (STAGE_LABELS[template.scope as CandidateStage] ?? template.scope)
                    : "Все этапы"}{" "}
                  · ревизия {template.revision} · версий {template.versions.length}
                </p>
                {REVIEW_KINDS.has(template.kind) && (
                  <p className="template-review-note" role="note">
                    Правовая форма: перед использованием проверьте, что текст
                    актуален. Ответственность за содержание несёт HR — программа
                    не проверяет юридическую свежесть документа.
                  </p>
                )}
                <p className="template-meta template-dates">
                  Создан: {formatDateTime(template.created_at)} · Изменён:{" "}
                  {formatDateTime(template.updated_at)}
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
                        setPendingAction({
                          operation: "activate",
                          template,
                          version,
                        })
                      }
                      onArchive={() =>
                        setPendingAction({ operation: "archive", template, version })
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

      {templates.length > 0 && (
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
      )}

      {importer && (
        <TemplateImporter
          state={importer}
          busy={busy}
          onChange={setImporter}
          onCancel={() => setImporter(null)}
          onSubmit={() => {
            const file = importer.file;
            if (!file) return;
            void run(
              () =>
                importDocumentTemplate({
                  kind: importer.kind,
                  scope: importer.scope,
                  name: importer.name,
                  file,
                }),
              "Шаблон загружен из файла (черновик) — проверьте текст и опубликуйте",
            );
            setImporter(null);
          }}
        />
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
        <Modal
          open
          onClose={() => setRename(null)}
          title="Переименовать шаблон"
          description="Имя меняется отдельно от версий и не влияет на созданные документы: они хранят имя на момент создания."
        >
          <form
            className="template-form"
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
        </Modal>
      )}

      {pendingAction && (
        <ConfirmDialog
          open
          danger={pendingAction.operation === "archive"}
          onCancel={() => setPendingAction(null)}
          onConfirm={() => {
            const { operation, template, version } = pendingAction;
            setPendingAction(null);
            if (operation === "activate") {
              void run(
                () =>
                  activateDocumentTemplateVersion(
                    template.id,
                    version.id,
                    template.revision,
                  ),
                `Версия ${version.number} опубликована`,
              );
              return;
            }
            void run(
              () =>
                archiveDocumentTemplateVersion(template.id, version.id, template.revision),
              `Версия ${version.number} в архиве`,
            );
          }}
          title={
            pendingAction.operation === "activate"
              ? `Опубликовать версию ${pendingAction.version.number}?`
              : `Архивировать версию ${pendingAction.version.number}?`
          }
          description={
            pendingAction.operation === "activate"
              ? `Сотрудники будут видеть текст версии ${pendingAction.version.number}. Текущая опубликованная версия этого шаблона, если она есть, автоматически уйдёт в архив.`
              : `Версия ${pendingAction.version.number} перестанет публиковаться для новых документов. Если она опубликована, шаблон останется без опубликованной версии, пока вы не опубликуете другую.`
          }
          confirmLabel={pendingAction.operation === "activate" ? "Опубликовать" : "В архив"}
        />
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

function TemplateImporter({
  state,
  busy,
  onChange,
  onCancel,
  onSubmit,
}: {
  state: ImportState;
  busy: boolean;
  onChange: (next: ImportState) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const problem = describeImportFile(state.file);
  const canSubmit = state.file !== null && problem === null && !busy;

  return (
    <Modal
      open
      onClose={onCancel}
      title="Загрузить шаблон из файла"
      description="Из файла создаётся черновик: сначала проверьте текст, потом опубликуйте версию."
    >
      <form
        className="template-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <Field label="Файл" required hint={`${TEMPLATE_IMPORT_EXTENSIONS.join(", ")}, до ${TEMPLATE_IMPORT_MAX_BYTES / 1024} КБ, UTF-8`}>
          {(id, describedBy) => (
            <input
              id={id}
              aria-describedby={describedBy}
              className="template-file-input"
              type="file"
              accept={TEMPLATE_IMPORT_EXTENSIONS.join(",")}
              onChange={(event) =>
                onChange({
                  ...state,
                  file: event.target.files?.[0] ?? null,
                })
              }
            />
          )}
        </Field>
        {state.file && problem && (
          <p className="template-form-error" role="alert">
            {problem}
          </p>
        )}
        <Field label="Тип документа" required>
          {(id) => (
            <SelectInput
              id={id}
              value={state.kind}
              onChange={(event) => onChange({ ...state, kind: event.target.value })}
            >
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
        <Field label="Название" hint="Если оставить пустым, возьмём первую строку файла.">
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              maxLength={120}
              value={state.name}
              onChange={(event) => onChange({ ...state, name: event.target.value })}
            />
          )}
        </Field>
        <Field label="Этап (область действия)">
          {(id) => (
            <SelectInput
              id={id}
              value={state.scope}
              onChange={(event) => onChange({ ...state, scope: event.target.value })}
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
        <p className="template-meta">
          Файл остаётся на вашем компьютере: на сервер уходит только текст, проверенный
          на размер, кодировку и безопасность. Персональные данные кандидатов из
          методички в шаблон не попадут — вставляйте их через поля «ФИО кандидата»
          и другие подстановки.
        </p>
        <div className="template-actions">
          <Button type="submit" variant="primary" disabled={!canSubmit}>
            Загрузить как черновик
          </Button>
          <Button type="button" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
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
    <Modal
      open
      onClose={onCancel}
      size="lg"
      title={
        editor.mode === "create" ? "Новый шаблон" : `Новая версия: ${editor.template.name}`
      }
      description="Черновик не виден сотрудникам, пока вы не опубликуете версию."
    >
      <form
        className="template-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
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
          <span className="template-meta">Нажмите, чтобы вставить значение поля:</span>
          {placeholders.map((item) => (
            <button
              key={item.token}
              type="button"
              title={`${item.description} — вставляется как {{ ${item.token} }}`}
              onClick={() => insertToken(item.token)}
            >
              {friendlyPlaceholderName(item)}
            </button>
          ))}
        </div>
        <details className="template-token-syntax">
          <summary>Как это выглядит в тексте шаблона</summary>
          <p className="template-meta">
            Подстановки в тексте записываются в двойных фигурных скобках:{" "}
            <code>{"{{ candidate.full_name }}"}</code>, <code>{"{{ system.date }}"}</code>.
            Обычный текст, списки через «- » и выделение через **жирный** тоже
            поддерживаются. HTML и произвольные выражения — нет: это защищает
            персональные данные.
          </p>
        </details>

        <div className="template-actions">
          <Button type="submit" variant="primary" disabled={busy}>
            Сохранить черновик
          </Button>
          <Button type="button" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  );
}
