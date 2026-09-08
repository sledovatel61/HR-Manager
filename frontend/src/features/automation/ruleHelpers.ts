/** Pure helpers of the «Мои правила» screen (phase 11): typed payload
 * building from the closed form and Russian descriptions of stored rules
 * and executions. Kept apart from the component for fast refresh and for
 * unit tests without rendering. */

import {
  AUTOMATION_OUTCOME_LABELS,
  AUTOMATION_SKIP_LABELS,
  STAGE_LABELS,
  type AutomationActionType,
  type AutomationRule,
  type AutomationRuleExecution,
  type AutomationRuleInput,
  type AutomationTriggerType,
  type CandidateChannelName,
  type CandidateStage,
  type PublishedDocumentList,
} from "../../types";

export const CHANNEL_LABELS: Record<CandidateChannelName, string> = {
  email: "Электронная почта",
  telegram: "Telegram",
};

export interface RuleForm {
  name: string;
  trigger_type: AutomationTriggerType;
  trigger_stage: CandidateStage | "";
  trigger_days_after: string;
  condition_stage: CandidateStage | "";
  condition_list_id: string;
  condition_missing: "" | "true" | "false";
  condition_channel: CandidateChannelName | "";
  action_type: AutomationActionType;
  action_list_id: string;
  action_channel: CandidateChannelName | "";
  action_delay_days: string;
}

export const EMPTY_FORM: RuleForm = {
  name: "",
  trigger_type: "stage_entered",
  trigger_stage: "",
  trigger_days_after: "3",
  condition_stage: "",
  condition_list_id: "",
  condition_missing: "",
  condition_channel: "",
  action_type: "send_document_request",
  action_list_id: "",
  action_channel: "",
  action_delay_days: "0",
};

export function formFromRule(rule: AutomationRule): RuleForm {
  const trigger = rule.trigger_params as { stage?: CandidateStage; days_after?: number };
  const action = rule.action_params as {
    list_id?: string;
    channel?: CandidateChannelName;
    delay_days?: number;
  };
  return {
    name: rule.name,
    trigger_type: rule.trigger_type,
    trigger_stage: trigger.stage ?? "",
    trigger_days_after: String(trigger.days_after ?? 3),
    condition_stage: rule.conditions.stage ?? "",
    condition_list_id: rule.conditions.list_id ?? "",
    condition_missing:
      rule.conditions.has_missing_required === undefined
        ? ""
        : rule.conditions.has_missing_required
          ? "true"
          : "false",
    condition_channel: rule.conditions.channel ?? "",
    action_type: rule.action_type,
    action_list_id: action.list_id ?? "",
    action_channel: action.channel ?? "",
    action_delay_days: String(action.delay_days ?? 0),
  };
}

/** Build the typed payload; returns a Russian validation message on failure. */
export function buildRuleInput(
  form: RuleForm,
  maxDelayDays: number,
): { input: AutomationRuleInput } | { error: string } {
  const name = form.name.trim();
  if (!name) {
    return { error: "Укажите название правила." };
  }
  const triggerParams: Record<string, unknown> = {};
  if (form.trigger_type === "stage_entered") {
    if (!form.trigger_stage) {
      return { error: "Выберите этап, при переходе на который сработает правило." };
    }
    triggerParams.stage = form.trigger_stage;
  } else {
    const days = Number(form.trigger_days_after);
    if (!Number.isInteger(days) || days < 1 || days > maxDelayDays) {
      return { error: `Срок ожидания документов — целое число от 1 до ${maxDelayDays} дней.` };
    }
    triggerParams.days_after = days;
  }
  const conditions: AutomationRuleInput["conditions"] = {};
  if (form.condition_stage) conditions.stage = form.condition_stage;
  if (form.condition_list_id) conditions.list_id = form.condition_list_id;
  if (form.condition_missing) conditions.has_missing_required = form.condition_missing === "true";
  if (form.condition_channel) conditions.channel = form.condition_channel;
  const actionParams: Record<string, unknown> = {};
  if (form.action_type === "apply_document_list") {
    if (!form.action_list_id) {
      return { error: "Выберите опубликованный список документов для применения." };
    }
    actionParams.list_id = form.action_list_id;
  } else {
    if (form.action_channel) actionParams.channel = form.action_channel;
    if (form.action_type === "send_document_reminder") {
      const delay = Number(form.action_delay_days);
      if (!Number.isInteger(delay) || delay < 0 || delay > maxDelayDays) {
        return { error: `Задержка напоминания — целое число от 0 до ${maxDelayDays} дней.` };
      }
      actionParams.delay_days = delay;
    }
  }
  return {
    input: {
      name,
      trigger_type: form.trigger_type,
      trigger_params: triggerParams,
      conditions,
      action_type: form.action_type,
      action_params: actionParams,
    },
  };
}

export function describeTrigger(rule: AutomationRule): string {
  const params = rule.trigger_params as { stage?: CandidateStage; days_after?: number };
  if (rule.trigger_type === "stage_entered") {
    return `Переход на этап «${params.stage ? STAGE_LABELS[params.stage] : "?"}»`;
  }
  return `Обязательные документы не получены через ${params.days_after ?? "?"} дн. после применения списка`;
}

export function describeAction(rule: AutomationRule, lists: PublishedDocumentList[]): string {
  const params = rule.action_params as {
    list_id?: string;
    channel?: CandidateChannelName;
    delay_days?: number;
  };
  if (rule.action_type === "apply_document_list") {
    const list = lists.find((entry) => entry.id === params.list_id);
    return `Применить список «${list ? list.name : "список недоступен"}» (если ещё не применён)`;
  }
  const channel = params.channel ? ` по каналу «${CHANNEL_LABELS[params.channel]}»` : "";
  if (rule.action_type === "send_document_request") {
    return `Отправить запрос документов${channel}`;
  }
  const delay = params.delay_days ? ` через ${params.delay_days} дн.` : "";
  return `Отправить напоминание о документах${delay}${channel}`;
}

export function describeConditions(rule: AutomationRule, lists: PublishedDocumentList[]): string[] {
  const parts: string[] = [];
  const conditions = rule.conditions;
  if (conditions.stage) parts.push(`этап «${STAGE_LABELS[conditions.stage]}»`);
  if (conditions.list_id) {
    const list = lists.find((entry) => entry.id === conditions.list_id);
    parts.push(`применён список «${list ? list.name : "недоступен"}»`);
  }
  if (conditions.has_missing_required !== undefined) {
    parts.push(
      conditions.has_missing_required
        ? "есть недостающие обязательные документы"
        : "нет недостающих обязательных документов",
    );
  }
  if (conditions.channel) parts.push(`разрешён канал «${CHANNEL_LABELS[conditions.channel]}»`);
  return parts;
}

export function describeOutcome(execution: AutomationRuleExecution): string {
  const base = AUTOMATION_OUTCOME_LABELS[execution.outcome] ?? execution.outcome;
  if (!execution.outcome_class) {
    return base;
  }
  if (execution.outcome === "failed") {
    return `${base}: внутренняя ошибка правила (основная операция не пострадала)`;
  }
  const reason = AUTOMATION_SKIP_LABELS[execution.outcome_class] ?? execution.outcome_class;
  return `${base}: ${reason}`;
}
