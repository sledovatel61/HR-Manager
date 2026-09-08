import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError, documentRequest } from "../../api";
import { DocumentListsPage } from "./DocumentListsPage";
import { DocumentsTab } from "./DocumentsTab";
import { MyRulesPage } from "./MyRulesPage";
import type {
  CandidateDocuments,
  DocumentLists,
  DocumentRule,
  DocumentVersion,
} from "./types";

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  documentRequest: vi.fn(),
}));
const api = vi.mocked(documentRequest);
const version: DocumentVersion = {
  id: "version-1",
  list_id: "list-1",
  number: 1,
  stage: "",
  state: "published",
  name: "Оформление",
  description: "Список",
  items: [
    { key: "passport", name: "Паспорт", explanation: "", required: true },
  ],
  author_id: "admin",
  created_at: "2026-09-08T12:00:00Z",
  published_at: "2026-09-08T12:00:00Z",
};
const lists: DocumentLists = {
  can_manage: true,
  items: [{ id: "list-1", stage: "", version: 2, versions: [version] }],
};
const documents: CandidateDocuments = {
  revision: 1,
  set_id: "set-1",
  exact_version: version,
  items: [
    {
      ...version.items[0],
      state: "missing",
      version: 1,
      changed_by: "hr",
      changed_at: "2026-09-08T12:00:00Z",
    },
  ],
  missing_required: ["passport"],
};
const rule: DocumentRule = {
  id: "rule-1",
  version: 1,
  name: "Запрос после оффера",
  enabled: true,
  created_at: "2026-09-08T12:00:00Z",
  updated_at: "2026-09-08T12:00:00Z",
  params: {
    trigger: "stage_transition",
    action: "document_request",
    stage: "offer",
    list_id: "list-1",
    list_version_id: null,
    missing_required: true,
    channel: "email",
    days: null,
  },
};
beforeEach(() => {
  vi.resetAllMocks();
  api.mockImplementation(async (path) => {
    if (path === "/document-lists") return lists as never;
    if (path === "/document-rules") return [rule] as never;
    if (path.endsWith("/history")) return [] as never;
    if (path.endsWith("/documents")) return documents as never;
    return {} as never;
  });
});

describe("Списки документов", () => {
  it("shows loading and an empty list", async () => {
    api.mockResolvedValue({ items: [], can_manage: true });
    render(<DocumentListsPage />);
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка");
    expect(await screen.findByText("Списков пока нет.")).toBeInTheDocument();
  });
  it("shows errors and retries", async () => {
    api.mockRejectedValueOnce(new ApiError(403, "forbidden"));
    render(<DocumentListsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Недостаточно прав",
    );
    await userEvent.click(screen.getByText("Повторить загрузку"));
    expect(await screen.findByText("Оформление")).toBeInTheDocument();
  });
  it("hides management when the server denies the grant", async () => {
    api.mockResolvedValue({ ...lists, can_manage: false });
    render(<DocumentListsPage />);
    await screen.findByText("Оформление");
    expect(screen.queryByText("Новый список")).toBeNull();
    expect(screen.queryByText("Создать новую версию")).toBeNull();
  });
  it("creates a draft through the API with ordered typed items", async () => {
    const user = userEvent.setup();
    render(<DocumentListsPage />);
    await screen.findByText("Оформление");
    await user.click(screen.getByRole("button", { name: "Новый список" }));
    await user.type(screen.getByLabelText(/^Название$/), "Трудоустройство");
    await user.type(screen.getByLabelText("Русское название"), "Паспорт");
    await user.click(
      screen.getByRole("button", { name: "Сохранить черновик" }),
    );
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/document-lists",
        expect.objectContaining({
          method: "POST",
          body: expect.objectContaining({
            name: "Трудоустройство",
            stage: null,
            items: [
              {
                key: "document_1",
                name: "Паспорт",
                explanation: "",
                required: true,
              },
            ],
          }),
        }),
      ),
    );
  });
  it("clones a published version instead of editing it in place and surfaces conflicts", async () => {
    const user = userEvent.setup();
    render(<DocumentListsPage />);
    await screen.findByText("Оформление");
    await user.click(screen.getByText("Версия 1 — Опубликована"));
    await user.click(screen.getByText("Создать новую версию"));
    api.mockRejectedValueOnce(new ApiError(409, "Версия изменилась"));
    await user.click(screen.getByText("Сохранить черновик"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Конфликт версии",
    );
    expect(api).toHaveBeenCalledWith(
      "/document-lists/list-1/versions",
      expect.objectContaining({
        method: "POST",
        body: expect.objectContaining({ expected_version: 2 }),
      }),
    );
  });
});

describe("Документы кандидата", () => {
  it("shows no assignment and applies an exact published version", async () => {
    const user = userEvent.setup();
    api.mockImplementation(
      async (path) =>
        (path === "/document-lists"
          ? lists
          : {
              revision: 0,
              set_id: null,
              exact_version: null,
              items: [],
              missing_required: [],
            }) as never,
    );
    render(<DocumentsTab candidateId="candidate-1" stage="new" />);
    await screen.findByText("Список ещё не применён.");
    await user.selectOptions(
      screen.getByLabelText("Опубликованная версия"),
      "version-1",
    );
    await user.click(screen.getByText("Применить список"));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/candidates/candidate-1/documents", {
        method: "POST",
        body: { version_id: "version-1", expected_revision: 0 },
      }),
    );
  });
  it("updates receipts with the exact set and optimistic item version", async () => {
    render(<DocumentsTab candidateId="candidate-1" stage="new" />);
    await userEvent.click(await screen.findByLabelText("Получен: Паспорт"));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/candidates/candidate-1/documents/passport",
        {
          method: "PATCH",
          body: { set_id: "set-1", expected_version: 1, state: "received" },
        },
      ),
    );
  });
  it("disables sends for a complete list", async () => {
    api.mockImplementation(
      async (path) =>
        (path === "/document-lists"
          ? lists
          : { ...documents, missing_required: [] }) as never,
    );
    render(<DocumentsTab candidateId="candidate-1" stage="new" />);
    await screen.findByText(
      "Все обязательные документы получены. Отправка не нужна.",
    );
    expect(screen.queryByText("Предпросмотр запроса")).toBeNull();
  });
  it("previews server text, refuses unconsented channels and retries one operation", async () => {
    const user = userEvent.setup();
    render(<DocumentsTab candidateId="candidate-1" stage="new" />);
    await screen.findByLabelText("Получен: Паспорт");
    api.mockResolvedValueOnce({
      title: "Документы",
      body: "Серверный текст",
      channels: [],
    });
    await user.click(screen.getByText("Предпросмотр запроса"));
    await screen.findByText("Серверный текст");
    expect(screen.getByText("Поставить в очередь")).toBeDisabled();
    api.mockResolvedValueOnce({
      title: "Документы",
      body: "Серверный текст",
      channels: ["email"],
    });
    await user.click(screen.getByText("Предпросмотр запроса"));
    await waitFor(() =>
      expect(screen.getByText("Поставить в очередь")).toBeEnabled(),
    );
    api.mockRejectedValueOnce(new ApiError(0, "Сеть недоступна"));
    await user.click(screen.getByText("Поставить в очередь"));
    await screen.findByText("Сеть недоступна");
    api.mockResolvedValueOnce({ messages: [], channels: ["email"] });
    await user.click(screen.getByText("Поставить в очередь"));
    await screen.findByRole("status");
    const calls = api.mock.calls.filter(([path]) =>
      path.endsWith("/messages/send"),
    );
    expect(calls).toHaveLength(2);
    expect(calls[0]).toEqual(calls[1]);
    expect(calls[0][1]?.body).toEqual({
      message_type: "document_request",
      channel: "email",
      document_set_id: "set-1",
      idempotency_key: expect.any(String),
    });
  });
});

describe("Мои правила", () => {
  it("creates a closed stage-transition rule", async () => {
    const user = userEvent.setup();
    render(<MyRulesPage />);
    await screen.findByText(rule.name);
    await user.click(screen.getByText("Создать правило"));
    await user.type(screen.getByLabelText("Название правила"), "Оформление");
    await user.selectOptions(screen.getByLabelText("Список"), "list-1");
    expect(screen.queryByLabelText("Канал")).toBeNull();
    await user.click(screen.getByText("Сохранить правило"));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/document-rules",
        expect.objectContaining({
          method: "POST",
          body: expect.objectContaining({
            name: "Оформление",
            params: expect.objectContaining({
              trigger: "stage_transition",
              action: "apply_list",
              channel: null,
            }),
          }),
        }),
      ),
    );
  });
  it("edits a scheduled reminder with bounded days and channels", async () => {
    const user = userEvent.setup();
    render(<MyRulesPage />);
    await screen.findByText(rule.name);
    await user.click(screen.getByText("Редактировать"));
    await user.selectOptions(
      screen.getByLabelText("Триггер"),
      "scheduled_reminder",
    );
    expect(screen.getByLabelText("Действие")).toHaveValue("document_reminder");
    expect(screen.getByLabelText("Через сколько дней (1–30)")).toHaveAttribute(
      "max",
      "30",
    );
    await user.selectOptions(screen.getByLabelText("Канал"), "telegram");
    await user.click(screen.getByText("Сохранить правило"));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/document-rules/rule-1",
        expect.objectContaining({
          method: "PUT",
          body: expect.objectContaining({
            expected_version: 1,
            params: expect.objectContaining({ channel: "telegram", days: 1 }),
          }),
        }),
      ),
    );
  });
  it("disables without deleting and loads immutable history", async () => {
    const user = userEvent.setup();
    render(<MyRulesPage />);
    await screen.findByText(rule.name);
    await user.click(screen.getByText("Выключить"));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        "/document-rules/rule-1",
        expect.objectContaining({
          method: "PUT",
          body: expect.objectContaining({
            enabled: false,
            expected_version: 1,
          }),
        }),
      ),
    );
    await user.click(screen.getByText("История срабатываний"));
    expect(
      await screen.findByText("Срабатываний в текущей области доступа нет."),
    ).toBeInTheDocument();
  });
  it("shows empty state and a retryable failure", async () => {
    api.mockRejectedValueOnce(new ApiError(500, "Ошибка"));
    render(<MyRulesPage />);
    await screen.findByText("Ошибка");
    api.mockImplementation(
      async (path) => (path === "/document-lists" ? lists : []) as never,
    );
    await userEvent.click(screen.getByText("Повторить загрузку"));
    expect(await screen.findByText("Правил пока нет.")).toBeInTheDocument();
  });
});
