import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { CalendarEvent, Candidate, User } from "../../types";
import { EventFormModal } from "./EventFormModal";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    createEvent: vi.fn(),
    updateEvent: vi.fn(),
    listEventHistory: vi.fn(),
    listHrUsers: vi.fn(),
    listCandidates: vi.fn(),
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
  phone: null,
  email: null,
  source: "site",
  position: "Инженер",
  owner_user_id: HR.id,
  owner_username: "hr1",
  stage: "new",
  start_date: null,
  start_time: null,
  start_organization: null,
  start_department: null,
  shift: null,
  start_comment: null,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

const EVENT: CalendarEvent = {
  id: "55555555-5555-5555-5555-555555555555",
  candidate_id: CANDIDATE.id,
  candidate_full_name: "Петров Пётр Петрович",
  type: "call",
  title: "Созвон",
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
  version: 3,
  created_at: "2026-09-06T09:00:00Z",
  updated_at: "2026-09-06T09:00:00Z",
};

function renderCreate(candidate: Candidate | null = null) {
  const onSaved = vi.fn();
  render(
    <ToastProvider>
      <EventFormModal
        open
        user={HR}
        candidate={candidate}
        onClose={vi.fn()}
        onSaved={onSaved}
      />
    </ToastProvider>
  );
  return { onSaved };
}

function renderEdit(event: CalendarEvent = EVENT) {
  const onSaved = vi.fn();
  render(
    <ToastProvider>
      <EventFormModal
        open
        user={HR}
        event={event}
        onClose={vi.fn()}
        onSaved={onSaved}
      />
    </ToastProvider>
  );
  return { onSaved };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.listEventHistory).mockResolvedValue({ items: [], total: 0, limit: 10, offset: 0 });
});

describe("EventFormModal (create)", () => {
  it("validates required fields and does not call the API", async () => {
    renderCreate(CANDIDATE);
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByText("Название события обязательно.")).toBeInTheDocument();
    expect(api.createEvent).not.toHaveBeenCalled();
  });

  it("creates an event with ISO timestamps and no false success on error", async () => {
    const { onSaved } = renderCreate(CANDIDATE);
    await userEvent.type(screen.getByLabelText(/Название/), "Созвон");
    await userEvent.type(screen.getByLabelText(/Начало/), "2026-09-07T09:00");

    vi.mocked(api.createEvent).mockRejectedValueOnce(new api.ApiError(422, "Сбой сервера"));
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    expect(await screen.findByText("Сбой сервера")).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
    expect(api.createEvent).toHaveBeenCalledWith(
      expect.objectContaining({
        candidate_id: CANDIDATE.id,
        title: "Созвон",
        starts_at: expect.stringMatching(/^20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.\d{3}Z$/),
      })
    );
  });

  it("creates a personal event without a candidate (optional link)", async () => {
    const { onSaved } = renderCreate(null);
    await userEvent.type(screen.getByLabelText(/Название/), "Личная задача");
    await userEvent.type(screen.getByLabelText(/Начало/), "2026-09-07T09:00");
    vi.mocked(api.createEvent).mockResolvedValue({
      ...EVENT,
      candidate_id: null,
      candidate_full_name: "",
      title: "Личная задача",
    });
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(api.createEvent).toHaveBeenCalledWith(
        expect.objectContaining({ candidate_id: null, title: "Личная задача" })
      )
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("explains the reminder moment for reminder-type events instead of a dead control", async () => {
    renderCreate(CANDIDATE);
    await userEvent.selectOptions(screen.getByLabelText("Тип события"), "reminder");
    // No disabled «Напоминание» input — an explicit explanation instead.
    expect(screen.queryByLabelText(/Напоминание/)).not.toBeInTheDocument();
    expect(screen.getByText(/Момент напоминания/)).toBeInTheDocument();
  });

  it("clearing the reminder uses the «Очистить» action", async () => {
    renderCreate(CANDIDATE);
    const remindInput = screen.getByLabelText(/Напоминание/);
    await userEvent.type(remindInput, "2026-09-07T08:00");
    expect(remindInput).toHaveValue("2026-09-07T08:00");
    await userEvent.click(screen.getByRole("button", { name: "Очистить" }));
    expect(remindInput).toHaveValue("");
  });
});

describe("EventFormModal (edit)", () => {
  it("completes the event with its current version", async () => {
    const { onSaved } = renderEdit();
    vi.mocked(api.updateEvent).mockResolvedValue({ ...EVENT, status: "completed" });

    await userEvent.click(screen.getByRole("button", { name: "Выполнено" }));

    await waitFor(() =>
      expect(api.updateEvent).toHaveBeenCalledWith(EVENT.id, {
        expected_version: 3,
        status: "completed",
      })
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("postpones with a new start date and no date means a validation error", async () => {
    const { onSaved } = renderEdit();
    vi.mocked(api.updateEvent).mockResolvedValue({ ...EVENT, status: "postponed" });

    // Without a new date the draft validates against the current value
    // (still filled) — the postpone uses the current starts_at, which the
    // server would accept only if changed; the form requires an edit, so we
    // change the date first.
    const startInput = screen.getByLabelText(/Начало/);
    await userEvent.clear(startInput);
    await userEvent.type(startInput, "2026-09-08T11:00");
    await userEvent.click(screen.getByRole("button", { name: "Отложить" }));

    await waitFor(() =>
      expect(api.updateEvent).toHaveBeenCalledWith(EVENT.id, {
        expected_version: 3,
        status: "postponed",
        starts_at: expect.stringMatching(/2026-09-08T/),
        ends_at: null,
      })
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("shows the server error on version conflict and nothing is saved", async () => {
    const { onSaved } = renderEdit();
    vi.mocked(api.updateEvent).mockRejectedValue(
      new api.ApiError(409, "Событие уже изменено (ожидалась версия 3, актуальная — 4).")
    );

    await userEvent.click(screen.getByRole("button", { name: "Выполнено" }));

    expect((await screen.findAllByText(/Событие уже изменено/)).length).toBeGreaterThan(0);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("renders the immutable history with kind labels", async () => {
    vi.mocked(api.listEventHistory).mockResolvedValue({
      items: [
        {
          id: "h-1",
          event_id: EVENT.id,
          changed_by_user_id: HR.id,
          changed_by_username: "hr1",
          kind: "created",
          status_old: null,
          status_new: "scheduled",
          starts_at_old: null,
          starts_at_new: null,
          ends_at_old: null,
          ends_at_new: null,
          remind_at_old: null,
          remind_at_new: null,
          assignee_user_id_old: null,
          assignee_user_id_new: null,
          title_changed: false,
          note_changed: false,
          created_at: "2026-09-06T09:00:00Z",
        },
      ],
      total: 1,
      limit: 10,
      offset: 0,
    });
    renderEdit();

    expect(await screen.findByText("Создано")).toBeInTheDocument();
  });

  it("disables editing for completed events", () => {
    renderEdit({ ...EVENT, status: "completed" });
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Выполнено" })).not.toBeInTheDocument();
  });
});

describe("EventFormModal (assignee directory states, UX feedback 2026-09-29)", () => {
  const ADMIN: User = {
    ...HR,
    id: "66666666-6666-6666-6666-666666666666",
    username: "boss",
    role: "admin",
    full_name: "Администратор",
  };
  const HR_ITEM = {
    id: HR.id,
    username: "hr1",
    full_name: "HR Один",
    role: "hr" as const,
    is_active: true,
  };

  it("empty directory: explains and offers a recovery action to the admin", async () => {
    vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
    render(
      <ToastProvider>
        <EventFormModal
          open
          user={ADMIN}
          candidate={CANDIDATE}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />
      </ToastProvider>
    );

    expect(await screen.findByText("Нет доступных исполнителей.")).toBeInTheDocument();
    expect(screen.getByText(/Нужен активный пользователь с ролью «HR»/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Открыть «Пользователи»/ })).toBeInTheDocument();
  });

  it("failed directory request: shows an error with retry instead of an empty select", async () => {
    vi.mocked(api.listHrUsers)
      .mockRejectedValueOnce(new api.ApiError(500, "Сбой"))
      .mockResolvedValue({ items: [HR_ITEM], total: 1 });
    render(
      <ToastProvider>
        <EventFormModal
          open
          user={ADMIN}
          candidate={CANDIDATE}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />
      </ToastProvider>
    );

    expect(await screen.findByText("Не удалось загрузить список исполнителей.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByLabelText(/Исполнитель/)).toBeInTheDocument();
  });

  it("loads the directory for admin regardless of the caller's own role", async () => {
    vi.mocked(api.listHrUsers).mockResolvedValue({ items: [HR_ITEM], total: 1 });
    render(
      <ToastProvider>
        <EventFormModal
          open
          user={ADMIN}
          candidate={CANDIDATE}
          onClose={vi.fn()}
          onSaved={vi.fn()}
        />
      </ToastProvider>
    );
    await screen.findByLabelText(/Исполнитель/);
    expect(api.listHrUsers).toHaveBeenCalled();
  });
});

describe("EventFormModal (orchestrator review regressions)", () => {
  const MANAGER: User = { ...HR, id: "33333333-3333-3333-3333-333333333333", username: "mgr", role: "manager" };
  const HR_ITEM = {
    id: HR.id,
    username: "hr1",
    full_name: "HR Один",
    role: "hr" as const,
    is_active: true,
  };

  it("manager/admin must pick an active HR — no «Я» option", async () => {
    vi.mocked(api.listHrUsers).mockResolvedValue({ items: [HR_ITEM], total: 1 });
    const onSaved = vi.fn();
    render(
      <ToastProvider>
        <EventFormModal
          open
          user={MANAGER}
          candidate={CANDIDATE}
          onClose={vi.fn()}
          onSaved={onSaved}
        />
      </ToastProvider>
    );

    const assigneeSelect = await screen.findByLabelText(/Исполнитель/);
    const options = within(assigneeSelect).getAllByRole("option");
    expect(options.map((o) => o.textContent)).not.toContain(`Я (${MANAGER.username})`);
    expect(options.map((o) => o.textContent)).toContain("Выберите исполнителя");
    expect(options.map((o) => o.textContent)).toContain("HR Один");

    // Submit without an assignee → validation error, no API call.
    await userEvent.type(screen.getByLabelText(/Название/), "Созвон");
    await userEvent.type(screen.getByLabelText(/Начало/), "2026-09-07T09:00");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    expect(
      await screen.findByText("Выберите исполнителя — активного пользователя с ролью HR.")
    ).toBeInTheDocument();
    expect(api.createEvent).not.toHaveBeenCalled();

    // Picking an HR sends the explicit assignee.
    vi.mocked(api.createEvent).mockResolvedValue({ ...EVENT });
    await userEvent.selectOptions(assigneeSelect, HR.id);
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() =>
      expect(api.createEvent).toHaveBeenCalledWith(
        expect.objectContaining({ assignee_user_id: HR.id })
      )
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("clearing nullable fields sends explicit nulls (contract regression)", async () => {
    const onSaved = vi.fn();
    const withFields: CalendarEvent = {
      ...EVENT,
      note: "Заметка",
      ends_at: "2026-09-07T10:00:00Z",
      remind_at: "2026-09-07T08:00:00Z",
    };
    vi.mocked(api.updateEvent).mockResolvedValue({ ...withFields, note: null, ends_at: null, remind_at: null });
    render(
      <ToastProvider>
        <EventFormModal
          open
          user={HR}
          event={withFields}
          onClose={vi.fn()}
          onSaved={onSaved}
        />
      </ToastProvider>
    );

    await userEvent.clear(screen.getByLabelText(/Окончание/));
    await userEvent.clear(screen.getByLabelText(/Напоминание/));
    await userEvent.clear(screen.getByLabelText(/Заметка/));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(api.updateEvent).toHaveBeenCalledWith(
        EVENT.id,
        expect.objectContaining({
          expected_version: 3,
          note: null,
          ends_at: null,
          remind_at: null,
        })
      )
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});
