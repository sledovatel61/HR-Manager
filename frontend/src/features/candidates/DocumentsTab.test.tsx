import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  Candidate,
  CandidateDocumentAssignment,
  CandidateDocuments,
  PublishedDocumentList,
} from "../../types";
import { DocumentsTab } from "./DocumentsTab";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    getCandidateDocuments: vi.fn(),
    listPublishedDocumentLists: vi.fn(),
    applyCandidateDocumentList: vi.fn(),
    updateCandidateDocumentItem: vi.fn(),
    sendCandidateDocumentMessage: vi.fn(),
  };
});

import * as api from "../../api";

const CANDIDATE: Candidate = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  full_name: "Тестовый кандидат",
  phone: null,
  email: null,
  source: "referral",
  position: "Разработчик",
  owner_user_id: "33333333-3333-4333-8333-333333333333",
  owner_username: "hr1",
  stage: "offer",
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

const LIST: PublishedDocumentList = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Документы для оформления",
  description: "",
  scope_position: null,
  scope_stage: null,
  published_version_id: "22222222-2222-4222-8222-222222222222",
  published_version_number: 2,
  items: [],
};

const ASSIGNMENT: CandidateDocumentAssignment = {
  id: "44444444-4444-4444-8444-444444444444",
  candidate_id: CANDIDATE.id,
  list_id: LIST.id,
  list_name: LIST.name,
  version_id: LIST.published_version_id,
  version_number: 2,
  assigned_at: "2026-09-02T10:00:00Z",
  assigned_by_user_id: "33333333-3333-4333-8333-333333333333",
  assigned_by_username: "hr1",
  assigned_by_rule_id: null,
  items: [
    {
      id: "55555555-5555-4555-8555-555555555555",
      item_key: "passport",
      name: "Паспорт",
      explanation: "Все заполненные страницы",
      is_required: true,
      sort_order: 1,
      status: "missing",
      version: 1,
      changed_by_user_id: null,
      changed_by_username: null,
      changed_at: null,
    },
    {
      id: "66666666-6666-4666-8666-666666666666",
      item_key: "diploma",
      name: "Диплом",
      explanation: "",
      is_required: false,
      sort_order: 2,
      status: "received",
      version: 2,
      changed_by_user_id: "33333333-3333-4333-8333-333333333333",
      changed_by_username: "hr1",
      changed_at: "2026-09-03T10:00:00Z",
    },
  ],
  missing_required_count: 1,
  received_count: 1,
};

const WITH_LIST: CandidateDocuments = { current: ASSIGNMENT, history: [] };
const EMPTY: CandidateDocuments = { current: null, history: [] };

function renderTab(candidate: Candidate = CANDIDATE) {
  return render(
    <ToastProvider>
      <DocumentsTab candidate={candidate} />
    </ToastProvider>,
  );
}

describe("DocumentsTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listPublishedDocumentLists).mockResolvedValue({ items: [LIST] });
  });

  it("shows the empty state when no list is applied and applies one", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(EMPTY);
    vi.mocked(api.applyCandidateDocumentList).mockResolvedValue(WITH_LIST);
    renderTab();
    expect(await screen.findByText("Список документов не применён")).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Применить" });
    expect(apply).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Применить список"), LIST.id);
    await userEvent.click(apply);
    await waitFor(() =>
      expect(api.applyCandidateDocumentList).toHaveBeenCalledWith(CANDIDATE.id, LIST.id, false),
    );
    expect(await screen.findByText(/Документы для оформления · версия 2/)).toBeInTheDocument();
  });

  it("shows the error state and retries", async () => {
    vi.mocked(api.getCandidateDocuments)
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce(WITH_LIST);
    renderTab();
    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Повторить попытку/ }));
    expect(await screen.findByRole("heading", { name: /версия 2/ })).toBeInTheDocument();
  });

  it("renders the snapshot with statuses and who/when marked", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(WITH_LIST);
    renderTab();
    expect(await screen.findByText("Не хватает обязательных: 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Паспорт: не получен")).not.toBeChecked();
    expect(screen.getByLabelText("Диплом: получен")).toBeChecked();
    expect(screen.getByText(/отмечено .*hr1/)).toBeInTheDocument();
    expect(screen.getByText("Все заполненные страницы")).toBeInTheDocument();
  });

  it("marks an item received with the optimistic version", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(WITH_LIST);
    vi.mocked(api.updateCandidateDocumentItem).mockResolvedValue({
      current: {
        ...ASSIGNMENT,
        missing_required_count: 0,
        items: ASSIGNMENT.items.map((item) =>
          item.item_key === "passport" ? { ...item, status: "received", version: 2 } : item,
        ),
      },
      history: [],
    });
    renderTab();
    await userEvent.click(await screen.findByLabelText("Паспорт: не получен"));
    await waitFor(() =>
      expect(api.updateCandidateDocumentItem).toHaveBeenCalledWith(
        CANDIDATE.id,
        "55555555-5555-4555-8555-555555555555",
        { status: "received", expected_version: 1 },
      ),
    );
    expect(await screen.findByText("Все обязательные получены")).toBeInTheDocument();
  });

  it("shows a version conflict on 409 with a reload action", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(WITH_LIST);
    vi.mocked(api.updateCandidateDocumentItem).mockRejectedValue(
      new api.ApiError(409, "Отметка уже изменена другим пользователем."),
    );
    renderTab();
    await userEvent.click(await screen.findByLabelText("Паспорт: не получен"));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Отметка уже изменена другим пользователем.");
    await userEvent.click(within(alert).getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(api.getCandidateDocuments).toHaveBeenCalledTimes(2));
  });

  it("queues a document request with a client idempotency key and no text", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(WITH_LIST);
    vi.mocked(api.sendCandidateDocumentMessage).mockResolvedValue({
      messages: [],
      channels: ["email"],
    });
    renderTab();
    await screen.findByRole("heading", { name: /версия 2/ });
    await userEvent.selectOptions(screen.getByLabelText("Канал"), "email");
    await userEvent.click(screen.getByRole("button", { name: "Запросить документы" }));
    await waitFor(() => expect(api.sendCandidateDocumentMessage).toHaveBeenCalledTimes(1));
    const [candidateId, payload] = vi.mocked(api.sendCandidateDocumentMessage).mock.calls[0];
    expect(candidateId).toBe(CANDIDATE.id);
    expect(payload.message_type).toBe("document_request");
    expect(payload.channel).toBe("email");
    expect(payload.idempotency_key.length).toBeGreaterThanOrEqual(8);
    expect(Object.keys(payload).sort()).toEqual(["channel", "idempotency_key", "message_type"]);
    expect(await screen.findByText(/Запрос поставлен в очередь/)).toBeInTheDocument();
  });

  it("disables sending when nothing is missing", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue({
      current: {
        ...ASSIGNMENT,
        missing_required_count: 0,
        items: ASSIGNMENT.items.map((item) => ({ ...item, status: "received" })),
      },
      history: [],
    });
    renderTab();
    await screen.findByRole("heading", { name: /версия 2/ });
    expect(screen.getByRole("button", { name: "Запросить документы" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Напомнить" })).toBeDisabled();
    expect(screen.getByText("Все документы получены — отправлять нечего.")).toBeInTheDocument();
  });

  it("asks for confirmation before replacing an applied list", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(WITH_LIST);
    vi.mocked(api.applyCandidateDocumentList).mockResolvedValue(WITH_LIST);
    renderTab();
    await screen.findByRole("heading", { name: /версия 2/ });
    await userEvent.selectOptions(screen.getByLabelText("Заменить список"), LIST.id);
    await userEvent.click(screen.getByRole("button", { name: "Заменить" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Заменить" }));
    await waitFor(() =>
      expect(api.applyCandidateDocumentList).toHaveBeenCalledWith(CANDIDATE.id, LIST.id, true),
    );
  });

  it("is read-only for a soft-deleted candidate", async () => {
    vi.mocked(api.getCandidateDocuments).mockResolvedValue(WITH_LIST);
    renderTab({ ...CANDIDATE, is_deleted: true, deleted_at: "2026-09-05T10:00:00Z" });
    await screen.findByRole("heading", { name: /версия 2/ });
    expect(screen.getByLabelText("Паспорт: не получен")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Запросить документы" })).toBeNull();
    expect(screen.queryByLabelText("Заменить список")).toBeNull();
  });
});
