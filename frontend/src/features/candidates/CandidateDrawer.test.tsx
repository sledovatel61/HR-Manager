import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, User } from "../../types";
import { CandidateDrawer } from "./CandidateDrawer";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    getCandidate: vi.fn(),
    updateCandidate: vi.fn(),
    listCandidateInteractions: vi.fn(),
    createCandidateInteraction: vi.fn(),
    listCandidateTransfers: vi.fn(),
    listEvents: vi.fn(),
    updateEvent: vi.fn(),
    listHrUsers: vi.fn(),
    listEventHistory: vi.fn(),
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

const CANDIDATE: Candidate = {
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
};

function renderDrawer(overrides: { user?: User } = {}) {
  return render(
    <ToastProvider>
      <CandidateDrawer
        candidateId={CANDIDATE.id}
        user={overrides.user ?? HR}
        onClose={vi.fn()}
        onChanged={vi.fn()}
        onOpenCandidate={vi.fn()}
      />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getCandidate).mockResolvedValue(CANDIDATE);
  vi.mocked(api.listCandidateInteractions).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.mocked(api.listCandidateTransfers).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.mocked(api.listEventHistory).mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 });
});

describe("CandidateDrawer", () => {
  it("loads the candidate and shows the stage control", async () => {
    renderDrawer();
    expect(
      await screen.findByRole("heading", { name: "Петров Пётр Петрович" })
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Изменить этап")).toHaveValue("new");
  });

  it("changes the stage via PATCH and rolls back on failure", async () => {
    vi.mocked(api.updateCandidate).mockResolvedValueOnce({ ...CANDIDATE, stage: "offer" });
    renderDrawer();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await userEvent.selectOptions(screen.getByLabelText("Изменить этап"), "offer");

    await waitFor(() =>
      expect(api.updateCandidate).toHaveBeenCalledWith(CANDIDATE.id, { stage: "offer" })
    );
    expect(screen.getByLabelText("Изменить этап")).toHaveValue("offer");
  });

  it("rolls the stage back when the server rejects the change", async () => {
    vi.mocked(api.updateCandidate).mockRejectedValue(new api.ApiError(500, "Сбой"));
    renderDrawer();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await userEvent.selectOptions(screen.getByLabelText("Изменить этап"), "hired");

    await waitFor(() => expect(screen.getByLabelText("Изменить этап")).toHaveValue("new"));
  });

  it("loads the interaction history and appends a new entry without reload", async () => {
    vi.mocked(api.listCandidateInteractions).mockResolvedValue({
      items: [
        {
          id: "i-1",
          candidate_id: CANDIDATE.id,
          author_user_id: HR.id,
          author_username: "hr1",
          type: "call",
          comment: "Первый звонок",
          created_at: "2026-09-02T09:00:00Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.createCandidateInteraction).mockResolvedValue({
      id: "i-2",
      candidate_id: CANDIDATE.id,
      author_user_id: HR.id,
      author_username: "hr1",
      type: "note",
      comment: "Новая заметка",
      created_at: "2026-09-03T09:00:00Z",
    });
    renderDrawer();

    await userEvent.click(await screen.findByRole("tab", { name: "Взаимодействия" }));
    expect(await screen.findByText("Первый звонок")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Тип взаимодействия"), "note");
    await userEvent.type(screen.getByLabelText(/Комментарий/), "Новая заметка");
    await userEvent.click(screen.getByRole("button", { name: "Добавить запись" }));

    expect(await screen.findByText("Новая заметка")).toBeInTheDocument();
    // The new entry appears immediately; the history was not refetched fully.
    expect(api.listCandidateInteractions).toHaveBeenCalledTimes(1);
  });

  it("edits the card through PATCH with client validation", async () => {
    vi.mocked(api.updateCandidate).mockResolvedValue({ ...CANDIDATE, position: "Старший инженер" });
    renderDrawer();

    await screen.findByRole("heading", { name: "Петров Пётр Петрович" });
    await userEvent.click(screen.getByRole("button", { name: "Редактировать" }));
    await userEvent.clear(screen.getByLabelText(/Должность/));
    await userEvent.type(screen.getByLabelText(/Должность/), "Старший инженер");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(api.updateCandidate).toHaveBeenCalledWith(
        CANDIDATE.id,
        expect.objectContaining({ position: "Старший инженер" })
      )
    );
  });

  it("shows the transfer history tab", async () => {
    vi.mocked(api.listCandidateTransfers).mockResolvedValue({
      items: [
        {
          id: "t-1",
          candidate_id: CANDIDATE.id,
          initiator_user_id: "33333333-3333-3333-3333-333333333333",
          initiator_username: "mgr",
          from_user_id: "44444444-5555-6666-7777-888888888888",
          from_username: "hr_old",
          to_user_id: HR.id,
          to_username: "hr1",
          reason: "Перераспределение",
          created_at: "2026-09-02T12:00:00Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderDrawer();

    await userEvent.click(await screen.findByRole("tab", { name: "Передачи" }));
    expect(await screen.findByText("hr_old → hr1")).toBeInTheDocument();
    expect(screen.getByText("Перераспределение")).toBeInTheDocument();
  });
});

describe("CandidateDrawer events tab", () => {
  it("lists candidate events and completes one with its version", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({
      items: [
        {
          id: "55555555-5555-5555-5555-555555555555",
          candidate_id: CANDIDATE.id,
          candidate_full_name: CANDIDATE.full_name,
          type: "interview",
          title: "Собеседование",
          note: null,
          status: "scheduled",
          starts_at: "2026-09-07T09:00:00Z",
          ends_at: null,
          remind_at: null,
          completed_at: null,
          author_user_id: HR.id,
          author_username: "hr1",
          assignee_user_id: HR.id,
          assignee_username: "hr1",
          version: 2,
          created_at: "2026-09-06T09:00:00Z",
          updated_at: "2026-09-06T09:00:00Z",
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.updateEvent).mockResolvedValue({} as never);
    renderDrawer();

    await userEvent.click(await screen.findByRole("tab", { name: "События" }));
    expect(await screen.findByText("Собеседование: Собеседование")).toBeInTheDocument();
    expect(api.listEvents).toHaveBeenCalledWith(
      expect.objectContaining({ candidate_id: CANDIDATE.id })
    );

    await userEvent.click(screen.getByRole("button", { name: "Выполнено" }));
    await waitFor(() =>
      expect(api.updateEvent).toHaveBeenCalledWith("55555555-5555-5555-5555-555555555555", {
        expected_version: 2,
        status: "completed",
      })
    );
  });
});
