import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  DocumentListDetail,
  DocumentListSummary,
  DocumentListVersion,
} from "../../types";
import { DocumentListsPage } from "./DocumentListsPage";
import { buildItems, slugify } from "./listHelpers";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listDocumentLists: vi.fn(),
    getDocumentList: vi.fn(),
    createDocumentList: vi.fn(),
    updateDocumentList: vi.fn(),
    createDocumentListVersion: vi.fn(),
    updateDocumentListVersionItems: vi.fn(),
    publishDocumentListVersion: vi.fn(),
    archiveDocumentListVersion: vi.fn(),
  };
});

import * as api from "../../api";

const LIST_ID = "11111111-1111-4111-8111-111111111111";

const PUBLISHED: DocumentListVersion = {
  id: "22222222-2222-4222-8222-222222222222",
  list_id: LIST_ID,
  version_number: 1,
  status: "published",
  row_version: 2,
  published_at: "2026-09-01T10:00:00Z",
  archived_at: null,
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  items: [
    {
      id: "aaaaaaaa-0000-4000-8000-000000000001",
      item_key: "passport",
      name: "Паспорт",
      explanation: "",
      is_required: true,
      sort_order: 1,
    },
  ],
};

const DRAFT: DocumentListVersion = {
  id: "33333333-3333-4333-8333-333333333333",
  list_id: LIST_ID,
  version_number: 2,
  status: "draft",
  row_version: 1,
  published_at: null,
  archived_at: null,
  created_at: "2026-09-02T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  items: [
    {
      id: "aaaaaaaa-0000-4000-8000-000000000002",
      item_key: "passport",
      name: "Паспорт",
      explanation: "",
      is_required: true,
      sort_order: 1,
    },
    {
      id: "aaaaaaaa-0000-4000-8000-000000000003",
      item_key: "snils",
      name: "СНИЛС",
      explanation: "",
      is_required: false,
      sort_order: 2,
    },
  ],
};

const SUMMARY: DocumentListSummary = {
  id: LIST_ID,
  name: "Документы для оформления",
  description: "Базовый набор",
  scope_position: null,
  scope_stage: "offer",
  version: 1,
  published_version_id: PUBLISHED.id,
  published_version_number: 1,
  draft_version_id: DRAFT.id,
  versions_count: 2,
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
};

const DETAIL: DocumentListDetail = {
  id: LIST_ID,
  name: SUMMARY.name,
  description: SUMMARY.description,
  scope_position: null,
  scope_stage: "offer",
  version: 1,
  created_at: SUMMARY.created_at,
  updated_at: SUMMARY.updated_at,
  published_version_id: PUBLISHED.id,
  published_version_number: 1,
  versions: [PUBLISHED, DRAFT],
};

function renderPage() {
  return render(
    <ToastProvider>
      <DocumentListsPage />
    </ToastProvider>,
  );
}

async function openList() {
  await userEvent.click(await screen.findByRole("button", { name: /Документы для оформления/ }));
  await screen.findByRole("heading", { name: "Документы для оформления" });
}

describe("DocumentListsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getDocumentList).mockResolvedValue(DETAIL);
  });

  it("shows loading, then the empty state", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [], total: 0 });
    renderPage();
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
    expect(await screen.findByText("Списков документов пока нет")).toBeInTheDocument();
  });

  it("shows the error state and retries", async () => {
    vi.mocked(api.listDocumentLists)
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValueOnce({ items: [SUMMARY], total: 1 });
    renderPage();
    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Повторить попытку/ }));
    expect(await screen.findByRole("button", { name: /Документы для оформления/ })).toBeInTheDocument();
  });

  it("shows the permission state on 403 (non-admin)", async () => {
    vi.mocked(api.listDocumentLists).mockRejectedValue(new api.ApiError(403, "Только администратор."));
    renderPage();
    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
  });

  it("shows versions with statuses; published items are not editable", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [SUMMARY], total: 1 });
    renderPage();
    await openList();
    expect(screen.getByText("Опубликована")).toBeInTheDocument();
    expect(screen.getByText("Черновик")).toBeInTheDocument();
    // Only the draft offers editing/publishing; the published one only archiving.
    expect(screen.getAllByRole("button", { name: "Редактировать" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Опубликовать" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Снять с публикации" })).toBeInTheDocument();
    // A draft already exists — no second draft may be started.
    expect(screen.getByRole("button", { name: "Новый черновик" })).toBeDisabled();
  });

  it("creates a list with items as a draft", async () => {
    vi.mocked(api.listDocumentLists)
      .mockResolvedValueOnce({ items: [], total: 0 })
      .mockResolvedValueOnce({ items: [SUMMARY], total: 1 });
    vi.mocked(api.createDocumentList).mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByText("Списков документов пока нет");
    await userEvent.click(screen.getByRole("button", { name: "Новый список" }));
    const form = screen.getByRole("form", { name: "Новый список документов" });
    await userEvent.type(within(form).getByLabelText(/^Название/), "Документы для оформления");
    await userEvent.selectOptions(within(form).getByLabelText(/Этап/), "offer");
    await userEvent.type(within(form).getByLabelText(/Документ 1/), "Паспорт");
    await userEvent.click(within(form).getByRole("button", { name: "Добавить документ" }));
    await userEvent.type(within(form).getByLabelText(/Документ 2/), "СНИЛС");
    await userEvent.click(within(form).getByRole("button", { name: "Создать черновик" }));
    await waitFor(() => expect(api.createDocumentList).toHaveBeenCalledTimes(1));
    expect(api.createDocumentList).toHaveBeenCalledWith({
      name: "Документы для оформления",
      description: "",
      scope_position: null,
      scope_stage: "offer",
      items: [
        { item_key: "pasport", name: "Паспорт", explanation: "", is_required: true },
        { item_key: "snils", name: "СНИЛС", explanation: "", is_required: true },
      ],
    });
    expect(await screen.findByText(/Список создан как черновик/)).toBeInTheDocument();
  });

  it("rejects duplicate item keys before calling the API", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [], total: 0 });
    renderPage();
    await screen.findByText("Списков документов пока нет");
    await userEvent.click(screen.getByRole("button", { name: "Новый список" }));
    const form = screen.getByRole("form", { name: "Новый список документов" });
    await userEvent.type(within(form).getByLabelText(/^Название/), "Список");
    await userEvent.type(within(form).getByLabelText(/Документ 1/), "Паспорт");
    await userEvent.click(within(form).getByRole("button", { name: "Добавить документ" }));
    await userEvent.type(within(form).getByLabelText(/Документ 2/), "Паспорт");
    await userEvent.click(within(form).getByRole("button", { name: "Создать черновик" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже используется");
    expect(api.createDocumentList).not.toHaveBeenCalled();
  });

  it("saves draft items with the expected row version", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [SUMMARY], total: 1 });
    vi.mocked(api.updateDocumentListVersionItems).mockResolvedValue({ ...DRAFT, row_version: 2 });
    renderPage();
    await openList();
    await userEvent.click(screen.getByRole("button", { name: "Редактировать" }));
    const form = screen.getByRole("form", { name: "Черновик версии 2" });
    await userEvent.click(within(form).getByRole("button", { name: "Убрать документ 2" }));
    await userEvent.click(within(form).getByRole("button", { name: "Сохранить черновик" }));
    await waitFor(() =>
      expect(api.updateDocumentListVersionItems).toHaveBeenCalledWith(LIST_ID, DRAFT.id, {
        expected_row_version: 1,
        items: [{ item_key: "passport", name: "Паспорт", explanation: "", is_required: true }],
      }),
    );
  });

  it("publishes a draft after confirmation with its row version", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [SUMMARY], total: 1 });
    vi.mocked(api.publishDocumentListVersion).mockResolvedValue({ ...DRAFT, status: "published" });
    renderPage();
    await openList();
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать" }));
    const dialog = await screen.findByRole("dialog", { name: "Опубликовать версию 2?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Опубликовать" }));
    await waitFor(() =>
      expect(api.publishDocumentListVersion).toHaveBeenCalledWith(LIST_ID, DRAFT.id, 1),
    );
    expect(await screen.findByText("Версия 2 опубликована.")).toBeInTheDocument();
  });

  it("shows a conflict on 409 when publishing a stale version", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [SUMMARY], total: 1 });
    vi.mocked(api.publishDocumentListVersion).mockRejectedValue(
      new api.ApiError(409, "Версия была изменена — обновите страницу."),
    );
    renderPage();
    await openList();
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать" }));
    const dialog = await screen.findByRole("dialog", { name: "Опубликовать версию 2?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Опубликовать" }));
    expect(await screen.findByText("Данные устарели")).toBeInTheDocument();
    expect(screen.getByText("Версия была изменена — обновите страницу.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => expect(api.getDocumentList).toHaveBeenCalledTimes(2));
  });

  it("archives the published version via a destructive confirmation", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [SUMMARY], total: 1 });
    vi.mocked(api.archiveDocumentListVersion).mockResolvedValue({ ...PUBLISHED, status: "archived" });
    renderPage();
    await openList();
    await userEvent.click(screen.getByRole("button", { name: "Снять с публикации" }));
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "В архив" }));
    await waitFor(() =>
      expect(api.archiveDocumentListVersion).toHaveBeenCalledWith(LIST_ID, PUBLISHED.id, 2),
    );
  });

  it("starts a new draft when none exists", async () => {
    const noDraft: DocumentListDetail = { ...DETAIL, versions: [PUBLISHED] };
    vi.mocked(api.listDocumentLists).mockResolvedValue({
      items: [{ ...SUMMARY, draft_version_id: null, versions_count: 1 }],
      total: 1,
    });
    vi.mocked(api.getDocumentList).mockResolvedValueOnce(noDraft).mockResolvedValue(DETAIL);
    vi.mocked(api.createDocumentListVersion).mockResolvedValue(DRAFT);
    renderPage();
    await openList();
    await userEvent.click(screen.getByRole("button", { name: "Новый черновик" }));
    await waitFor(() => expect(api.createDocumentListVersion).toHaveBeenCalledWith(LIST_ID));
    expect(await screen.findByText("Черновик")).toBeInTheDocument();
  });

  it("edits the list header with the optimistic version", async () => {
    vi.mocked(api.listDocumentLists).mockResolvedValue({ items: [SUMMARY], total: 1 });
    vi.mocked(api.updateDocumentList).mockResolvedValue({ ...DETAIL, version: 2 });
    renderPage();
    await openList();
    await userEvent.click(screen.getByRole("button", { name: "Изменить описание" }));
    const form = screen.getByRole("form", { name: "Описание списка" });
    const name = within(form).getByLabelText(/^Название/);
    await userEvent.clear(name);
    await userEvent.type(name, "Новое название");
    await userEvent.click(within(form).getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(api.updateDocumentList).toHaveBeenCalledWith(
        LIST_ID,
        expect.objectContaining({ expected_version: 1, name: "Новое название" }),
      ),
    );
  });
});

describe("list helpers", () => {
  it("derives ASCII keys and validates items", () => {
    expect(slugify("Паспорт РФ")).toBe("pasport-rf");
    expect(slugify("  Диплом (копия) ")).toBe("diplom-kopiya");
    const tooMany = buildItems(
      Array.from({ length: 21 }, (_, i) => ({
        item_key: `k${i}`,
        name: `Документ ${i}`,
        explanation: "",
        is_required: true,
      })),
    );
    expect("error" in tooMany && tooMany.error).toMatch(/Не больше 20/);
    const badKey = buildItems([
      { item_key: "Паспорт", name: "Паспорт", explanation: "", is_required: true },
    ]);
    expect("error" in badKey && badKey.error).toMatch(/ключ/);
  });
});
