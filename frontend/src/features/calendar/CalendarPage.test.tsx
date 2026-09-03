import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { CalendarEvent, User } from "../../types";
import CalendarPage from "./CalendarPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listEvents: vi.fn(),
    listHrUsers: vi.fn(),
    updateEvent: vi.fn(),
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

const MANAGER: User = {
  ...HR,
  id: "33333333-3333-3333-3333-333333333333",
  username: "mgr",
  full_name: "Менеджер",
  role: "manager",
};

function event(overrides: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    id: "55555555-5555-5555-5555-555555555555",
    candidate_id: "44444444-4444-4444-4444-444444444444",
    candidate_full_name: "Петров Пётр",
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
    version: 1,
    created_at: "2026-09-06T09:00:00Z",
    updated_at: "2026-09-06T09:00:00Z",
    ...overrides,
  };
}

function renderPage(user: User = HR, onOpenCandidate = vi.fn()) {
  return render(
    <ToastProvider>
      <CalendarPage user={user} onOpenCandidate={onOpenCandidate} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
});

describe("CalendarPage", () => {
  it("renders the week grid with events and panel data", async () => {
    vi.mocked(api.listEvents).mockImplementation(async (params = {}) => {
      const isGrid = params.from !== undefined && params.to !== undefined && params.remind_from === undefined && params.remind_to === undefined;
      return { items: isGrid ? [event()] : [], total: isGrid ? 1 : 0, limit: 100, offset: 0 };
    });
    renderPage();

    expect(await screen.findByText("Созвон")).toBeInTheDocument();
    // Panels rendered with their headings.
    expect(screen.getByText("Просроченные")).toBeInTheDocument();
    expect(screen.getByText("Ближайшие")).toBeInTheDocument();
    expect(screen.getByText("Напоминания")).toBeInTheDocument();
  });

  it("shows empty and error/retry states", async () => {
    vi.mocked(api.listEvents)
      .mockRejectedValueOnce(new api.ApiError(500, "Сбой"))
      .mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    renderPage();

    const retry = await screen.findByRole("button", { name: /повторить/i });
    await userEvent.click(retry);
    expect(await screen.findByText("На этой неделе событий нет")).toBeInTheDocument();
  });

  it("passes period, type, status and owner filters to the API", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    vi.mocked(api.listHrUsers).mockResolvedValue({
      items: [{ id: HR.id, username: "hr1", full_name: "HR Один", role: "hr", is_active: true }],
      total: 1,
    });
    renderPage(MANAGER);

    const user = userEvent.setup();
    await user.selectOptions(await screen.findByLabelText("Тип"), "interview");
    await user.selectOptions(screen.getByLabelText("Состояние"), "completed");
    await user.selectOptions(screen.getByLabelText("Ответственный"), HR.id);

    await waitFor(() => {
      const gridCalls = vi
        .mocked(api.listEvents)
        .mock.calls.filter(([params]) => params?.type === "interview");
      const last = gridCalls.at(-1)?.[0];
      expect(last).toMatchObject({
        from: expect.stringMatching(/^20\d{2}-\d{2}-\d{2}T/),
        to: expect.stringMatching(/^20\d{2}-\d{2}-\d{2}T/),
        type: "interview",
        status: "completed",
        owner_id: HR.id,
      });
    });
  });

  it("hides the owner filter for HR and shows it for managers", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    const { unmount } = renderPage(HR);
    expect(screen.queryByLabelText("Ответственный")).not.toBeInTheDocument();
    unmount();
    renderPage(MANAGER);
    expect(await screen.findByLabelText("Ответственный")).toBeInTheDocument();
  });

  it("loads reminders server-side with remind_from/remind_to", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 5, offset: 0 });
    renderPage();

    await waitFor(() => {
      const params = vi.mocked(api.listEvents).mock.calls.flatMap(([p]) => (p ? [p] : []));
      expect(params.some((p) => p.remind_from !== undefined)).toBe(true);
      expect(params.some((p) => p.remind_to !== undefined)).toBe(true);
      expect(params.some((p) => p.from !== undefined && p.to !== undefined)).toBe(true);
    });
  });

  it("moves the week with keyboard-accessible buttons", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
    renderPage();

    const prev = screen.getByRole("button", { name: "Предыдущая неделя" });
    const next = screen.getByRole("button", { name: "Следующая неделя" });
    await userEvent.click(next);
    await waitFor(() =>
      expect(vi.mocked(api.listEvents)).toHaveBeenCalled()
    );
    await userEvent.click(prev);
    expect(prev).toBeInTheDocument();
  });

  it("completes an event from a panel with its current version", async () => {
    vi.mocked(api.listEvents).mockImplementation(async (params = {}) => {
      const isUpcoming = params.from !== undefined && params.to === undefined && params.remind_from === undefined && params.remind_to === undefined;
      return { items: isUpcoming ? [event()] : [], total: isUpcoming ? 1 : 0, limit: 5, offset: 0 };
    });
    vi.mocked(api.updateEvent).mockResolvedValue({ ...event(), status: "completed" });
    renderPage();

    await screen.findByText("Созвон");
    const buttons = screen.getAllByRole("button", { name: /Выполнить: Созвон/ });
    await userEvent.click(buttons[0]);

    await waitFor(() =>
      expect(api.updateEvent).toHaveBeenCalledWith("55555555-5555-5555-5555-555555555555", {
        expected_version: 1,
        status: "completed",
      })
    );
  });

  it("opens the candidate card from the event dialog", async () => {
    vi.mocked(api.listEvents).mockResolvedValue({
      items: [event()],
      total: 1,
      limit: 100,
      offset: 0,
    });
    const onOpenCandidate = vi.fn();
    renderPage(HR, onOpenCandidate);

    // The event appears in the grid chip and in the upcoming panel; click
    // the first occurrence (the grid chip) to open the edit dialog.
    const titles = await screen.findAllByText("Созвон");
    await userEvent.click(titles[0]);
    await userEvent.click(
      await screen.findByRole("button", { name: "Открыть карточку кандидата" })
    );
    expect(onOpenCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444");
  });
});
