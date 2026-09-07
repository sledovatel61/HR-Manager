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
