import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, Reminder, ReminderListPayload } from "../../types";
import { RemindersPage } from "./RemindersPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listReminders: vi.fn(),
    createReminder: vi.fn(),
    updateReminder: vi.fn(),
    completeReminder: vi.fn(),
    cancelReminder: vi.fn(),
    listTimezones: vi.fn(),
    listCandidates: vi.fn(),
  };
});

import * as api from "../../api";

const REMINDER: Reminder = {
  id: "22222222-2222-4222-8222-222222222222",
  owner_user_id: "33333333-3333-4333-8333-333333333333",
  owner_username: "hr1",
  assignee_user_id: "33333333-3333-4333-8333-333333333333",
  assignee_username: "hr1",
  title: "Позвонить кандидату",
  note: null,
  candidate_id: null,
  event_id: null,
  due_at: "2026-09-08T09:00:00Z",
  timezone: "Europe/Moscow",
  importance: "normal",
  recurrence: "none",
  status: "active",
  completed_at: null,
  occurrence: 1,
  version: 1,
  created_at: "2026-09-07T08:00:00Z",
  updated_at: "2026-09-07T08:00:00Z",
  candidate_full_name: null,
};

const CANDIDATE: Candidate = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  full_name: "Тестовый кандидат",
  phone: null,
  email: null,
  source: "referral",
  position: "Разработчик",
  owner_user_id: "33333333-3333-4333-8333-333333333333",
  owner_username: "hr1",
  stage: "new",
  start_date: null,
  start_time: null,
  start_organization: null,
  start_department: null,
  shift: null,
  start_comment: null,
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-01T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

function list(items: Reminder[]): ReminderListPayload {
  return { items, total: items.length, limit: 50, offset: 0 };
}

describe("RemindersPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listTimezones).mockResolvedValue({ timezones: ["Europe/Moscow", "UTC"] });
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [CANDIDATE], total: 1, limit: 200, offset: 0 });
  });

  it("lists active reminders with accessible actions", async () => {
    vi.mocked(api.listReminders).mockResolvedValue(list([REMINDER]));
    render(
      <ToastProvider>
        <RemindersPage user={{ id: "33333333-3333-4333-8333-333333333333", role: "hr" }} />
      </ToastProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("Позвонить кандидату")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "Выполнено" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Отменить" })).toBeTruthy();
  });

  it("creates a reminder through the compact form", async () => {
    vi.mocked(api.listReminders).mockResolvedValue(list([]));
    const create = vi.mocked(api.createReminder).mockResolvedValue(REMINDER);
    render(
      <ToastProvider>
        <RemindersPage user={{ id: "33333333-3333-4333-8333-333333333333", role: "hr" }} />
      </ToastProvider>,
    );
    await waitFor(() => screen.getByLabelText(/Название/));

    await userEvent.type(screen.getByLabelText(/Название/), "Позвонить кандидату");
    await userEvent.type(screen.getByLabelText(/Когда/), "2026-09-08T12:00");
    await userEvent.click(screen.getByRole("button", { name: "Создать напоминание" }));

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Позвонить кандидату",
          timezone: "Europe/Moscow",
          recurrence: "none",
        }),
      );
    });
  });

  it("requires title and due time (no silent submission)", async () => {
    vi.mocked(api.listReminders).mockResolvedValue(list([]));
    const create = vi.mocked(api.createReminder).mockResolvedValue(REMINDER);
    render(
      <ToastProvider>
        <RemindersPage user={{ id: "33333333-3333-4333-8333-333333333333", role: "hr" }} />
      </ToastProvider>,
    );
    await waitFor(() => screen.getByRole("button", { name: "Создать напоминание" }));
    await userEvent.click(screen.getByRole("button", { name: "Создать напоминание" }));
    expect(create).not.toHaveBeenCalled();
  });

  it("completes a reminder", async () => {
    vi.mocked(api.listReminders).mockResolvedValue(list([REMINDER]));
    const complete = vi.mocked(api.completeReminder).mockResolvedValue({
      ...REMINDER,
      status: "completed",
      completed_at: "2026-09-07T09:00:00Z",
    });
    render(
      <ToastProvider>
        <RemindersPage user={{ id: "33333333-3333-4333-8333-333333333333", role: "hr" }} />
      </ToastProvider>,
    );
    await waitFor(() => screen.getByText("Позвонить кандидату"));
    await userEvent.click(screen.getByRole("button", { name: "Выполнено" }));
    await waitFor(() => {
      expect(complete).toHaveBeenCalledWith(REMINDER.id);
    });
  });
});

/**
 * UX feedback 2026-09-29, block B: the candidate picker preloaded 200
 * candidates into a <select> with no search, so anything beyond the first
 * page was unreachable and the field could not be filtered. It must now be a
 * server-side search over ФИО / телефон / email, keeping «без кандидата».
 */
describe("RemindersPage — candidate search (block B)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listTimezones).mockResolvedValue({ timezones: ["Europe/Moscow"] });
    vi.mocked(api.listReminders).mockResolvedValue(list([]));
  });

  const renderPage = () =>
    render(
      <ToastProvider>
        <RemindersPage user={{ id: "33333333-3333-4333-8333-333333333333", role: "hr" }} />
      </ToastProvider>,
    );

  it("searches candidates on the server instead of preloading a page", async () => {
    const search = vi
      .mocked(api.listCandidates)
      .mockResolvedValue({ items: [CANDIDATE], total: 1, limit: 8, offset: 0 });
    renderPage();
    await screen.findByLabelText(/Кандидат/);

    // Nothing is fetched up front any more.
    expect(search).not.toHaveBeenCalled();

    await userEvent.type(screen.getByPlaceholderText(/Поиск по ФИО/), "Тест");

    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(search.mock.calls[0][0]).toMatchObject({ query: "Тест", limit: 8 });
    expect(await screen.findByText("Тестовый кандидат")).toBeTruthy();

    await userEvent.click(screen.getByText("Тестовый кандидат"));
    const create = vi.mocked(api.createReminder).mockResolvedValue(REMINDER);
    await userEvent.type(screen.getByLabelText(/Название/), "Позвонить");
    await userEvent.type(screen.getByLabelText(/Когда/), "2026-09-08T12:00");
    await userEvent.click(screen.getByRole("button", { name: "Создать напоминание" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(expect.objectContaining({ candidate_id: CANDIDATE.id }))
    );
  });

  it("keeps the «без кандидата» option and says so when nothing matches", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 8, offset: 0 });
    renderPage();
    await userEvent.type(screen.getByPlaceholderText(/Поиск по ФИО/), "несуществующий");

    expect(await screen.findByText(/Ничего не найдено/)).toBeTruthy();
    expect(screen.getByText(/может остаться и без кандидата/)).toBeTruthy();
  });

  it("opens the linked candidate card from a reminder row", async () => {
    const onOpenCandidate = vi.fn();
    vi.mocked(api.listReminders).mockResolvedValue(
      list([
        {
          ...REMINDER,
          candidate_id: CANDIDATE.id,
          candidate_full_name: "Тестовый кандидат",
          event_id: "44444444-4444-4444-8444-444444444444",
        },
      ]),
    );
    render(
      <ToastProvider>
        <RemindersPage
          user={{ id: "33333333-3333-4333-8333-333333333333", role: "hr" }}
          onOpenCandidate={onOpenCandidate}
        />
      </ToastProvider>,
    );

    await waitFor(() => expect(screen.getByText("Позвонить кандидату")).toBeTruthy());
    // The row says it came from the calendar, so the two views are linked.
    expect(screen.getByText("из события календаря")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Тестовый кандидат" }));
    expect(onOpenCandidate).toHaveBeenCalledWith(CANDIDATE.id);
  });
});
