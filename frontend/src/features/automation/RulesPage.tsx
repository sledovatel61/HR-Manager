/** «Мои правила» (phase 11): personal automation rules.
 *
 * A rule is NOT a program: the editor offers only the closed vocabulary
 * returned by the server (trigger, conditions, one action with typed
 * parameters). The list shows enable/disable, edit, delete and the recent
 * immutable executions of a rule. Every mutation carries the optimistic
 * version; a 409 is shown as an explicit conflict with a reload action,
 * a 403 as the permission state (an administrator without the pilot
 * grant cannot create rules).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  createAutomationRule,
  deleteAutomationRule,
  fetchAutomationVocabulary,
  listAutomationRuleExecutions,
  listAutomationRules,
  listPublishedDocumentLists,
  toggleAutomationRule,
  updateAutomationRule,
} from "../../api";
import { Button } from "../../design-system/components/Button";
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
  AUTOMATION_ACTION_LABELS,
  AUTOMATION_TRIGGER_LABELS,
  STAGE_LABELS,
  type AutomationActionType,
  type AutomationRule,
  type AutomationRuleExecution,
  type AutomationTriggerType,
  type AutomationVocabulary,
  type CandidateChannelName,
  type CandidateStage,
  type PublishedDocumentList,
} from "../../types";
import {
  CHANNEL_LABELS,
  EMPTY_FORM,
  buildRuleInput,
  describeAction,
  describeConditions,
  describeOutcome,
  describeTrigger,
  formFromRule,
  type RuleForm,
} from "./ruleHelpers";
import "../notifications/notifications.css";
import "./automation.css";

function formatWhen(iso: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}


interface ExecutionsPanelProps {
  rule: AutomationRule;
}

function ExecutionsPanel({ rule }: ExecutionsPanelProps) {
  const [items, setItems] = useState<AutomationRuleExecution[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setError(false);
    setItems(null);
    try {
      const payload = await listAutomationRuleExecutions(rule.id, 10);
      setItems(payload.items);
      setTotal(payload.total);
    } catch {
      setError(true);
    }
  }, [rule.id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return <ErrorState onRetry={() => void load()} />;
  }
  if (items === null) {
    return <SkeletonRows rows={3} columns={3} />;
  }
  if (items.length === 0) {
    return <p className="rule-empty-history">Правило ещё не срабатывало.</p>;
  }
  return (
    <div className="rule-history" aria-label={`Последние срабатывания правила «${rule.name}»`}>
      <p className="rule-history-total">Всего срабатываний: {total}. Показаны последние.</p>
      <ul className="rule-history-list">
        {items.map((execution) => (
          <li key={execution.id} className={`rule-history-item is-${execution.outcome}`}>
            <span className="rule-history-when">{formatWhen(execution.executed_at)}</span>
            <span className="rule-history-outcome">{describeOutcome(execution)}</span>
            <span className="rule-history-meta">
              {AUTOMATION_ACTION_LABELS[execution.action_type]} · версия правила{" "}
              {execution.rule_version}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function RulesPage() {
  const { pushToast } = useToast();
  const [vocabulary, setVocabulary] = useState<AutomationVocabulary | null>(null);
  const [lists, setLists] = useState<PublishedDocumentList[]>([]);
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [form, setForm] = useState<RuleForm>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [editing, setEditing] = useState<AutomationRule | null>(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<AutomationRule | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    setForbidden(false);
    try {
      const [vocab, published, mine] = await Promise.all([
        fetchAutomationVocabulary(),
        listPublishedDocumentLists(),
        listAutomationRules(),
      ]);
      setVocabulary(vocab);
      setLists(published.items);
      setRules(mine.items);
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

  useEffect(() => {
    void load();
  }, [load]);

  const allowedActions = useMemo<AutomationActionType[]>(() => {
    if (!vocabulary) return ["send_document_request"];
    return vocabulary.trigger_actions[form.trigger_type] ?? [];
  }, [vocabulary, form.trigger_type]);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setFormError(null);
    setConflict(null);
    setFormOpen(true);
  };

  const openEdit = (rule: AutomationRule) => {
    setEditing(rule);
    setForm(formFromRule(rule));
    setFormError(null);
    setConflict(null);
    setFormOpen(true);
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditing(null);
    setFormError(null);
  };

  const handleApiError = (caught: unknown, fallback: string) => {
    if (caught instanceof ApiError) {
      if (caught.status === 409) {
        setConflict(caught.message);
        return;
      }
      if (caught.status === 403) {
        pushToast("danger", caught.message);
        return;
      }
      pushToast("danger", caught.message || fallback);
      return;
    }
    pushToast("danger", fallback);
  };

  const submit = async () => {
    const built = buildRuleInput(form, vocabulary?.max_delay_days ?? 30);
    if ("error" in built) {
      setFormError(built.error);
      return;
    }
    setFormError(null);
    setConflict(null);
    setSaving(true);
    try {
      if (editing) {
        await updateAutomationRule(editing.id, {
          ...built.input,
          expected_version: editing.version,
        });
        pushToast("success", "Правило обновлено. Отложенные задания старой версии отменены.");
      } else {
        await createAutomationRule(built.input);
        pushToast("success", "Правило создано.");
      }
      closeForm();
      await load();
    } catch (caught) {
      handleApiError(caught, "Не удалось сохранить правило.");
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (rule: AutomationRule) => {
    setConflict(null);
    try {
      await toggleAutomationRule(rule.id, {
        expected_version: rule.version,
        is_enabled: !rule.is_enabled,
      });
      pushToast(
        "success",
        rule.is_enabled
          ? "Правило выключено. Ещё не начатые задания отменены."
          : "Правило включено.",
      );
      await load();
    } catch (caught) {
      handleApiError(caught, "Не удалось изменить состояние правила.");
    }
  };

  const remove = async () => {
    if (!deleting) return;
    const rule = deleting;
    setDeleting(null);
    try {
      await deleteAutomationRule(rule.id);
      pushToast("success", "Правило удалено. История срабатываний сохранена.");
      await load();
    } catch (caught) {
      handleApiError(caught, "Не удалось удалить правило.");
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
    return <ErrorState onRetry={() => void load()} />;
  }

  const stages = vocabulary?.stages ?? [];
  const channels = vocabulary?.channels ?? [];

  return (
    <div className="notif-page rules-page">
      <div className="rules-header">
        <p className="rules-intro">
          Правила выполняются только для кандидатов в вашей зоне доступа и в часы, разрешённые
          вашими настройками уведомлений. Текст сообщений формирует сервер из применённого списка
          документов.
        </p>
        <Button variant="primary" icon="plus" onClick={openCreate}>
          Новое правило
        </Button>
      </div>

      {conflict && (
        <StateView
          icon="alert-triangle"
          tone="warning"
          title="Данные устарели"
          description={conflict}
          action={
            <Button variant="secondary" onClick={() => void load()}>
              Обновить список правил
            </Button>
          }
        />
      )}

      {formOpen && (
        <form
          className="reminder-form rule-form"
          aria-label={editing ? "Редактирование правила" : "Новое правило"}
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Field label="Название" required>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={form.name}
                maxLength={200}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Например: оффер → запрос документов"
              />
            )}
          </Field>
          <Field label="Триггер" required>
            {(id) => (
              <SelectInput
                id={id}
                value={form.trigger_type}
                onChange={(event) => {
                  const trigger = event.target.value as AutomationTriggerType;
                  const actions = vocabulary?.trigger_actions[trigger] ?? [];
                  setForm({
                    ...form,
                    trigger_type: trigger,
                    action_type: actions.includes(form.action_type)
                      ? form.action_type
                      : (actions[0] ?? form.action_type),
                  });
                }}
              >
                {(vocabulary?.triggers ?? ["stage_entered"]).map((trigger) => (
                  <option key={trigger} value={trigger}>
                    {AUTOMATION_TRIGGER_LABELS[trigger]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          {form.trigger_type === "stage_entered" ? (
            <Field label="Этап" required>
              {(id) => (
                <SelectInput
                  id={id}
                  value={form.trigger_stage}
                  onChange={(event) =>
                    setForm({ ...form, trigger_stage: event.target.value as CandidateStage | "" })
                  }
                >
                  <option value="">— выберите этап —</option>
                  {stages.map((stage) => (
                    <option key={stage} value={stage}>
                      {STAGE_LABELS[stage]}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          ) : (
            <Field
              label="Дней после применения списка"
              required
              hint={`От 1 до ${vocabulary?.max_delay_days ?? 30}. Считается в вашей часовой зоне.`}
            >
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  type="number"
                  min={1}
                  max={vocabulary?.max_delay_days ?? 30}
                  value={form.trigger_days_after}
                  onChange={(event) => setForm({ ...form, trigger_days_after: event.target.value })}
                />
              )}
            </Field>
          )}
          <Field label="Действие" required>
            {(id) => (
              <SelectInput
                id={id}
                value={form.action_type}
                onChange={(event) =>
                  setForm({ ...form, action_type: event.target.value as AutomationActionType })
                }
              >
                {allowedActions.map((action) => (
                  <option key={action} value={action}>
                    {AUTOMATION_ACTION_LABELS[action]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          {form.action_type === "apply_document_list" ? (
            <Field label="Список документов" required hint="Применяется текущая опубликованная версия.">
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  aria-describedby={describedBy}
                  value={form.action_list_id}
                  onChange={(event) => setForm({ ...form, action_list_id: event.target.value })}
                >
                  <option value="">— выберите список —</option>
                  {lists.map((list) => (
                    <option key={list.id} value={list.id}>
                      {list.name} (версия {list.published_version_number})
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          ) : (
            <>
              <Field label="Канал" hint="Пусто — все каналы, разрешённые кандидатом.">
                {(id, describedBy) => (
                  <SelectInput
                    id={id}
                    aria-describedby={describedBy}
                    value={form.action_channel}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        action_channel: event.target.value as CandidateChannelName | "",
                      })
                    }
                  >
                    <option value="">— любой разрешённый —</option>
                    {channels.map((channel) => (
                      <option key={channel} value={channel}>
                        {CHANNEL_LABELS[channel]}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              {form.action_type === "send_document_reminder" && (
                <Field
                  label="Через сколько дней напомнить"
                  hint={`От 0 до ${vocabulary?.max_delay_days ?? 30}. Ночью и в выходные сообщение не уйдёт.`}
                >
                  {(id, describedBy) => (
                    <TextInput
                      id={id}
                      aria-describedby={describedBy}
                      type="number"
                      min={0}
                      max={vocabulary?.max_delay_days ?? 30}
                      value={form.action_delay_days}
                      onChange={(event) =>
                        setForm({ ...form, action_delay_days: event.target.value })
                      }
                    />
                  )}
                </Field>
              )}
            </>
          )}

          <fieldset className="rule-conditions field-span-2">
            <legend>Условия (необязательно)</legend>
            <div className="rule-conditions-grid">
              <Field label="Кандидат на этапе">
                {(id) => (
                  <SelectInput
                    id={id}
                    value={form.condition_stage}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        condition_stage: event.target.value as CandidateStage | "",
                      })
                    }
                  >
                    <option value="">— любой —</option>
                    {stages.map((stage) => (
                      <option key={stage} value={stage}>
                        {STAGE_LABELS[stage]}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              <Field label="Применён список">
                {(id) => (
                  <SelectInput
                    id={id}
                    value={form.condition_list_id}
                    onChange={(event) =>
                      setForm({ ...form, condition_list_id: event.target.value })
                    }
                  >
                    <option value="">— любой —</option>
                    {lists.map((list) => (
                      <option key={list.id} value={list.id}>
                        {list.name}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
              <Field label="Недостающие обязательные документы">
                {(id) => (
                  <SelectInput
                    id={id}
                    value={form.condition_missing}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        condition_missing: event.target.value as RuleForm["condition_missing"],
                      })
                    }
                  >
                    <option value="">— не проверять —</option>
                    <option value="true">есть</option>
                    <option value="false">нет</option>
                  </SelectInput>
                )}
              </Field>
              <Field label="Разрешён канал">
                {(id) => (
                  <SelectInput
                    id={id}
                    value={form.condition_channel}
                    onChange={(event) =>
                      setForm({
                        ...form,
                        condition_channel: event.target.value as CandidateChannelName | "",
                      })
                    }
                  >
                    <option value="">— не проверять —</option>
                    {channels.map((channel) => (
                      <option key={channel} value={channel}>
                        {CHANNEL_LABELS[channel]}
                      </option>
                    ))}
                  </SelectInput>
                )}
              </Field>
            </div>
          </fieldset>

          {formError && (
            <p className="field-error field-span-2" role="alert">
              {formError}
            </p>
          )}
          <div className="field-span-2 rule-form-actions">
            <Button type="submit" variant="primary" loading={saving}>
              {editing ? "Сохранить изменения" : "Создать правило"}
            </Button>
            <Button type="button" variant="secondary" onClick={closeForm}>
              Отмена
            </Button>
          </div>
        </form>
      )}

      <div aria-live="polite">
        {rules.length === 0 ? (
          <EmptyState
            icon="settings"
            title="Правил пока нет"
            description="Создайте первое правило: например, при переходе кандидата на этап «Оффер» применить список документов и отправить запрос."
          />
        ) : (
          <ul className="notif-list rules-list">
            {rules.map((rule) => {
              const conditions = describeConditions(rule, lists);
              const isExpanded = expanded === rule.id;
              return (
                <li key={rule.id} className={`notif-card rule-card ${rule.is_enabled ? "" : "is-disabled"}`}>
                  <div className="notif-card-main">
                    <div className="rule-card-title-row">
                      <h3 className="notif-card-title">{rule.name}</h3>
                      <Badge tone={rule.is_enabled ? "success" : "neutral"}>
                        {rule.is_enabled ? "Включено" : "Выключено"}
                      </Badge>
                    </div>
                    <dl className="rule-card-summary">
                      <dt>Когда</dt>
                      <dd>{describeTrigger(rule)}</dd>
                      <dt>Что</dt>
                      <dd>{describeAction(rule, lists)}</dd>
                      {conditions.length > 0 && (
                        <>
                          <dt>Если</dt>
                          <dd>{conditions.join("; ")}</dd>
                        </>
                      )}
                    </dl>
                    <div className="notif-card-meta">
                      <span>версия {rule.version}</span>
                      <span>изменено {formatWhen(rule.updated_at)}</span>
                    </div>
                    {isExpanded && <ExecutionsPanel rule={rule} />}
                  </div>
                  <div className="notif-card-actions">
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-pressed={rule.is_enabled}
                      onClick={() => void toggle(rule)}
                    >
                      {rule.is_enabled ? "Выключить" : "Включить"}
                    </Button>
                    <Button variant="secondary" size="sm" onClick={() => openEdit(rule)}>
                      Изменить
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-expanded={isExpanded}
                      onClick={() => setExpanded(isExpanded ? null : rule.id)}
                    >
                      {isExpanded ? "Скрыть срабатывания" : "Срабатывания"}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => setDeleting(rule)}>
                      Удалить
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <ConfirmDialog
        open={deleting !== null}
        onCancel={() => setDeleting(null)}
        onConfirm={() => void remove()}
        title="Удалить правило?"
        description={`Правило «${deleting?.name ?? ""}» перестанет выполняться, ещё не начатые задания будут отменены. История срабатываний сохранится.`}
        confirmLabel="Удалить"
        danger
      />
    </div>
  );
}
