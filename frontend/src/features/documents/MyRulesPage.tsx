/**
 * «Мои правила» — личные правила автоматизации документов (phase 11).
 *
 * Backend-контракты (не менялись): GET/POST /document-rules,
 * PUT /document-rules/{id} (expected_version, оптимистическая блокировка),
 * GET /document-rules/{id}/history. Отдельного endpoint включения/выключения
 * нет — переключение выполняется тем же PUT; удаления правил не существует,
 * поэтому кнопки удаления в UI нет. Правила всегда личные (owner_id); статус —
 * только enabled/disabled плюс монотонный version (черновиков/архива у правил,
 * в отличие от шаблонов, нет). Редактирование отменяет ожидающие задания
 * предыдущей версии — выключение/правка требуют подтверждения.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, documentRequest as request } from "../../api";
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
  type NotificationPreferences,
} from "../../types";
import type {
  DocumentLists,
  DocumentRule,
  RuleExecution,
  RuleInput,
  RuleParams,
} from "./types";
import { errorText, useResource } from "./hooks";
import { buildRulePreview } from "./rulePreview";
import "./documents.css";

const actions: Record<RuleParams["action"], string> = {
  apply_list: "Применить актуальный список, если списка ещё нет",
  document_request: "Поставить запрос документов",
  document_reminder: "Поставить напоминание о документах",
};

/** Короткие названия действий для фильтра и сводки карточки. */
const actionShort: Record<RuleParams["action"], string> = {
  apply_list: "Применение списка",
  document_request: "Запрос документов",
  document_reminder: "Напоминание о документах",
};

const outcomes: Record<string, string> = {
  applied: "Список применён",
  queued: "Сообщение в очереди",
  skipped: "Условия не выполнены",
  cancelled: "Отменено",
  failed: "Ошибка исполнения",
};

const triggerLabels: Record<RuleParams["trigger"], string> = {
  stage_transition: "При переходе этапа",
  scheduled_reminder: "По сроку",
};

const STATUS_FILTER_OPTIONS = [
  { value: "all", label: "Все статусы" },
  { value: "enabled", label: "Включённые" },
  { value: "disabled", label: "Выключенные" },
] as const;

type StatusFilter = (typeof STATUS_FILTER_OPTIONS)[number]["value"];

const ACTION_FILTER_OPTIONS: { value: RuleParams["action"] | "all"; label: string }[] = [
  { value: "all", label: "Все типы" },
  { value: "apply_list", label: actionShort.apply_list },
  { value: "document_request", label: actionShort.document_request },
  { value: "document_reminder", label: actionShort.document_reminder },
];

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

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("ru-RU");
}

/**
 * Человекочитаемое сообщение об ошибке на русском. 422 от pydantic приходит
 * массивом нарушений — извлекаем текст первого («Value error, …» — префикс
 * pydantic); остальное — общий documents-паттерн (errorText).
 */
function ruleError(error: unknown): string {
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

interface EditorState {
  id?: string;
  version?: number;
  input: RuleInput;
}

/** Выключение отменяет незавершённые задания версии — опасное действие. */
interface PendingToggle {
  rule: DocumentRule;
}

interface HistoryState {
  name: string;
  rows: RuleExecution[] | null;
  error: string;
}

function ruleMatchesFilters(
  rule: DocumentRule,
  search: string,
  status: StatusFilter,
  action: RuleParams["action"] | "all",
  stage: CandidateStage | "all",
): boolean {
  if (status === "enabled" && !rule.enabled) return false;
  if (status === "disabled" && rule.enabled) return false;
  if (action !== "all" && rule.params.action !== action) return false;
  if (stage !== "all" && rule.params.stage !== stage) return false;
  const query = search.trim().toLowerCase();
  if (!query) return true;
  return [
    rule.name,
    actionShort[rule.params.action],
    actions[rule.params.action],
    triggerLabels[rule.params.trigger],
    STAGE_LABELS[rule.params.stage],
  ]
    .join(" ")
    .toLowerCase()
    .includes(query);
}

export function MyRulesPage() {
  const { pushToast } = useToast();
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [rules, lists] = await Promise.all([
      request<DocumentRule[]>("/document-rules"),
      request<DocumentLists>("/document-lists"),
    ]);
    return { rules, lists };
  }, []);
  const resource = useResource(load);

  // Настройки уведомлений автора — только для живого блока «Что произойдёт»
  // (тихие часы, рабочие дни, часовой пояс). Если загрузить не удалось,
  // блок честно пишет обобщённо, без конкретных часов.
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  useEffect(() => {
    let cancelled = false;
    request<NotificationPreferences>("/notification-preferences")
      .then((loaded) => {
        if (!cancelled) setPrefs(loaded);
      })
      .catch(() => {
        /* preview останется с обобщённой фразой про настройки */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // 403 при загрузке (errorText-сообщение) — штатное «Недостаточно прав»
  // вместо общей ошибки; остальное — error state с retry.
  const accessDenied = resource.error.startsWith("Недостаточно прав");

  const [editor, setEditor] = useState<EditorState | null>(null);
  const [pendingToggle, setPendingToggle] = useState<PendingToggle | null>(null);
  const [history, setHistory] = useState<HistoryState | null>(null);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [actionFilter, setActionFilter] = useState<RuleParams["action"] | "all">("all");
  const [stageFilter, setStageFilter] = useState<CandidateStage | "all">("all");

  const rules = useMemo(() => resource.data?.rules ?? [], [resource.data]);
  const visible = useMemo(
    () =>
      rules.filter((rule) =>
        ruleMatchesFilters(rule, search, statusFilter, actionFilter, stageFilter),
      ),
    [actionFilter, rules, search, stageFilter, statusFilter],
  );
  const filtersActive =
    search.trim() !== "" ||
    statusFilter !== "all" ||
    actionFilter !== "all" ||
    stageFilter !== "all";

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

  const mutate = async (action: () => Promise<unknown>, successNotice: string) => {
    setBusy(true);
    try {
      await action();
      pushToast("success", successNotice);
      setEditor(null);
      await resource.reload();
    } catch (e) {
      // Ошибка видна рядом с формой/страницей через Toast-паттерн; форма и
      // список не закрываются — данные не теряются молча.
      pushToast("danger", ruleError(e));
    } finally {
      setBusy(false);
    }
  };

  const confirmToggle = (rule: DocumentRule) => {
    setPendingToggle(null);
    void mutate(
      () =>
        request(`/document-rules/${rule.id}`, {
          method: "PUT",
          body: {
            name: rule.name,
            enabled: !rule.enabled,
            params: rule.params,
            expected_version: rule.version,
          },
        }),
      rule.enabled
        ? `Правило «${rule.name}» выключено.`
        : `Правило «${rule.name}» включено.`,
    );
  };

  const openHistory = async (rule: DocumentRule) => {
    setHistory({ name: rule.name, rows: null, error: "" });
    try {
      const rows = await request<RuleExecution[]>(`/document-rules/${rule.id}/history`);
      setHistory({ name: rule.name, rows, error: "" });
    } catch (e) {
      setHistory({ name: rule.name, rows: null, error: ruleError(e) });
    }
  };

  const published = useMemo(
    () =>
      resource.data?.lists.items.flatMap((l) =>
        l.versions.filter((v) => v.state === "published"),
      ) ?? [],
    [resource.data],
  );

  /** Имя списка для карточки: по опубликованной версии из /document-lists. */
  const listNameOf = (rule: DocumentRule): string => {
    const match =
      published.find((v) => v.id === rule.params.list_version_id) ??
      published.find((v) => v.list_id === rule.params.list_id);
    return match?.name ?? "";
  };

  /** Сводка карточки теми же человеческими словами, что поля формы:
   * «Когда это происходит» / «Что сделать» / «Куда отправить». */
  const ruleSummary = (rule: DocumentRule): string => {
    const stage = STAGE_LABELS[rule.params.stage];
    const when =
      rule.params.trigger === "stage_transition"
        ? `кандидат перешёл на этап «${stage}»`
        : `прошло ${rule.params.days ?? 1} дн. после применения списка, а документы не получены (этап «${stage}»)`;
    const listName = listNameOf(rule);
    const what = listName
      ? `${actionShort[rule.params.action]} «${listName}»`
      : actionShort[rule.params.action];
    const where =
      rule.params.channel === "email"
        ? "по почте"
        : rule.params.channel === "telegram"
          ? "в Telegram"
          : "без отправки кандидату";
    return `Когда это происходит: ${when}. Что сделать: ${what}. Куда отправить: ${where}.`;
  };

  /** Живой блок «Что произойдёт»: чистый клиентский расчёт по данным формы
   * и настройкам профиля — без сервера, ничего не создаёт и не отправляет.
   * Пересчитывается на каждое изменение полей (зависимость от `editor`). */
  const editorPreview = useMemo(() => {
    if (!editor) return null;
    const selectedVersion = published.find(
      (v) =>
        v.list_id === editor.input.params.list_id &&
        (!v.stage || v.stage === editor.input.params.stage),
    );
    const requiredItems =
      selectedVersion?.items.filter((item) => item.required).map((item) => item.name) ?? [];
    const items =
      requiredItems.length > 0
        ? requiredItems
        : (selectedVersion?.items.map((item) => item.name) ?? []);
    return buildRulePreview(editor.input, {
      prefs,
      listName: selectedVersion?.name ?? "",
      listItems: items,
      now: new Date(),
    });
  }, [editor, prefs, published]);

  if (accessDenied) {
    return (
      <section className="documents-page">
        <PermissionDeniedState />
      </section>
    );
  }

  return (
    <section className="documents-page">
      <p className="documents-intro" role="note">
        Правила работают только с доступными вам кандидатами. Тихие часы, рабочие
        дни и часовой пояс берутся из ваших настроек уведомлений.
      </p>

      {resource.loading && <SkeletonRows rows={4} columns={3} />}

      {!resource.loading && resource.error && (
        <ErrorState onRetry={() => void resource.reload()} />
      )}

      {!resource.loading && !resource.error && (
        <>
          <div className="document-actions">
            <Button
              variant="primary"
              icon="plus"
              onClick={() => setEditor({ input: structuredClone(blank) })}
            >
              Создать правило
            </Button>
            <Button
              variant="ghost"
              icon="loader"
              onClick={() => void resource.reload()}
            >
              Обновить правила
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
          </div>

          {rules.length === 0 && (
            <EmptyState
              icon="settings"
              title="Правил пока нет."
              description="Создайте правило: выберите этап, список документов и действие — напоминания и запросы будут уходить автоматически в ваши тихие часы."
              action={
                <Button
                  variant="primary"
                  icon="plus"
                  onClick={() => setEditor({ input: structuredClone(blank) })}
                >
                  Создать правило
                </Button>
              }
            />
          )}

          {rules.length > 0 && (
            <div className="documents-filters">
              <Field label="Поиск правила">
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    type="search"
                    placeholder="Название, тип или этап"
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                )}
              </Field>
              <Field label="Тип действия">
                {(id, describedBy) => (
                  <SelectInput
                    id={id}
                    aria-describedby={describedBy}
                    value={actionFilter}
                    onChange={(event) =>
                      setActionFilter(event.target.value as RuleParams["action"] | "all")
                    }
                  >
                    {ACTION_FILTER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              <Field label="Статус">
                {(id, describedBy) => (
                  <SelectInput
                    id={id}
                    aria-describedby={describedBy}
                    value={statusFilter}
                    onChange={(event) =>
                      setStatusFilter(event.target.value as StatusFilter)
                    }
                  >
                    {STATUS_FILTER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              <Field label="Этап">
                {(id, describedBy) => (
                  <SelectInput
                    id={id}
                    aria-describedby={describedBy}
                    value={stageFilter}
                    onChange={(event) =>
                      setStageFilter(event.target.value as CandidateStage | "all")
                    }
                  >
                    <option value="all">Все этапы</option>
                    {CANDIDATE_STAGE_ORDER.map((stage) => (
                      <option key={stage} value={stage}>
                        {STAGE_LABELS[stage]}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              {filtersActive && (
                <div className="documents-filter-meta">
                  <span role="status">
                    Показано {visible.length} из {rules.length}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    icon="undo"
                    onClick={() => {
                      setSearch("");
                      setStatusFilter("all");
                      setActionFilter("all");
                      setStageFilter("all");
                    }}
                  >
                    Сбросить фильтры
                  </Button>
                </div>
              )}
            </div>
          )}

          {rules.length > 0 && visible.length === 0 && (
            <EmptyState
              icon="search"
              title="Ничего не найдено"
              description="По заданным фильтрам правил нет. Измените поиск или сбросьте фильтры."
            />
          )}

          {visible.map((rule) => (
            <article className="document-panel" key={rule.id}>
              <header className="document-panel-head">
                <div>
                  <h2>{rule.name}</h2>
                  <p className="document-meta">
                    версия {rule.version} ·{" "}
                    {`создано: ${formatDateTime(rule.created_at)}`} ·{" "}
                    {`изменено: ${formatDateTime(rule.updated_at)}`}
                  </p>
                </div>
                <Badge tone={rule.enabled ? "success" : "neutral"}>
                  {rule.enabled ? "Включено" : "Выключено"}
                </Badge>
              </header>
              <p>{ruleSummary(rule)}</p>
              <p className="document-meta">{actions[rule.params.action]}</p>
              <div className="document-actions">
                <Button
                  size="sm"
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
                </Button>
                <Button
                  size="sm"
                  variant={rule.enabled ? "secondary" : "primary"}
                  disabled={busy}
                  onClick={() => setPendingToggle({ rule })}
                >
                  {rule.enabled ? "Выключить" : "Включить"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy && history === null}
                  onClick={() => void openHistory(rule)}
                >
                  История срабатываний
                </Button>
              </div>
            </article>
          ))}
        </>
      )}

      {editor && (
        <Modal
          open
          size="lg"
          onClose={() => setEditor(null)}
          title={editor.id ? "Редактирование правила" : "Новое правило"}
          description="Редактирование отменяет ожидающие задания предыдущей версии правила."
        >
          <div className="rule-editor">
          <form
            className="document-form"
            onSubmit={(e) => {
              e.preventDefault();
              const payload = {
                ...editor.input,
                ...(editor.id ? { expected_version: editor.version } : {}),
              };
              void mutate(
                () =>
                  request(
                    editor.id ? `/document-rules/${editor.id}` : "/document-rules",
                    {
                      method: editor.id ? "PUT" : "POST",
                      body: payload,
                    },
                  ),
                editor.id ? "Правило сохранено." : "Правило создано.",
              );
            }}
          >
            <Field label="Название правила" required>
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
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
              )}
            </Field>
            <Field label="Правило включено">
              {(id, describedBy) => (
                <input
                  id={id}
                  aria-describedby={describedBy}
                  type="checkbox"
                  className="documents-checkbox"
                  checked={editor.input.enabled}
                  onChange={(e) =>
                    setEditor({
                      ...editor,
                      input: { ...editor.input, enabled: e.target.checked },
                    })
                  }
                />
              )}
            </Field>
            <Field label="Когда это происходит" required>
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  aria-describedby={describedBy}
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
                </SelectInput>
              )}
            </Field>
            <Field label="Этап" required>
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  aria-describedby={describedBy}
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
                </SelectInput>
              )}
            </Field>
            <Field label="Что сделать" required>
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  aria-describedby={describedBy}
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
                </SelectInput>
              )}
            </Field>
            <Field label="Список" required>
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  aria-describedby={describedBy}
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
                </SelectInput>
              )}
            </Field>
            {editor.input.params.action !== "apply_list" && (
              <>
                <Field label="Условие версии">
                  {(id, describedBy) => (
                    <SelectInput
                      id={id}
                      aria-describedby={describedBy}
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
                    </SelectInput>
                  )}
                </Field>
                <p className="document-meta">
                  Обязательное условие: есть недостающие обязательные документы.
                </p>
                <Field label="Куда отправить" required>
                  {(id, describedBy) => (
                    <SelectInput
                      id={id}
                      aria-describedby={describedBy}
                      value={editor.input.params.channel ?? "email"}
                      onChange={(e) =>
                        params({ channel: e.target.value as "email" | "telegram" })
                      }
                    >
                      <option value="email">Электронная почта</option>
                      <option value="telegram">Telegram</option>
                    </SelectInput>
                  )}
                </Field>
              </>
            )}
            {editor.input.params.action === "document_reminder" && (
              <Field label="Через сколько дней (1–30)" required>
                {(id, describedBy) => (
                  <TextInput
                    id={id}
                    aria-describedby={describedBy}
                    type="number"
                    min={1}
                    max={30}
                    step={1}
                    required
                    value={editor.input.params.days ?? 1}
                    onChange={(e) => params({ days: Number(e.target.value) })}
                  />
                )}
              </Field>
            )}
            <p className="document-meta">
              Напоминание исполняется один раз для каждого снимка и версии правила.
              Редактирование отменяет ожидающие задания предыдущей версии.
            </p>
            <div className="document-actions">
              <Button type="submit" variant="primary" loading={busy}>
                Сохранить правило
              </Button>
              <Button type="button" onClick={() => setEditor(null)}>
                Отмена
              </Button>
            </div>
          </form>

          {editorPreview && (
            <aside className="rule-preview" aria-label="Что произойдёт">
              <h3 className="rule-preview-title">Что произойдёт</h3>
              <div aria-live="polite">
                {editorPreview.missing.length > 0 ? (
                  <p className="rule-preview-missing">
                    Пока не хватает: {editorPreview.missing.join(", ")}. Заполните
                    эти поля — и здесь появится описание.
                  </p>
                ) : (
                  <>
                    <p className="rule-preview-summary">
                      {editorPreview.when}, {editorPreview.what} — куда отправить:{" "}
                      {editorPreview.where}. Правило сработает только для
                      кандидатов, к которым у вас есть доступ.
                    </p>
                    {editorPreview.disabled && (
                      <p className="rule-preview-warn">
                        Правило выключено — оно не будет срабатывать, пока вы не
                        включите его.
                      </p>
                    )}
                    <p className="rule-preview-timing">{editorPreview.timing}</p>
                    {editorPreview.textSample && (
                      <>
                        <p className="document-meta">
                          Образец текста кандидату (первые ~200 символов):
                        </p>
                        <pre className="rule-preview-text">
                          {editorPreview.textSample}
                        </pre>
                        <p className="document-meta">
                          Имя кандидата и реально недостающие документы подставит
                          сервер перед отправкой.
                        </p>
                      </>
                    )}
                  </>
                )}
              </div>

              <details className="rule-howto">
                <summary>Как это работает</summary>
                <ol>
                  <li>
                    Правило срабатывает на событие: переход кандидата на этап или
                    наступление срока, когда документы не получены.
                  </li>
                  <li>
                    Правило работает только с кандидатами, к которым у вас есть
                    доступ.
                  </li>
                  <li>
                    Отправка учитывает ваши тихие часы, рабочие дни и часовой
                    пояс — ночью и в выходные сообщения ждут.
                  </li>
                  <li>
                    Правка или выключение правила отменяет ожидающие отправки
                    предыдущей версии.
                  </li>
                  <li>
                    Каждое срабатывание видно в «Истории срабатываний» на
                    карточке правила.
                  </li>
                </ol>
              </details>
            </aside>
          )}
          </div>
        </Modal>
      )}

      <ConfirmDialog
        open={pendingToggle !== null}
        danger={pendingToggle?.rule.enabled ?? false}
        onCancel={() => setPendingToggle(null)}
        onConfirm={() => pendingToggle && confirmToggle(pendingToggle.rule)}
        title={
          pendingToggle?.rule.enabled
            ? `Выключить правило «${pendingToggle.rule.name}»?`
            : `Включить правило «${pendingToggle?.rule.name ?? ""}»?`
        }
        description={
          pendingToggle?.rule.enabled
            ? "Ожидающие отправки задания этой версии правила будут отменены. Включённые настройки не изменятся — вы сможете включить правило снова."
            : "Правило снова начнёт срабатывать по своим условиям. Backend проверит доступ к списку и этап при включении."
        }
        confirmLabel={pendingToggle?.rule.enabled ? "Выключить" : "Включить"}
      />

      {history && (
        <Modal
          open
          onClose={() => setHistory(null)}
          title={`История: ${history.name}`}
          description="Исполнения в текущей области доступа; история неизменяема."
        >
          {history.rows === null && !history.error && (
            <SkeletonRows rows={3} columns={3} />
          )}
          {history.error && (
            <p role="alert" className="documents-error">
              {history.error}
            </p>
          )}
          {history.rows !== null && !history.error && history.rows.length === 0 && (
            <EmptyState
              icon="clock"
              title="Срабатываний в текущей области доступа нет."
              description="Как только правило выполнится для доступного вам кандидата, исполнение появится здесь."
            />
          )}
          {history.rows !== null && !history.error && history.rows.length > 0 && (
            <div className="table-wrap">
              <table className="document-table">
                <thead>
                  <tr>
                    <th scope="col">Время</th>
                    <th scope="col">Версия правила</th>
                    <th scope="col">Действие</th>
                    <th scope="col">Результат</th>
                    <th scope="col">Кандидат</th>
                  </tr>
                </thead>
                <tbody>
                  {history.rows.map((row) => (
                    <tr key={row.id}>
                      <td>{new Date(row.created_at).toLocaleString("ru")}</td>
                      <td>{row.rule_version}</td>
                      <td>{actions[row.action as keyof typeof actions] ?? row.action}</td>
                      <td>{outcomes[row.outcome] ?? row.outcome}</td>
                      <td className="document-meta">{row.candidate_id.slice(0, 8)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="document-actions">
            <Button onClick={() => setHistory(null)}>Закрыть историю</Button>
          </div>
        </Modal>
      )}
    </section>
  );
}
