import { useEffect, useState, useCallback } from "react";
import { listRules, createRule, updateRule, toggleRule, deleteRule, listRuleExecutions, listDocumentLists } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Icon } from "../../design-system/icons/Icon";
import type {
  AutomationRule,
  AutomationRuleExecution,
  DocumentList,
  RuleTriggerType,
  RuleActionType,
  CandidateStage,
} from "../../types";
import { CANDIDATE_STAGE_ORDER, STAGE_LABELS, RULE_TRIGGER_LABELS, RULE_ACTION_LABELS } from "../../types";

export function MyRulesPage() {
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [docLists, setDocLists] = useState<DocumentList[]>([]);
  const [execRuleId, setExecRuleId] = useState<string | null>(null);
  const [executions, setExecutions] = useState<AutomationRuleExecution[]>([]);
  const [execTotal, setExecTotal] = useState(0);

  // Create form state
  const [title, setTitle] = useState("");
  const [triggerType, setTriggerType] = useState<RuleTriggerType>("stage_transition");
  const [triggerStage, setTriggerStage] = useState<CandidateStage>("offer");
  const [triggerDelayDays, setTriggerDelayDays] = useState(7);
  const [actionType, setActionType] = useState<RuleActionType>("document_request");
  const [actionListId, setActionListId] = useState("");
  const [actionDelayDays, setActionDelayDays] = useState(3);
  const [condStage, setCondStage] = useState<string>("");
  const [condHasMissing, setCondHasMissing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rulesData, listsData] = await Promise.all([listRules(), listDocumentLists()]);
      setRules(rulesData.items);
      setDocLists(listsData.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = async () => {
    if (!title.trim()) return;
    const triggerParams: Record<string, unknown> =
      triggerType === "stage_transition" ? { stage: triggerStage } : { delay_days: triggerDelayDays };
    const conditions: Record<string, unknown> | null =
      condStage || condHasMissing
        ? { ...(condStage ? { stage: condStage } : {}), ...(condHasMissing ? { has_missing_mandatory: true } : {}) }
        : null;
    const actionParams: Record<string, unknown> =
      actionType === "apply_list" ? { list_id: actionListId } : actionType === "document_reminder" ? { delay_days: actionDelayDays } : {};
    try {
      await createRule({
        title: title.trim(),
        trigger_type: triggerType,
        trigger_params: triggerParams,
        conditions,
        action_type: actionType,
        action_params: actionParams,
      });
      setShowCreate(false);
      setTitle("");
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка создания");
    }
  };

  const handleToggle = async (ruleId: string) => {
    try {
      await toggleRule(ruleId);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка переключения");
    }
  };

  const handleDelete = async (ruleId: string) => {
    try {
      await deleteRule(ruleId);
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка удаления");
    }
  };

  const handleShowExecutions = async (ruleId: string) => {
    setExecRuleId(ruleId);
    try {
      const data = await listRuleExecutions(ruleId, 20);
      setExecutions(data.items);
      setExecTotal(data.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ошибка загрузки истории");
    }
  };

  if (loading) return <p className="muted">Загрузка…</p>;

  return (
    <div className="my-rules-page">
      <div className="page-toolbar">
        <Button variant="primary" onClick={() => setShowCreate(true)}>
          <Icon name="plus" size={14} /> Новое правило
        </Button>
      </div>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)}>✕</button></div>}

      {showCreate && (
        <div className="modal-backdrop" onClick={() => setShowCreate(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Создание правила">
            <h3>Новое правило</h3>
            <label>
              Название
              <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus />
            </label>
            <fieldset>
              <legend>Триггер</legend>
              <select value={triggerType} onChange={(e) => setTriggerType(e.target.value as RuleTriggerType)}>
                <option value="stage_transition">{RULE_TRIGGER_LABELS.stage_transition}</option>
                <option value="scheduled_reminder">{RULE_TRIGGER_LABELS.scheduled_reminder}</option>
              </select>
              {triggerType === "stage_transition" && (
                <select value={triggerStage} onChange={(e) => setTriggerStage(e.target.value as CandidateStage)}>
                  {CANDIDATE_STAGE_ORDER.map((s) => (
                    <option key={s} value={s}>{STAGE_LABELS[s]}</option>
                  ))}
                </select>
              )}
              {triggerType === "scheduled_reminder" && (
                <label>
                  Дней после перехода
                  <input type="number" min={1} max={365} value={triggerDelayDays} onChange={(e) => setTriggerDelayDays(Number(e.target.value))} />
                </label>
              )}
            </fieldset>
            <fieldset>
              <legend>Условия</legend>
              <label>
                Этап кандидата
                <select value={condStage} onChange={(e) => setCondStage(e.target.value)}>
                  <option value="">Любой</option>
                  {CANDIDATE_STAGE_ORDER.map((s) => (
                    <option key={s} value={s}>{STAGE_LABELS[s]}</option>
                  ))}
                </select>
              </label>
              <label>
                <input type="checkbox" checked={condHasMissing} onChange={(e) => setCondHasMissing(e.target.checked)} />
                Есть недостающие обязательные документы
              </label>
            </fieldset>
            <fieldset>
              <legend>Действие</legend>
              <select value={actionType} onChange={(e) => setActionType(e.target.value as RuleActionType)}>
                <option value="apply_list">{RULE_ACTION_LABELS.apply_list}</option>
                <option value="document_request">{RULE_ACTION_LABELS.document_request}</option>
                <option value="document_reminder">{RULE_ACTION_LABELS.document_reminder}</option>
              </select>
              {actionType === "apply_list" && (
                <select value={actionListId} onChange={(e) => setActionListId(e.target.value)}>
                  <option value="">— Выберите список —</option>
                  {docLists.filter((dl) => dl.published_version).map((dl) => (
                    <option key={dl.id} value={dl.id}>{dl.title}</option>
                  ))}
                </select>
              )}
              {actionType === "document_reminder" && (
                <label>
                  Напомнить через дней
                  <input type="number" min={1} max={365} value={actionDelayDays} onChange={(e) => setActionDelayDays(Number(e.target.value))} />
                </label>
              )}
            </fieldset>
            <div className="modal-actions">
              <Button variant="secondary" onClick={() => setShowCreate(false)}>Отмена</Button>
              <Button variant="primary" onClick={() => void handleCreate()}>Создать</Button>
            </div>
          </div>
        </div>
      )}

      {execRuleId && (
        <div className="modal-backdrop" onClick={() => setExecRuleId(null)}>
          <div className="modal modal-wide" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="История правила">
            <h3>История срабатываний (всего: {execTotal})</h3>
            {executions.length === 0 ? (
              <p className="muted">Нет записей.</p>
            ) : (
              <table className="exec-table">
                <thead>
                  <tr><th>Дата</th><th>Кандидат</th><th>Действие</th><th>Результат</th></tr>
                </thead>
                <tbody>
                  {executions.map((ex) => (
                    <tr key={ex.id}>
                      <td>{new Date(ex.created_at).toLocaleString("ru-RU")}</td>
                      <td>{ex.candidate_id.slice(0, 8)}…</td>
                      <td>{ex.action_type}</td>
                      <td><span className={`outcome-${ex.outcome}`}>{ex.outcome}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="modal-actions">
              <Button variant="secondary" onClick={() => setExecRuleId(null)}>Закрыть</Button>
            </div>
          </div>
        </div>
      )}

      {rules.length === 0 ? (
        <div className="empty-state">
          <Icon name="settings" size={32} />
          <p>У вас пока нет правил автоматизации.</p>
        </div>
      ) : (
        <div className="rules-list">
          {rules.map((rule) => (
            <div key={rule.id} className={`rule-card ${rule.enabled ? "" : "rule-disabled"}`}>
              <div className="rule-header">
                <h3>{rule.title}</h3>
                <span className={`status-chip ${rule.enabled ? "enabled" : "disabled"}`}>
                  {rule.enabled ? "Включено" : "Выключено"}
                </span>
              </div>
              <div className="rule-details">
                <span><strong>Триггер:</strong> {RULE_TRIGGER_LABELS[rule.trigger_type]}</span>
                {rule.trigger_type === "stage_transition" && rule.trigger_params.stage && (
                  <span> → {STAGE_LABELS[rule.trigger_params.stage as string] || rule.trigger_params.stage}</span>
                )}
                {rule.trigger_type === "scheduled_reminder" && (
                  <span> через {String(rule.trigger_params.delay_days)} дн.</span>
                )}
              </div>
              <div className="rule-details">
                <span><strong>Действие:</strong> {RULE_ACTION_LABELS[rule.action_type]}</span>
              </div>
              <div className="rule-actions">
                <Button size="sm" onClick={() => void handleToggle(rule.id)}>
                  {rule.enabled ? "Выключить" : "Включить"}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => void handleShowExecutions(rule.id)}>
                  История
                </Button>
                <Button size="sm" variant="secondary" onClick={() => void handleDelete(rule.id)}>
                  Удалить
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
