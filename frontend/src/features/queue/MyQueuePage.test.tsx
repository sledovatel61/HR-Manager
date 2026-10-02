import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CalendarEvent, Candidate } from "../../types";
import MyQueuePage from "./MyQueuePage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listCandidates: vi.fn(),
    listEvents: vi.fn(),
    unreadCount: vi.fn(),
  };
});

import * as api from "../../api";

const DAY = 24 * 60 * 60 * 1000;

function iso(offsetDays: number): string {
  return new Date(Date.now() + offsetDays * DAY).toISOString();
}

function dateOnly(offsetDays: number): string {
  return new Date(Date.now() + offsetDays * DAY).toISOString().slice(0, 10);
}

function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    full_name: "Петров Пётр",
    phone: "+7 900 123-45-67",
    email: "petrov@example.com",
    source: "site",
    position: "Монтажник РЭА",
    owner_user_id: "22222222-2222-2222-2222-222222222222",
    owner_username: "hr1",
    stage: "new",
    start_date: null,
    start_time: null,
    start_organization: null,
    start_department: null,
    shift: null,
    start_comment: null,
    created_at: iso(-10),
    updated_at: iso(-1),
    deleted_at: null,
    deleted_by_user_id: null,
    is_deleted: false,
    ...overrides,
  };
}

function event(overrides: Partial<CalendarEvent> = {}): CalendarEvent {
  return {
    id: "55555555-5555-5555-5555-555555555555",
    candidate_id: "44444444-4444-4444-4444-444444444444",
    candidate_full_name: "Петров Пётр",
    type: "interview",
    title: "Собеседование",
    note: null,
    status: "scheduled",
    starts_at: iso(1),
    ends_at: null,
    remind_at: null,
    completed_at: null,
    author_user_id: "22222222-2222-2222-2222-222222222222",
    author_username: "hr1",
    assignee_user_id: "22222222-2222-2222-2222-222222222222",
    assignee_username: "hr1",
    version: 1,
    created_at: iso(-1),
    updated_at: iso(-1),
    ...overrides,
  };
}

beforeEach(() => {
  vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 });
  vi.mocked(api.listEvents).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
  vi.mocked(api.unreadCount).mockResolvedValue({ count: 0 });
});

describe("MyQueuePage", () => {
  it("считает показатели сводки из существующих данных", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [
        candidate({ id: "c1", full_name: "Новый кандидат", created_at: iso(-0.2), updated_at: iso(-0.2) }),
        candidate({ id: "c2", full_name: "Застрявший кандидат", stage: "contacted", updated_at: iso(-5) }),
        candidate({ id: "c3", full_name: "Выходит на неделе", stage: "offer", start_date: dateOnly(2) }),
        candidate({ id: "c4", full_name: "Уже оформлен", stage: "hired", updated_at: iso(-9) }),
      ],
      total: 4,
      limit: 100,
      offset: 0,
    });

    render(<MyQueuePage />);

    // В работе = все, кроме закрытых этапов: c1, c2, c3.
    expect(await screen.findByText("3")).toBeInTheDocument();
    // Новые за сутки — только c1.
    const freshTile = screen.getByText("Новые за сутки").closest(".queue-kpi");
    expect(freshTile).toHaveTextContent("1");
    // Без движения 3+ дня — только c2 (c4 на закрытом этапе не считается).
    const stuckTile = screen.getByText(/Без движения/).closest(".queue-kpi");
    expect(stuckTile).toHaveTextContent("1");
    // Выходы на неделе — c3.
    const startsTile = screen.getByText("Выходы на неделе").closest(".queue-kpi");
    expect(startsTile).toHaveTextContent("1");
    // «Требуют внимания» показывает застрявшего кандидата по имени.
    expect(screen.getByRole("button", { name: "Застрявший кандидат" })).toBeInTheDocument();
  });

  it("показывает ближайшие события и открывает кандидата по клику", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 100,
      offset: 0,
    });
    vi.mocked(api.listEvents).mockResolvedValue({
      items: [event({ candidate_full_name: "Гаврилов Антон", title: "Звонок" })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    const onOpenCandidate = vi.fn();

    render(<MyQueuePage onOpenCandidate={onOpenCandidate} />);

    const button = await screen.findByRole("button", { name: "Гаврилов Антон" });
    await userEvent.click(button);
    expect(onOpenCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444");
  });

  it("ведёт в уведомления, если есть непрочитанные", async () => {
    vi.mocked(api.unreadCount).mockResolvedValue({ count: 4 });
    const onOpenNotifications = vi.fn();

    render(<MyQueuePage onOpenNotifications={onOpenNotifications} />);

    const link = await screen.findByRole("button", { name: /Непрочитанных уведомлений: 4/ });
    await userEvent.click(link);
    expect(onOpenNotifications).toHaveBeenCalledTimes(1);
  });

  it("показывает состояние ошибки, если сводку загрузить не удалось", async () => {
    vi.mocked(api.listCandidates).mockRejectedValue(new Error("boom"));

    render(<MyQueuePage />);

    expect(await screen.findByText(/Не удалось загрузить/i)).toBeInTheDocument();
  });
});
