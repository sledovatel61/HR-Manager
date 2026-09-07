import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, User } from "../../types";
import CandidatesListPage from "./CandidatesListPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listCandidates: vi.fn(),
    listHrUsers: vi.fn(),
    deleteCandidate: vi.fn(),
    restoreCandidate: vi.fn(),
  };
});

import * as api from "../../api";

const HR: User = {
  id: "22222222-2222-2222-2222-222222222222",
  username: "hr1",
  full_name: "HR Один",
  role: "hr",
  is_active: true,
  locked_until: null,
  last_login_at: null,
  created_at: "2026-09-01T10:00:00Z",
};

const MANAGER: User = { ...HR, id: "33333333-3333-3333-3333-333333333333", username: "mgr", full_name: "Менеджер", role: "manager" };

function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    full_name: "Петров Пётр Петрович",
    phone: "+7 900 123-45-67",
    email: "petrov@example.com",
    source: "site",
    position: "Инженер",
    owner_user_id: HR.id,
    owner_username: "hr1",
    stage: "new",
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
    deleted_at: null,
    deleted_by_user_id: null,
    is_deleted: false,
    ...overrides,
  };
}

function renderPage(mode: "queue" | "all" | "deleted" = "queue", user: User = HR) {
  return render(
    <ToastProvider>
      <CandidatesListPage user={user} mode={mode} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
});

describe("CandidatesListPage", () => {
  it("shows a skeleton while loading and then renders rows", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderPage();

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Инженер")).toBeInTheDocument();
    expect(within(table).getByText("Новый")).toBeInTheDocument();
    expect(within(table).getByText("Сайт компании")).toBeInTheDocument();
  });

  it("shows the empty state when there are no candidates", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    renderPage();
    expect(await screen.findByText("Кандидаты не найдены")).toBeInTheDocument();
  });

  it("shows an error with retry when the request fails", async () => {
    vi.mocked(api.listCandidates).mockRejectedValueOnce(new api.ApiError(500, "Сбой сервера"));
    renderPage();

    const retry = await screen.findByRole("button", { name: /повторить/i });
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [candidate()], total: 1, limit: 20, offset: 0 });
    await userEvent.click(retry);

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
  });

  it("debounces search and passes query/stage/source to the API", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    const user = userEvent.setup();
    renderPage();

    const search = screen.getByLabelText("Поиск кандидатов");
    await user.type(search, "петров");

    await waitFor(() => {
      const lastCall = vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ query: "петров" });
    });

    await user.selectOptions(screen.getByLabelText("Этап"), "offer");
    await waitFor(() => {
      const lastCall = vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ stage: "offer" });
    });

    await user.selectOptions(screen.getByLabelText("Источник"), "referral");
    await waitFor(() => {
      const lastCall = vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ source: "referral" });
    });
  });

  it("paginates server-side with next/prev", async () => {
    vi.mocked(api.listCandidates)
      .mockResolvedValueOnce({
        items: Array.from({ length: 20 }, (_, i) => candidate({ id: `id-${i}`, full_name: `Кандидат ${i}` })),
        total: 25,
        limit: 20,
        offset: 0,
      })
      .mockResolvedValueOnce({
        items: Array.from({ length: 5 }, (_, i) => candidate({ id: `id-${20 + i}`, full_name: `Кандидат ${20 + i}` })),
        total: 25,
        limit: 20,
        offset: 20,
      });
    renderPage();

    expect(await screen.findByText("Кандидат 0")).toBeInTheDocument();
    expect(screen.getByText("1–20 из 25")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    expect(await screen.findByText("Кандидат 20")).toBeInTheDocument();

    expect(vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0]).toMatchObject({ offset: 20 });
  });

  it("hides the owner filter for HR and shows it for managers", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    const { unmount } = renderPage("queue", HR);
    expect(screen.queryByLabelText("Ответственный")).not.toBeInTheDocument();
    unmount();

    vi.mocked(api.listHrUsers).mockResolvedValue({
      items: [{ id: HR.id, username: "hr1", full_name: "HR Один", role: "hr", is_active: true }],
      total: 1,
    });
    renderPage("all", MANAGER);
    expect(await screen.findByLabelText("Ответственный")).toBeInTheDocument();
  });

  it("scopes the deleted view with include_deleted and restores rows", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate({ is_deleted: true, deleted_at: "2026-09-02T11:00:00Z", full_name: "Удалённый кандидат" })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.restoreCandidate).mockResolvedValue(candidate());
    renderPage("deleted");

    expect(await screen.findByText("Удалённый кандидат")).toBeInTheDocument();
    expect(vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0]).toMatchObject({
      include_deleted: true,
    });

    await userEvent.click(screen.getByRole("button", { name: "Восстановить" }));
    await waitFor(() => expect(api.restoreCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444"));
  });

  it("confirms soft delete before calling the API", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.deleteCandidate).mockResolvedValue(candidate());
    renderPage();

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Удалить кандидата Петров Пётр Петрович" }));

    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));

    await waitFor(() =>
      expect(api.deleteCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444")
    );
  });
});
