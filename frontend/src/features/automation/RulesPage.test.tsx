import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  AutomationRule,
  AutomationRuleExecution,
  AutomationVocabulary,
  PublishedDocumentList,
} from "../../types";
import { RulesPage } from "./RulesPage";
import { buildRuleInput, describeOutcome, EMPTY_FORM } from "./ruleHelpers";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    fetchAutomationVocabulary: vi.fn(),
    listPublishedDocumentLists: vi.fn(),
    listAutomationRules: vi.fn(),
    createAutomationRule: vi.fn(),
    updateAutomationRule: vi.fn(),
    toggleAutomationRule: vi.fn(),
    deleteAutomationRule: vi.fn(),
    listAutomationRuleExecutions: vi.fn(),
  };
});

import * as api from "../../api";

const VOCABULARY: AutomationVocabulary = {
  triggers: ["stage_entered", "documents_missing_due"],
  actions: ["apply_document_list", "send_document_request", "send_document_reminder"],
  trigger_actions: {
    stage_entered: ["apply_document_list", "send_document_request", "send_document_reminder"],
    documents_missing_due: ["send_document_reminder"],
  },
  channels: ["email", "telegram"],
  stages: ["new", "contacted", "interview_scheduled", "offer", "hired", "rejected"],
  max_delay_days: 30,
};

const LIST: PublishedDocumentList = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Документы для оформления",
  description: "",
  scope_position: null,
  scope_stage: null,
  published_version_id: "22222222-2222-4222-8222-222222222222",
  published_version_number: 2,
  items: [
    {
      id: "33333333-3333-4333-8333-333333333333",
      item_key: "passport",
      name: "Паспорт",
      explanation: "",
      is_required: true,
      sort_order: 1,
    },
  ],
};

const RULE: AutomationRule = {
  id: "44444444-4444-4444-8444-444444444444",
  owner_user_id: "55555555-5555-4555-8555-555555555555",
  name: "Оффер → запрос документов",
  is_enabled: true,
  trigger_type: "stage_entered",
  trigger_params: { stage: "offer" },
  conditions: { has_missing_required: true },
  action_type: "send_document_request",
  action_params: {},
  version: 3,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
};

const EXECUTION: AutomationRuleExecution = {
  id: "66666666-6666-4666-8666-666666666666",
  rule_id: RULE.id,
  rule_version: 3,
  trigger_type: "stage_entered",
  trigger_object_type: "candidate",
  trigger_object_id: "77777777-7777-4777-8777-777777777777",
  trigger_object_version: 4,
  candidate_id: "77777777-7777-4777-8777-777777777777",
  action_type: "send_document_request",
  outcome: "skipped",
  outcome_class: "no_allowed_channel",
  dedupe_key: "stage:x",
  list_id: null,
  list_version_id: null,
  executed_at: "2026-09-03T09:00:00Z",
};

function renderPage() {
  return render(
    <ToastProvider>
      <RulesPage />
    </ToastProvider>,
  );
}

describe("RulesPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.fetchAutomationVocabulary).mockResolvedValue(VOCABULARY);
    vi.mocked(api.listPublishedDocumentLists).mockResolvedValue({ items: [LIST] });
    vi.mocked(api.listAutomationRuleExecutions).mockResolvedValue({ items: [], total: 0 });
  });

  it("shows a loading skeleton and then the empty state", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [], total: 0 });
    renderPage();
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(await screen.findByText("Правил пока нет")).toBeInTheDocument();
  });

  it("shows the error state and retries", async () => {
    vi.mocked(api.listAutomationRules)
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ items: [RULE], total: 1 });
    renderPage();
    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Повторить попытку/ }));
    expect(await screen.findByText(RULE.name)).toBeInTheDocument();
    expect(api.listAutomationRules).toHaveBeenCalledTimes(2);
  });

  it("shows the permission state on 403", async () => {
    vi.mocked(api.listAutomationRules).mockRejectedValue(
      new api.ApiError(403, "Недостаточно прав для правил."),
    );
    renderPage();
    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
  });

  it("renders a rule in Russian with trigger, action and conditions", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    renderPage();
    expect(await screen.findByText(RULE.name)).toBeInTheDocument();
    expect(screen.getByText("Переход на этап «Оффер»")).toBeInTheDocument();
    expect(screen.getByText("Отправить запрос документов")).toBeInTheDocument();
    expect(screen.getByText("есть недостающие обязательные документы")).toBeInTheDocument();
    expect(screen.getByText("Включено")).toBeInTheDocument();
  });

  it("creates a rule from the closed vocabulary (keyboard accessible form)", async () => {
    vi.mocked(api.listAutomationRules)
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [RULE], total: 1 });
    vi.mocked(api.createAutomationRule).mockResolvedValue(RULE);
    renderPage();
    await screen.findByText("Правил пока нет");
    await userEvent.click(screen.getByRole("button", { name: "Новое правило" }));
    const form = screen.getByRole("form", { name: "Новое правило" });
    await userEvent.type(within(form).getByLabelText(/Название/), "Оффер → запрос документов");
    await userEvent.selectOptions(within(form).getByLabelText(/^Этап/), "offer");
    await userEvent.selectOptions(
      within(form).getByLabelText(/^Действие/),
      "send_document_request",
    );
    await userEvent.selectOptions(
      within(form).getByLabelText(/Недостающие обязательные документы/),
      "true",
    );
    // Keyboard submit: Enter inside the name field.
    within(form).getByLabelText(/Название/).focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(api.createAutomationRule).toHaveBeenCalledTimes(1));
    expect(api.createAutomationRule).toHaveBeenCalledWith({
      name: "Оффер → запрос документов",
      trigger_type: "stage_entered",
      trigger_params: { stage: "offer" },
      conditions: { has_missing_required: true },
      action_type: "send_document_request",
      action_params: {},
    });
    expect(await screen.findByText("Правило создано.")).toBeInTheDocument();
  });

  it("restricts actions by trigger and validates parameters client-side", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [], total: 0 });
    renderPage();
    await screen.findByText("Правил пока нет");
    await userEvent.click(screen.getByRole("button", { name: "Новое правило" }));
    const form = screen.getByRole("form", { name: "Новое правило" });
    await userEvent.selectOptions(within(form).getByLabelText(/^Триггер/), "documents_missing_due");
    const actions = within(form).getByLabelText(/^Действие/) as HTMLSelectElement;
    expect(Array.from(actions.options).map((option) => option.value)).toEqual([
      "send_document_reminder",
    ]);
    await userEvent.type(within(form).getByLabelText(/Название/), "Напоминание");
    await userEvent.clear(within(form).getByLabelText(/Дней после применения списка/));
    await userEvent.type(within(form).getByLabelText(/Дней после применения списка/), "99");
    await userEvent.click(screen.getByRole("button", { name: "Создать правило" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("от 1 до 30");
    expect(api.createAutomationRule).not.toHaveBeenCalled();
  });

  it("toggles a rule with the optimistic version", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    vi.mocked(api.toggleAutomationRule).mockResolvedValue({ ...RULE, is_enabled: false, version: 4 });
    renderPage();
    await screen.findByText(RULE.name);
    await userEvent.click(screen.getByRole("button", { name: "Выключить" }));
    await waitFor(() =>
      expect(api.toggleAutomationRule).toHaveBeenCalledWith(RULE.id, {
        expected_version: 3,
        is_enabled: false,
      }),
    );
    expect(await screen.findByText(/Правило выключено/)).toBeInTheDocument();
  });

  it("shows a conflict state on 409 with a reload action", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    vi.mocked(api.toggleAutomationRule).mockRejectedValue(
      new api.ApiError(409, "Правило было изменено другим пользователем."),
    );
    renderPage();
    await screen.findByText(RULE.name);
    await userEvent.click(screen.getByRole("button", { name: "Выключить" }));
    expect(await screen.findByText("Данные устарели")).toBeInTheDocument();
    expect(screen.getByText("Правило было изменено другим пользователем.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Обновить список правил" }));
    await waitFor(() => expect(api.listAutomationRules).toHaveBeenCalledTimes(2));
  });

  it("edits a rule sending the expected version", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    vi.mocked(api.updateAutomationRule).mockResolvedValue({ ...RULE, version: 4 });
    renderPage();
    await screen.findByText(RULE.name);
    await userEvent.click(screen.getByRole("button", { name: "Изменить" }));
    const form = screen.getByRole("form", { name: "Редактирование правила" });
    const name = within(form).getByLabelText(/Название/);
    await userEvent.clear(name);
    await userEvent.type(name, "Новое имя");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));
    await waitFor(() =>
      expect(api.updateAutomationRule).toHaveBeenCalledWith(
        RULE.id,
        expect.objectContaining({ expected_version: 3, name: "Новое имя" }),
      ),
    );
  });

  it("deletes a rule after confirmation", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    vi.mocked(api.deleteAutomationRule).mockResolvedValue(undefined);
    renderPage();
    await screen.findByText(RULE.name);
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(api.deleteAutomationRule).toHaveBeenCalledWith(RULE.id));
    expect(await screen.findByText(/История срабатываний сохранена/)).toBeInTheDocument();
  });

  it("shows recent executions with Russian outcome labels", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    vi.mocked(api.listAutomationRuleExecutions).mockResolvedValue({ items: [EXECUTION], total: 1 });
    renderPage();
    await screen.findByText(RULE.name);
    const button = screen.getByRole("button", { name: "Срабатывания" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(
      await screen.findByText("Пропущено: нет разрешённого канала связи"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Скрыть срабатывания" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(api.listAutomationRuleExecutions).toHaveBeenCalledWith(RULE.id, 10);
  });

  it("shows an error with retry when executions fail to load", async () => {
    vi.mocked(api.listAutomationRules).mockResolvedValue({ items: [RULE], total: 1 });
    vi.mocked(api.listAutomationRuleExecutions)
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ items: [], total: 0 });
    renderPage();
    await screen.findByText(RULE.name);
    await userEvent.click(screen.getByRole("button", { name: "Срабатывания" }));
    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Повторить попытку/ }));
    expect(await screen.findByText("Правило ещё не срабатывало.")).toBeInTheDocument();
  });
});

describe("rule helpers", () => {
  it("builds a typed payload and rejects out-of-range delays", () => {
    const ok = buildRuleInput(
      {
        ...EMPTY_FORM,
        name: "Напоминание",
        trigger_type: "stage_entered",
        trigger_stage: "offer",
        action_type: "send_document_reminder",
        action_channel: "telegram",
        action_delay_days: "3",
      },
      30,
    );
    expect(ok).toEqual({
      input: {
        name: "Напоминание",
        trigger_type: "stage_entered",
        trigger_params: { stage: "offer" },
        conditions: {},
        action_type: "send_document_reminder",
        action_params: { channel: "telegram", delay_days: 3 },
      },
    });
    const tooLong = buildRuleInput(
      {
        ...EMPTY_FORM,
        name: "x",
        trigger_stage: "offer",
        action_type: "send_document_reminder",
        action_delay_days: "31",
      },
      30,
    );
    expect("error" in tooLong && tooLong.error).toMatch(/от 0 до 30/);
    const noList = buildRuleInput(
      { ...EMPTY_FORM, name: "x", trigger_stage: "offer", action_type: "apply_document_list" },
      30,
    );
    expect("error" in noList && noList.error).toMatch(/список/);
  });

  it("describes outcomes without exposing raw classes when a label exists", () => {
    expect(describeOutcome(EXECUTION)).toBe("Пропущено: нет разрешённого канала связи");
    expect(
      describeOutcome({ ...EXECUTION, outcome: "failed", outcome_class: "internal_error:KeyError" }),
    ).toBe("Ошибка: внутренняя ошибка правила (основная операция не пострадала)");
    expect(describeOutcome({ ...EXECUTION, outcome: "queued", outcome_class: null })).toBe(
      "Поставлено в очередь",
    );
  });
});
