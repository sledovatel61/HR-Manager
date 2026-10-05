import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  EVENT_TYPE_LABELS,
  type CandidateStage,
  type QueueStuckCandidate,
  type QueueSummary,
  type QueueUpcomingEvent,
} from "../../types";
import MyQueuePage from "./MyQueuePage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    getQueueSummary: vi.fn(),
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

function stuck(overrides: Partial<QueueStuckCandidate> = {}): QueueStuckCandidate {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    full_name: "Застрявший кандидат",
    position: "Монтажник РЭА",
    stage: "contacted",
    updated_at: iso(-5),
    ...overrides,
  };
}

function event(overrides: Partial<QueueUpcomingEvent> = {}): QueueUpcomingEvent {
  return {
    id: "55555555-5555-5555-5555-555555555555",
    candidate_id: "44444444-4444-4444-4444-444444444444",
    candidate_full_name: "Гаврилов Антон",
    type: "interview",
    title: "Собеседование",
    status: "scheduled",
    starts_at: iso(1),
    ends_at: null,
    ...overrides,
  };
}

function summary(overrides: Partial<QueueSummary> = {}): QueueSummary {
  return {
    owner_id: "22222222-2222-2222-2222-222222222222",
    owner_username: "hr1",
    personal: true,
    generated_at: iso(0),
    total: 0,
    in_work: 0,
    fresh: 0,
    stuck: 0,
    starts: 0,
    stuck_days: 3,
    horizon_days: 7,
    closed_stages: ["hired", "started", "probation", "fired", "rejected"],
    by_stage: [],
    stuck_sample: [],
    stuck_sample_truncated: false,
    upcoming_events: [],
    upcoming_events_total: 0,
    upcoming_events_truncated: false,
    ...overrides,
  };
}

function byStage(counts: Partial<Record<CandidateStage, number>>) {
  const order: CandidateStage[] = [
    "new",
    "contacted",
    "reached",
    "interview_scheduled",
    "interview_done",
    "offer",
    "hired",
    "started",
    "probation",
    "fired",
    "rejected",
  ];
  return order.map((stage) => ({ stage, count: counts[stage] ?? 0 }));
}

beforeEach(() => {
  vi.mocked(api.getQueueSummary).mockResolvedValue(summary());
  vi.mocked(api.unreadCount).mockResolvedValue({ count: 0 });
});

describe("MyQueuePage", () => {
  it("берёт показатели из серверной сводки, а не из страницы кандидатов", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValue(
      summary({
        total: 121,
        in_work: 100,
        fresh: 4,
        stuck: 1,
        starts: 3,
        by_stage: byStage({ new: 40, contacted: 30, offer: 30, hired: 21 }),
        stuck_sample: [stuck({ id: "stale-1", full_name: "Давно ждёт" })],
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText("100")).toBeInTheDocument();
    const freshTile = screen.getByText("Новые за сутки").closest(".queue-kpi");
    expect(freshTile).toHaveTextContent("4");
    const stuckTile = screen.getByText(/Без движения/).closest(".queue-kpi");
    expect(stuckTile).toHaveTextContent("1");
    const startsTile = screen.getByText(/Выходы на/).closest(".queue-kpi");
    expect(startsTile).toHaveTextContent("3");

    // Кандидат, которого нет ни на одной загруженной странице, виден в
    // «Требуют внимания» — счёт прибыл с сервера целиком.
    expect(screen.getByRole("button", { name: "Давно ждёт" })).toBeInTheDocument();

    // Список кандидатов и календарь экран больше не запрашивает: числа целиком
    // считает сервер.
    expect(api.listCandidates).not.toHaveBeenCalled();
    expect(api.listEvents).not.toHaveBeenCalled();
    expect(api.getQueueSummary).toHaveBeenCalledTimes(1);
  });

  it("показывает воронку по этапам из сводки", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValue(
      summary({
        total: 3,
        in_work: 3,
        by_stage: byStage({ new: 2, contacted: 1, rejected: 5 }),
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText("Воронка моих кандидатов")).toBeInTheDocument();
    const funnel = screen.getByRole("list", { name: undefined });
    const rows = Array.from(funnel.querySelectorAll("li.queue-funnel-row"));
    expect(rows).toHaveLength(6);
    // Закрытые этапы в воронку не входят (rejected: 5 не показан).
    expect(funnel.textContent).toContain("Новый2");
    expect(funnel.textContent).toContain("Контакт1");
    expect(funnel.textContent).not.toContain("Отказ");
  });

  it("показывает ближайшие события и открывает кандидата по клику", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValue(
      summary({
        upcoming_events: [event({ candidate_full_name: "Гаврилов Антон", title: "Звонок" })],
        upcoming_events_total: 1,
      }),
    );
    const onOpenCandidate = vi.fn();

    render(<MyQueuePage onOpenCandidate={onOpenCandidate} />);

    const button = await screen.findByRole("button", { name: "Гаврилов Антон" });
    await userEvent.click(button);
    expect(onOpenCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444");
  });

  it("отмечает, что показаны не все события", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValue(
      summary({
        upcoming_events: Array.from({ length: 6 }, (_, index) =>
          event({ id: `e-${index}`, title: `Событие ${index}` }),
        ),
        upcoming_events_total: 24,
        upcoming_events_truncated: true,
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText(/Показаны первые 6 из 24/)).toBeInTheDocument();
    // Завершённые и отменённые события сервер в выборку не включает вовсе.
    expect(EVENT_TYPE_LABELS.interview).toBe("Собеседование");
  });

  it("отмечает, что показаны не все застрявшие кандидаты", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValue(
      summary({
        stuck: 12,
        stuck_sample: Array.from({ length: 5 }, (_, index) =>
          stuck({ id: `s-${index}`, full_name: `Ждёт ${index}` }),
        ),
        stuck_sample_truncated: true,
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText(/Показаны 5 из 12/)).toBeInTheDocument();
    const stuckTile = screen.getByText(/Без движения/).closest(".queue-kpi");
    // Плитка показывает полное число, а не длину выборки.
    expect(stuckTile).toHaveTextContent("12");
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
    vi.mocked(api.getQueueSummary).mockRejectedValue(new Error("boom"));

    render(<MyQueuePage />);

    expect(await screen.findByText(/Не удалось загрузить/i)).toBeInTheDocument();
  });

  it("на неудачном обновлении показывает отметку устаревших данных, а не молча старые цифры", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValueOnce(
      summary({ total: 7, in_work: 5, by_stage: byStage({ new: 5 }) }),
    );
    render(<MyQueuePage />);

    await waitFor(() => {
      expect(screen.getByText("В работе").closest(".queue-kpi")).toHaveTextContent("5");
    });

    vi.mocked(api.getQueueSummary).mockRejectedValue(new Error("boom"));
    await userEvent.click(screen.getByRole("button", { name: /Обновить/ }));

    expect(await screen.findByText(/Не удалось обновить сводку/)).toBeInTheDocument();
    // Старые числа остаются на экране, но помечены как устаревшие.
    expect(screen.getByText("В работе").closest(".queue-kpi")).toHaveTextContent("5");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("снимает отметку устаревших данных после успешного повтора", async () => {
    vi.mocked(api.getQueueSummary).mockResolvedValueOnce(
      summary({ total: 7, in_work: 5, by_stage: byStage({ new: 5 }) }),
    );
    render(<MyQueuePage />);
    await waitFor(() => {
      expect(screen.getByText("В работе").closest(".queue-kpi")).toHaveTextContent("5");
    });

    vi.mocked(api.getQueueSummary).mockRejectedValueOnce(new Error("boom"));
    await userEvent.click(screen.getByRole("button", { name: /Обновить/ }));
    expect(await screen.findByText(/Не удалось обновить сводку/)).toBeInTheDocument();

    vi.mocked(api.getQueueSummary).mockResolvedValue(
      summary({ total: 9, in_work: 8, by_stage: byStage({ new: 8 }) }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => {
      expect(screen.queryByText(/Не удалось обновить сводку/)).toBeNull();
    });
    expect(screen.getByText("В работе").closest(".queue-kpi")).toHaveTextContent("8");
  });

  it("показывает пустую сводку без ошибок", async () => {
    render(<MyQueuePage />);

    expect(await screen.findByText("Все кандидаты в движении — просроченных нет.")).toBeInTheDocument();
    expect(screen.getByText(/На ближайшие 7 дней событий нет/)).toBeInTheDocument();
  });
});
