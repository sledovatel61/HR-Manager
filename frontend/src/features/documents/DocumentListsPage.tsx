/** «Списки документов» (phase 11, admin): immutable versioned lists.
 *
 * A list has a stable id and a chain of versions: at most one draft
 * (editable), at most one published (immutable, what candidates and rules
 * use), any number of archived. Publishing a draft atomically archives the
 * previous published version on the server. Every mutation sends the
 * optimistic row version; a 409 is shown as an explicit conflict with a
 * reload action instead of silently overwriting someone else's change.
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  archiveDocumentListVersion,
  createDocumentList,
  createDocumentListVersion,
  getDocumentList,
  listDocumentLists,
  publishDocumentListVersion,
  updateDocumentList,
  updateDocumentListVersionItems,
} from "../../api";
import { Button, IconButton } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import {
  EmptyState,
  ErrorState,
  PermissionDeniedState,
  SkeletonRows,
  StateView,
} from "../../design-system/components/StateViews";
import { Badge } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import {
  CANDIDATE_STAGE_ORDER,
  STAGE_LABELS,
  type CandidateStage,
  type DocumentListDetail,
  type DocumentListStatus,
  type DocumentListSummary,
  type DocumentListVersion,
} from "../../types";
import { EMPTY_ITEM, MAX_ITEMS, buildItems, type ItemDraft } from "./listHelpers";
import "../notifications/notifications.css";
import "../automation/automation.css";

const STATUS_LABELS: Record<DocumentListStatus, { label: string; tone: "neutral" | "success" | "amber" }> = {
  draft: { label: "Черновик", tone: "amber" },
  published: { label: "Опубликована", tone: "success" },
  archived: { label: "В архиве", tone: "neutral" },
};

interface HeaderForm {
  name: string;
  description: string;
  scope_position: string;
  scope_stage: CandidateStage | "";
}

const EMPTY_HEADER: HeaderForm = { name: "", description: "", scope_position: "", scope_stage: "" };

function formatWhen(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

interface ItemsEditorProps {
  drafts: ItemDraft[];
  onChange: (next: ItemDraft[]) => void;
  disabled?: boolean;
}

function ItemsEditor({ drafts, onChange, disabled }: ItemsEditorProps) {
  const update = (index: number, patch: Partial<ItemDraft>) => {
    onChange(drafts.map((draft, i) => (i === index ? { ...draft, ...patch } : draft)));
  };
  const move = (index: number, delta: number) => {
    const target = index + delta;
    if (target < 0 || target >= drafts.length) return;
    const next = [...drafts];
    [next[index], next[target]] = [next[target], next[index]];
    onChange(next);
  };
  return (
    <div className="doclist-items" role="group" aria-label="Элементы списка">
      {drafts.map((draft, index) => (
        <div key={index} className="doclist-item-editor">
          <Field label={`Документ ${index + 1}`} required>
            {(id) => (
              <TextInput
                id={id}
                value={draft.name}
                maxLength={200}
                disabled={disabled}
                onChange={(event) => update(index, { name: event.target.value })}
                placeholder="Например: паспорт"
              />
            )}
          </Field>
          <Field label="Ключ" hint="Пусто — из названия">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={draft.item_key}
                maxLength={64}
                disabled={disabled}
                onChange={(event) => update(index, { item_key: event.target.value })}
                placeholder="passport"
              />
            )}
          </Field>
          <Field label="Пояснение">
            {(id) => (
              <TextInput
                id={id}
                value={draft.explanation}
                maxLength={500}
                disabled={disabled}
                onChange={(event) => update(index, { explanation: event.target.value })}
                placeholder="Скан всех заполненных страниц"
              />
            )}
          </Field>
          <label className="toggle-chip" style={{ alignSelf: "end" }}>
            <input
              type="checkbox"
              checked={draft.is_required}
              disabled={disabled}
              onChange={(event) => update(index, { is_required: event.target.checked })}
            />
            обязательный
          </label>
          <div style={{ display: "flex", gap: 4, alignSelf: "end" }}>
            <IconButton
              icon="chevron-up"
              label={`Поднять документ ${index + 1}`}
              disabled={disabled || index === 0}
              onClick={() => move(index, -1)}
            />
            <IconButton
              icon="chevron-down"
              label={`Опустить документ ${index + 1}`}
              disabled={disabled || index === drafts.length - 1}
              onClick={() => move(index, 1)}
            />
            <IconButton
              icon="trash"
              label={`Убрать документ ${index + 1}`}
              disabled={disabled}
              onClick={() => onChange(drafts.filter((_, i) => i !== index))}
            />
          </div>
        </div>
      ))}
      <div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          icon="plus"
          disabled={disabled || drafts.length >= MAX_ITEMS}
          onClick={() => onChange([...drafts, { ...EMPTY_ITEM }])}
        >
          Добавить документ
        </Button>
      </div>
    </div>
  );
}

interface VersionCardProps {
  listId: string;
  version: DocumentListVersion;
  onChanged: (detail?: DocumentListDetail) => Promise<void>;
  onConflict: (message: string) => void;
}

function VersionCard({ listId, version, onChanged, onConflict }: VersionCardProps) {
  const { pushToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<ItemDraft[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<"publish" | "archive" | null>(null);
  const status = STATUS_LABELS[version.status];

  const handle = (caught: unknown, fallback: string) => {
    if (caught instanceof ApiError && caught.status === 409) {
      onConflict(caught.message);
      return;
    }
    pushToast("danger", caught instanceof ApiError ? caught.message : fallback);
  };

  const startEdit = () => {
    setDrafts(
      version.items.map((item) => ({
        item_key: item.item_key,
        name: item.name,
        explanation: item.explanation,
        is_required: item.is_required,
      })),
    );
    setFormError(null);
    setEditing(true);
  };

  const saveItems = async () => {
    const built = buildItems(drafts);
    if ("error" in built) {
      setFormError(built.error);
      return;
    }
    setBusy(true);
    try {
      await updateDocumentListVersionItems(listId, version.id, {
        expected_row_version: version.row_version,
        items: built.items,
      });
      pushToast("success", "Черновик сохранён.");
      setEditing(false);
      await onChanged();
    } catch (caught) {
      handle(caught, "Не удалось сохранить черновик.");
    } finally {
      setBusy(false);
    }
  };

  const act = async (kind: "publish" | "archive") => {
    setConfirm(null);
    setBusy(true);
    try {
      if (kind === "publish") {
        await publishDocumentListVersion(listId, version.id, version.row_version);
        pushToast("success", `Версия ${version.version_number} опубликована.`);
      } else {
        await archiveDocumentListVersion(listId, version.id, version.row_version);
        pushToast("success", `Версия ${version.version_number} отправлена в архив.`);
      }
      await onChanged();
    } catch (caught) {
      handle(caught, "Не удалось изменить статус версии.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="doclist-version">
      <div className="doclist-version-head">
        <div>
          <strong>Версия {version.version_number}</strong>{" "}
          <Badge tone={status.tone}>{status.label}</Badge>
          <div className="cand-docs-meta">
            {version.status === "published" && `опубликована ${formatWhen(version.published_at)}`}
            {version.status === "archived" && `в архиве с ${formatWhen(version.archived_at)}`}
            {version.status === "draft" && `изменена ${formatWhen(version.updated_at)}`}
            {" · "}
            элементов: {version.items.length}
          </div>
        </div>
        <div className="doclist-version-actions">
          {version.status === "draft" && !editing && (
            <>
              <Button variant="secondary" size="sm" disabled={busy} onClick={startEdit}>
                Редактировать
              </Button>
              <Button
                variant="primary"
                size="sm"
                disabled={busy || version.items.length === 0}
                onClick={() => setConfirm("publish")}
              >
                Опубликовать
              </Button>
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm("archive")}>
                В архив
              </Button>
            </>
          )}
          {version.status === "published" && (
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm("archive")}>
              Снять с публикации
            </Button>
          )}
        </div>
      </div>

      {editing ? (
        <form
          aria-label={`Черновик версии ${version.version_number}`}
          onSubmit={(event) => {
            event.preventDefault();
            void saveItems();
          }}
        >
          <ItemsEditor drafts={drafts} onChange={setDrafts} disabled={busy} />
          {formError && (
            <p className="field-error" role="alert">
              {formError}
            </p>
          )}
          <div className="rule-form-actions" style={{ marginTop: 10 }}>
            <Button type="submit" variant="primary" size="sm" loading={busy}>
              Сохранить черновик
            </Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setEditing(false)}>
              Отмена
            </Button>
          </div>
        </form>
      ) : version.items.length === 0 ? (
        <p className="cand-docs-meta">Элементов пока нет.</p>
      ) : (
        <ol className="doclist-items">
          {version.items.map((item) => (
            <li key={item.id} className="doclist-item">
              <span>
                {item.name}
                {!item.is_required && <span className="muted-text"> (необязательный)</span>}
              </span>
              <span className="doclist-item-key">{item.item_key}</span>
              {item.explanation && (
                <span className="doclist-item-explanation">{item.explanation}</span>
              )}
            </li>
          ))}
        </ol>
      )}

      <ConfirmDialog
        open={confirm === "publish"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void act("publish")}
        title={`Опубликовать версию ${version.version_number}?`}
        description="Опубликованная версия становится неизменяемой; предыдущая опубликованная версия уйдёт в архив. Уже применённые кандидатам снимки не изменятся."
        confirmLabel="Опубликовать"
      />
      <ConfirmDialog
        open={confirm === "archive"}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void act("archive")}
        title={`Отправить версию ${version.version_number} в архив?`}
        description={
          version.status === "published"
            ? "Список перестанет быть доступным для применения и правил, пока не будет опубликована новая версия. Снимки у кандидатов сохранятся."
            : "Черновик будет закрыт без публикации. Историю версий это не изменит."
        }
        confirmLabel="В архив"
        danger={version.status === "published"}
      />
    </li>
  );
}

export function DocumentListsPage() {
  const { pushToast } = useToast();
  const [lists, setLists] = useState<DocumentListSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DocumentListDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [header, setHeader] = useState<HeaderForm>(EMPTY_HEADER);
  const [createItems, setCreateItems] = useState<ItemDraft[]>([{ ...EMPTY_ITEM }]);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [editingHeader, setEditingHeader] = useState(false);

  const loadLists = useCallback(async () => {
    setLoading(true);
    setError(false);
    setForbidden(false);
    try {
      const payload = await listDocumentLists();
      setLists(payload.items);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 403) {
        setForbidden(true);
      } else {
        setError(true);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (listId: string) => {
    setDetailLoading(true);
    setDetailError(false);
    try {
      const payload = await getDocumentList(listId);
      setDetail(payload);
      setConflict(null);
    } catch {
      setDetailError(true);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadLists();
  }, [loadLists]);

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId);
    } else {
      setDetail(null);
    }
  }, [selectedId, loadDetail]);

  const refreshAll = useCallback(async () => {
    await loadLists();
    if (selectedId) {
      await loadDetail(selectedId);
    }
  }, [loadLists, loadDetail, selectedId]);

  const handleError = (caught: unknown, fallback: string) => {
    if (caught instanceof ApiError) {
      if (caught.status === 409) {
        setConflict(caught.message);
        return;
      }
      pushToast("danger", caught.message || fallback);
      return;
    }
    pushToast("danger", fallback);
  };

  const submitCreate = async () => {
    const name = header.name.trim();
    if (!name) {
      setFormError("Укажите название списка.");
      return;
    }
    const built = buildItems(createItems.filter((item) => item.name.trim() || item.item_key.trim()));
    if ("error" in built) {
      setFormError(built.error);
      return;
    }
    setFormError(null);
    setSaving(true);
    try {
      const created = await createDocumentList({
        name,
        description: header.description.trim(),
        scope_position: header.scope_position.trim() || null,
        scope_stage: header.scope_stage || null,
        items: built.items,
      });
      pushToast("success", "Список создан как черновик. Опубликуйте его, когда будет готов.");
      setCreating(false);
      setHeader(EMPTY_HEADER);
      setCreateItems([{ ...EMPTY_ITEM }]);
      await loadLists();
      setSelectedId(created.id);
    } catch (caught) {
      handleError(caught, "Не удалось создать список.");
    } finally {
      setSaving(false);
    }
  };

  const submitHeader = async () => {
    if (!detail) return;
    const name = header.name.trim();
    if (!name) {
      setFormError("Укажите название списка.");
      return;
    }
    setFormError(null);
    setSaving(true);
    try {
      await updateDocumentList(detail.id, {
        expected_version: detail.version,
        name,
        description: header.description.trim(),
        scope_position: header.scope_position.trim() || null,
        clear_scope_position: !header.scope_position.trim(),
        scope_stage: header.scope_stage || null,
        clear_scope_stage: !header.scope_stage,
      });
      pushToast("success", "Описание списка обновлено.");
      setEditingHeader(false);
      await refreshAll();
    } catch (caught) {
      handleError(caught, "Не удалось сохранить описание списка.");
    } finally {
      setSaving(false);
    }
  };

  const newDraft = async () => {
    if (!detail) return;
    setSaving(true);
    try {
      await createDocumentListVersion(detail.id);
      pushToast("success", "Создан новый черновик (копия опубликованной версии).");
      await refreshAll();
    } catch (caught) {
      handleError(caught, "Не удалось создать черновик.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="notif-page" aria-busy="true">
        <SkeletonRows rows={4} columns={3} />
      </div>
    );
  }
  if (forbidden) {
    return <PermissionDeniedState />;
  }
  if (error) {
    return <ErrorState onRetry={() => void loadLists()} />;
  }

  const hasDraft = detail?.versions.some((version) => version.status === "draft") ?? false;

  const headerForm = (onSubmit: () => void, submitLabel: string, onCancel: () => void, withItems: boolean) => (
    <form
      className="reminder-form"
      aria-label={withItems ? "Новый список документов" : "Описание списка"}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <Field label="Название" required>
        {(id) => (
          <TextInput
            id={id}
            value={header.name}
            maxLength={200}
            onChange={(event) => setHeader({ ...header, name: event.target.value })}
            placeholder="Например: документы для оформления"
          />
        )}
      </Field>
      <Field label="Должность (область применения)" hint="Пусто — для любой должности">
        {(id, describedBy) => (
          <TextInput
            id={id}
            aria-describedby={describedBy}
            value={header.scope_position}
            maxLength={200}
            onChange={(event) => setHeader({ ...header, scope_position: event.target.value })}
          />
        )}
      </Field>
      <Field label="Этап (область применения)" hint="Пусто — для любого этапа">
        {(id, describedBy) => (
          <SelectInput
            id={id}
            aria-describedby={describedBy}
            value={header.scope_stage}
            onChange={(event) =>
              setHeader({ ...header, scope_stage: event.target.value as CandidateStage | "" })
            }
          >
            <option value="">— любой —</option>
            {CANDIDATE_STAGE_ORDER.map((stage) => (
              <option key={stage} value={stage}>
                {STAGE_LABELS[stage]}
              </option>
            ))}
          </SelectInput>
        )}
      </Field>
      <Field label="Описание">
        {(id) => (
          <TextInput
            id={id}
            value={header.description}
            maxLength={2000}
            onChange={(event) => setHeader({ ...header, description: event.target.value })}
          />
        )}
      </Field>
      {withItems && (
        <div className="field-span-2">
          <ItemsEditor drafts={createItems} onChange={setCreateItems} disabled={saving} />
        </div>
      )}
      {formError && (
        <p className="field-error field-span-2" role="alert">
          {formError}
        </p>
      )}
      <div className="field-span-2 rule-form-actions">
        <Button type="submit" variant="primary" loading={saving}>
          {submitLabel}
        </Button>
        <Button type="button" variant="secondary" onClick={onCancel}>
          Отмена
        </Button>
      </div>
    </form>
  );

  return (
    <div className="notif-page">
      <div className="rules-header">
        <p className="rules-intro">
          Кандидаты и правила используют только опубликованную версию. Публикация делает версию
          неизменяемой; для правок создайте новый черновик — уже применённые снимки не меняются.
        </p>
        <Button
          variant="primary"
          icon="plus"
          onClick={() => {
            setCreating(true);
            setEditingHeader(false);
            setHeader(EMPTY_HEADER);
            setFormError(null);
          }}
        >
          Новый список
        </Button>
      </div>

      {conflict && (
        <StateView
          icon="alert-triangle"
          tone="warning"
          title="Данные устарели"
          description={conflict}
          action={
            <Button variant="secondary" onClick={() => void refreshAll()}>
              Обновить
            </Button>
          }
        />
      )}

      {creating && headerForm(() => void submitCreate(), "Создать черновик", () => setCreating(false), true)}

      {lists.length === 0 && !creating ? (
        <EmptyState
          icon="file-text"
          title="Списков документов пока нет"
          description="Создайте первый список: он появится как черновик, который можно опубликовать."
        />
      ) : (
        <div className="doclist-layout">
          <nav aria-label="Списки документов">
            <ul className="doclist-nav">
              {lists.map((list) => (
                <li key={list.id}>
                  <button
                    type="button"
                    className={`doclist-nav-item ${selectedId === list.id ? "is-active" : ""}`}
                    aria-current={selectedId === list.id ? "true" : undefined}
                    onClick={() => {
                      setSelectedId(list.id);
                      setCreating(false);
                      setEditingHeader(false);
                    }}
                  >
                    <strong>{list.name}</strong>
                    <small>
                      {list.published_version_number
                        ? `опубликована версия ${list.published_version_number}`
                        : "нет опубликованной версии"}
                      {list.draft_version_id ? " · есть черновик" : ""}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <section className="doclist-detail" aria-live="polite">
            {!selectedId && <p className="cand-docs-meta">Выберите список слева.</p>}
            {selectedId && detailLoading && <SkeletonRows rows={3} columns={2} />}
            {selectedId && !detailLoading && detailError && (
              <ErrorState onRetry={() => void loadDetail(selectedId)} />
            )}
            {selectedId && !detailLoading && !detailError && detail && (
              <>
                {editingHeader ? (
                  headerForm(() => void submitHeader(), "Сохранить", () => setEditingHeader(false), false)
                ) : (
                  <div className="cand-docs-head">
                    <div>
                      <h3 className="messages-section-title">{detail.name}</h3>
                      <p className="cand-docs-meta">
                        {detail.description || "без описания"}
                        {detail.scope_position && ` · должность: ${detail.scope_position}`}
                        {detail.scope_stage && ` · этап: ${STAGE_LABELS[detail.scope_stage]}`}
                      </p>
                    </div>
                    <div className="doclist-version-actions">
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          setHeader({
                            name: detail.name,
                            description: detail.description,
                            scope_position: detail.scope_position ?? "",
                            scope_stage: detail.scope_stage ?? "",
                          });
                          setFormError(null);
                          setEditingHeader(true);
                        }}
                      >
                        Изменить описание
                      </Button>
                      <Button
                        variant="primary"
                        size="sm"
                        icon="plus"
                        disabled={hasDraft || saving}
                        onClick={() => void newDraft()}
                      >
                        Новый черновик
                      </Button>
                    </div>
                  </div>
                )}
                <ul className="doclist-versions">
                  {[...detail.versions]
                    .sort((a, b) => b.version_number - a.version_number)
                    .map((version) => (
                      <VersionCard
                        key={`${version.id}:${version.row_version}`}
                        listId={detail.id}
                        version={version}
                        onChanged={refreshAll}
                        onConflict={setConflict}
                      />
                    ))}
                </ul>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
