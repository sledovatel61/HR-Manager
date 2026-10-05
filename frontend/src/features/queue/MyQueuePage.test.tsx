import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import {
  EVENT_TYPE_LABELS,
  type CandidateStage,
  type QueueDashboard,
  type QueueDashboardPeriodKey,
  type QueueDashboardQuery,
  type QueueStuckCandidate,
  type QueueUpcomingEvent,
} from "../../types";
import MyQueuePage from "./MyQueuePage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    getQueueDashboard: vi.fn(),
    getQueueSummary: vi.fn(),
    listCandidates: vi.fn(),
    listEvents: vi.fn(),
    listHrUsers: vi.fn(),
    listPositionOptions: vi.fn(),
    unreadCount: vi.fn(),
  };
});

import * as api from "../../api";

const DAY = 24 * 60 * 60 * 1000;
/** Бакеты оси: сервер присылает подписи, экран ими не подменяет данные. */
const LABELS = ["29.09", "30.09", "01.10", "02.10", "03.10", "04.10", "05.10"];

function iso(offsetDays: number): string {
  return new Date(Date.now() + offsetDays * DAY).toISOString();
}

function bucket(label: string, index: number) {
  const start = new Date(Date.now() - (LABELS.length - 1 - index) * DAY);
  const end = new Date(start.getTime() + DAY);
  return { bucket: `b-${label}`, from: start.toISOString(), to: end.toISOString(), label };
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

interface DashboardOverrides {
  period?: QueueDashboardPeriodKey;
  kpis?: Partial<QueueDashboard["kpis"]>;
  created?: number[];
  conversionRates?: (number | null)[];
  conversion?: { numerator: number; denominator: number; rate: number | null };
  days?: (number | null)[];
  exits?: number[];
  sources?: { source: string; label: string; count: number }[];
  funnel?: Partial<Record<CandidateStage, number>>;
  attention?: QueueStuckCandidate[];
  attentionTotal?: number;
  attentionTruncated?: boolean;
  events?: QueueUpcomingEvent[];
  eventsTotal?: number;
  eventsTruncated?: boolean;
  unread?: number;
  tasksDue?: { id: string; title: string; due_at: string }[];
  tasksOverdue?: { id: string; title: string; due_at: string }[];
  kpiNotes?: Record<string, string>;
}

function dashboard(overrides: DashboardOverrides = {}): QueueDashboard {
  const period = overrides.period ?? "week";
  const created = overrides.created ?? [0, 1, 0, 2, 1, 0, 0];
  const exits = overrides.exits ?? [0, 0, 1, 0, 2, 0, 1];
  const days = overrides.days ?? [null, 12, null, 15.5, 10, null, 8];
  const conversionRates = overrides.conversionRates ?? [null, 50, null, 25, 0, null, null];
  const stages = overrides.funnel ?? {};
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
  return {
    scope: {
      owner_id: "22222222-2222-2222-2222-222222222222",
      owner_username: "hr1",
      personal: true,
      role: "hr",
    },
    period: {
      key: period,
      from: iso(-6),
      to: iso(1),
      timezone: "UTC",
      bucket_size: period === "today" ? "hour" : period === "week" ? "day" : "month",
      capped: false,
      buckets: LABELS.map(bucket),
    },
    generated_at: iso(0),
    filters: { position: null, stage: null, source: null },
    kpis: {
      total_candidates: 0,
      in_work: 0,
      my_tasks: 0,
      overdue_tasks: 0,
      new_candidates: 0,
      interviews: 0,
      interview_conversion: { numerator: 0, denominator: 0, rate: null },
      average_hiring_days: { value: null, sample: 0 },
      // Нет сущности «вакансия» — null, а не ноль и не демо-число.
      active_vacancies: null,
      weekly_exits: 0,
      ...overrides.kpis,
    },
    kpi_notes: overrides.kpiNotes ?? {
      active_vacancies: "В проекте нет сущности «вакансия».",
    },
    created_candidates_series: created.map((value, index) => ({
      bucket: `b-${LABELS[index]}`,
      value,
    })),
    interview_conversion_series: conversionRates.map((rate, index) => ({
      bucket: `b-${LABELS[index]}`,
      numerator: rate === null ? 0 : Math.round((rate / 100) * created[index]),
      denominator: created[index],
      rate,
    })),
    average_hiring_days_series: days.map((value, index) => ({
      bucket: `b-${LABELS[index]}`,
      value,
      sample: value === null ? 0 : 1,
    })),
    hiring_dynamics: exits.map((value, index) => ({
      bucket: `b-${LABELS[index]}`,
      exits: value,
      hired: days[index] === null ? 0 : 1,
      avg_hiring_days: days[index],
    })),
    sources: overrides.sources ?? [],
    funnel: order.map((stage) => ({ stage, count: stages[stage] ?? 0 })),
    attention_candidates: overrides.attention ?? [],
    attention_candidates_total: overrides.attentionTotal ?? (overrides.attention?.length ?? 0),
    attention_truncated: overrides.attentionTruncated ?? false,
    upcoming_events: overrides.events ?? [],
    upcoming_events_total: overrides.eventsTotal ?? (overrides.events?.length ?? 0),
    upcoming_events_truncated: overrides.eventsTruncated ?? false,
    tasks_due: (overrides.tasksDue ?? []).map((task) => ({
      ...task,
      importance: "normal",
      status: "active",
      candidate_id: null,
      event_id: null,
    })),
    tasks_overdue: (overrides.tasksOverdue ?? []).map((task) => ({
      ...task,
      importance: "normal",
      status: "active",
      candidate_id: null,
      event_id: null,
    })),
    unread_notifications: overrides.unread ?? 0,
    truncated: false,
  };
}

/** Плитка KPI по подписи (подписи повторяются в заголовках карточек). */
function kpiCard(label: string): HTMLElement | null {
  const tiles = Array.from(document.querySelectorAll<HTMLElement>(".queue-kpi"));
  return (
    tiles.find((tile) => tile.querySelector(".queue-kpi-label")?.textContent === label) ?? null
  );
}

function lastQuery(): QueueDashboardQuery {
  const calls = vi.mocked(api.getQueueDashboard).mock.calls;
  return calls[calls.length - 1]?.[0] ?? {};
}

beforeEach(() => {
  // Счётчики вызовов не должны перетекать между тестами: моки общие на файл.
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(api.getQueueDashboard).mockResolvedValue(dashboard());
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.listPositionOptions).mockResolvedValue({
    items: [],
    total: 0,
    limit: 500,
    truncated: false,
  });
});

describe("MyQueuePage", () => {
  it("берёт показатели из серверной сводки, а не из страницы кандидатов", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        kpis: {
          total_candidates: 121,
          in_work: 100,
          my_tasks: 3,
          overdue_tasks: 1,
          new_candidates: 4,
          interviews: 2,
          weekly_exits: 3,
          interview_conversion: { numerator: 2, denominator: 4, rate: 50 },
          average_hiring_days: { value: 18.5, sample: 2 },
        },
        attention: [stuck({ id: "stale-1", full_name: "Давно ждёт" })],
      }),
    );

    render(<MyQueuePage />);

    // Размер очереди считает сервер, а не список на экране.
    expect(await screen.findByText(/В работе 100 из 121/)).toBeInTheDocument();
    expect(kpiCard("Новые отклики")).toHaveTextContent("4");
    expect(kpiCard("Просроченные задачи")).toHaveTextContent("1");
    expect(kpiCard("Конверсия в собеседование")).toHaveTextContent("50");
    expect(kpiCard("Выходы на неделе")).toHaveTextContent("3");
    expect(kpiCard("Мои задачи")).toHaveTextContent("3");

    // Кандидат, которого нет ни на одной загруженной странице, виден в
    // «Требуют внимания» — счёт прибыл с сервера целиком.
    expect(screen.getByRole("button", { name: "Давно ждёт" })).toBeInTheDocument();

    // Список кандидатов и календарь экран больше не запрашивает: числа целиком
    // считает сервер.
    expect(api.listCandidates).not.toHaveBeenCalled();
    expect(api.listEvents).not.toHaveBeenCalled();
    expect(api.getQueueSummary).not.toHaveBeenCalled();
    expect(api.getQueueDashboard).toHaveBeenCalledTimes(1);
  });

  it("показывает «—» и пояснение для метрики, которой нет в модели данных", async () => {
    render(<MyQueuePage />);

    await screen.findByText("Активные вакансии");
    const tile = kpiCard("Активные вакансии");
    // Не ноль и не выдуманное число: честное «—» плюс объяснение на экране.
    expect(tile).toHaveTextContent("—");
    expect(tile).toHaveTextContent("В проекте нет сущности «вакансия»");
  });

  it("не содержит демо-чисел из макета", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        kpis: { new_candidates: 7, interview_conversion: { numerator: 1, denominator: 4, rate: 25 } },
      }),
    );

    render(<MyQueuePage />);

    await screen.findByText("Моя очередь");
    // Числа из design/mockups/bento-arctic.html (248 / 41,2 % / 18,4 дн.) —
    // демонстрационные: на боевом экране их быть не должно.
    expect(screen.queryByText("248")).toBeNull();
    expect(screen.queryByText(/41,2/)).toBeNull();
    expect(screen.queryByText(/18,4/)).toBeNull();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("переключает период и передаёт его на сервер", async () => {
    render(<MyQueuePage />);
    await screen.findByText("Моя очередь");
    expect(lastQuery().period).toBe("week");

    await userEvent.click(screen.getByRole("button", { name: "Сегодня" }));

    await waitFor(() => {
      expect(lastQuery().period).toBe("today");
    });
    // Период считает сервер: клиент не подменяет границы дат.
    expect(api.getQueueDashboard).toHaveBeenCalledTimes(2);

    await userEvent.click(screen.getByRole("button", { name: "Всё" }));
    await waitFor(() => {
      expect(lastQuery().period).toBe("all");
    });
  });

  it("передаёт фильтры на сервер и перезапрашивает сводку", async () => {
    render(<MyQueuePage />);
    await screen.findByText("Моя очередь");

    await userEvent.click(screen.getByRole("button", { name: /Фильтры/ }));
    // Текстовый фильтр не отправляет запрос на каждое нажатие.
    await userEvent.type(screen.getByLabelText("Должность"), "Монтажник");
    expect(api.getQueueDashboard).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Применить" }));
    await waitFor(() => {
      expect(lastQuery().position).toBe("Монтажник");
    });
    await userEvent.selectOptions(screen.getByLabelText("Этап"), "interview_done");
    await userEvent.selectOptions(screen.getByLabelText("Источник"), "referral");

    await waitFor(() => {
      expect(lastQuery()).toMatchObject({
        position: "Монтажник",
        stage: "interview_done",
        source: "referral",
      });
    });
    expect(api.getQueueDashboard).toHaveBeenCalledTimes(4);
  });

  it("показывает воронку по этапам из сводки", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({ funnel: { new: 2, contacted: 1, rejected: 5 } }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText("Воронка моих кандидатов")).toBeInTheDocument();
    const rows = document.querySelectorAll("li.queue-funnel-row");
    expect(rows).toHaveLength(6);
    const funnel = rows[0].closest("ul");
    expect(funnel?.textContent).toContain("Новый2");
    expect(funnel?.textContent).toContain("Контакт1");
    // Закрытые этапы в воронку не входят (rejected: 5 не показан).
    expect(funnel?.textContent).not.toContain("Отказ");
  });

  it("показывает ближайшие события и открывает кандидата по клику", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        events: [event({ candidate_full_name: "Гаврилов Антон", title: "Звонок" })],
        eventsTotal: 1,
      }),
    );
    const onOpenCandidate = vi.fn();

    render(<MyQueuePage onOpenCandidate={onOpenCandidate} />);

    const button = await screen.findByRole("button", { name: "Гаврилов Антон" });
    await userEvent.click(button);
    expect(onOpenCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444");
  });

  it("отмечает, что показаны не все события", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        events: Array.from({ length: 6 }, (_, index) =>
          event({ id: `e-${index}`, title: `Событие ${index}` }),
        ),
        eventsTotal: 24,
        eventsTruncated: true,
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText(/Показаны первые 6 из 24/)).toBeInTheDocument();
    // Завершённые и отменённые события сервер в выборку не включает вовсе.
    expect(EVENT_TYPE_LABELS.interview).toBe("Собеседование");
  });

  it("отмечает, что показаны не все застрявшие кандидаты", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        attention: Array.from({ length: 5 }, (_, index) =>
          stuck({ id: `s-${index}`, full_name: `Ждёт ${index}` }),
        ),
        attentionTotal: 12,
        attentionTruncated: true,
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText(/Показаны 5 из 12/)).toBeInTheDocument();
    // Полное число приходит с сервера, а не считается по длине выборки.
    expect(screen.getByText("Требуют внимания")).toBeInTheDocument();
  });

  it("ведёт в уведомления, если есть непрочитанные", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(dashboard({ unread: 4 }));
    const onOpenNotifications = vi.fn();

    render(<MyQueuePage onOpenNotifications={onOpenNotifications} />);

    const link = await screen.findByRole("button", { name: /Непрочитанных уведомлений: 4/ });
    await userEvent.click(link);
    expect(onOpenNotifications).toHaveBeenCalledTimes(1);
  });

  it("рисует графики и даёт таблицу значений без наведения", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        created: [1, 2, 3, 4, 5, 6, 7],
        sources: [
          { source: "site", label: "Сайт компании", count: 12 },
          { source: "referral", label: "Рекомендация", count: 3 },
        ],
      }),
    );

    render(<MyQueuePage />);

    const charts = await screen.findAllByRole("img");
    expect(charts.length).toBeGreaterThanOrEqual(5);
    // У графика есть подпись со сводкой — не только визуальный ряд.
    const dynamics = charts.find((node) => node.getAttribute("aria-label")?.includes("Динамика найма"));
    expect(dynamics).toBeTruthy();

    // Таблица значений: значения доступны без мыши и без hover.
    const fallback = screen.getAllByText("Таблица значений");
    expect(fallback.length).toBeGreaterThanOrEqual(4);
    const sourcesBlock = screen.getByRole("heading", { name: "Источники" }).closest(".queue-card");
    expect(sourcesBlock).not.toBeNull();
    // Значение подписано рядом с полосой — без наведения.
    expect(
      within(sourcesBlock as HTMLElement).getAllByText("Сайт компании").length,
    ).toBeGreaterThan(0);
    expect(within(sourcesBlock as HTMLElement).getAllByText("12").length).toBeGreaterThan(0);
  });

  it("на пустом периоде объясняет пустоту, а не рисует нули", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        created: [0, 0, 0, 0, 0, 0, 0],
        exits: [0, 0, 0, 0, 0, 0, 0],
        days: [null, null, null, null, null, null, null],
        conversionRates: [null, null, null, null, null, null, null],
        sources: [],
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText("Все кандидаты в движении — просроченных нет.")).toBeInTheDocument();
    expect(screen.getByText(/На ближайшие 7 дней событий нет/)).toBeInTheDocument();
    // Графики не притворяются данными: вместо SVG — объяснение.
    expect(screen.getAllByText("За период новых кандидатов не было.").length).toBeGreaterThan(0);
    expect(screen.getByText("За период выходов и наймов не было.")).toBeInTheDocument();
    expect(screen.queryAllByRole("img").length).toBe(0);
  });

  it("показывает скелетоны, пока первая сводка загружается", async () => {
    let resolve!: (value: QueueDashboard) => void;
    vi.mocked(api.getQueueDashboard).mockReturnValue(
      new Promise<QueueDashboard>((resolveFn) => {
        resolve = resolveFn;
      }),
    );

    render(<MyQueuePage />);

    expect(document.querySelectorAll(".queue-skeleton").length).toBeGreaterThan(0);
    expect(screen.getByText("Загружаем сводку очереди…")).toBeInTheDocument();

    resolve(dashboard());
    await screen.findByText("Моя очередь");
    expect(document.querySelectorAll(".queue-skeleton").length).toBe(0);
  });

  it("показывает состояние ошибки, если сводку загрузить не удалось", async () => {
    vi.mocked(api.getQueueDashboard).mockRejectedValue(new Error("boom"));

    render(<MyQueuePage />);

    expect(await screen.findByText(/Не удалось загрузить/i)).toBeInTheDocument();
  });

  it("отдельно показывает отсутствие прав (403) и не показывает чужие данные", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValueOnce(
      dashboard({ kpis: { new_candidates: 9 } }),
    );
    const { unmount } = render(<MyQueuePage />);
    await screen.findByText("Моя очередь");
    expect(screen.getByText("9")).toBeInTheDocument();
    unmount();

    vi.mocked(api.getQueueDashboard).mockRejectedValue(new ApiError(403, "forbidden"));
    render(<MyQueuePage />);

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
    // Данные предыдущей области на экране не остаются.
    expect(screen.queryByText("9")).toBeNull();
  });

  it("на неудачном обновлении показывает отметку устаревших данных, а не молча старые цифры", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValueOnce(
      dashboard({ kpis: { total_candidates: 7, in_work: 5 } }),
    );
    render(<MyQueuePage />);

    await waitFor(() => {
      expect(screen.getByText(/В работе 5 из 7/)).toBeInTheDocument();
    });

    vi.mocked(api.getQueueDashboard).mockRejectedValue(new Error("boom"));
    await userEvent.click(screen.getByRole("button", { name: /Обновить/ }));

    expect(await screen.findByText(/Не удалось обновить сводку/)).toBeInTheDocument();
    // Старые числа остаются на экране, но помечены как устаревшие.
    expect(screen.getByText(/В работе 5 из 7/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("снимает отметку устаревших данных после успешного повтора", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValueOnce(
      dashboard({ kpis: { total_candidates: 7, in_work: 5 } }),
    );
    render(<MyQueuePage />);
    await waitFor(() => {
      expect(screen.getByText(/В работе 5 из 7/)).toBeInTheDocument();
    });

    vi.mocked(api.getQueueDashboard).mockRejectedValueOnce(new Error("boom"));
    await userEvent.click(screen.getByRole("button", { name: /Обновить/ }));
    expect(await screen.findByText(/Не удалось обновить сводку/)).toBeInTheDocument();

    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({ kpis: { total_candidates: 9, in_work: 8 } }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => {
      expect(screen.queryByText(/Не удалось обновить сводку/)).toBeNull();
    });
    expect(screen.getByText(/В работе 8 из 9/)).toBeInTheDocument();
  });

  it("показывает задачи на период и просроченные", async () => {
    vi.mocked(api.getQueueDashboard).mockResolvedValue(
      dashboard({
        kpis: { my_tasks: 2, overdue_tasks: 1 },
        tasksDue: [
          { id: "t-1", title: "Позвонить кандидату", due_at: iso(0) },
          { id: "t-2", title: "Проверить документы", due_at: iso(0) },
        ],
        tasksOverdue: [{ id: "t-3", title: "Отправить приглашение", due_at: iso(-2) }],
      }),
    );

    render(<MyQueuePage />);

    expect(await screen.findByText("Позвонить кандидату")).toBeInTheDocument();
    expect(screen.getByText("Отправить приглашение")).toBeInTheDocument();
    expect(kpiCard("Мои задачи")).toHaveTextContent("2");
  });

  it("сохраняет выбранный период и фильтры между переходами", async () => {
    const first = render(<MyQueuePage />);
    await screen.findByText("Моя очередь");
    await userEvent.click(screen.getByRole("button", { name: "Сегодня" }));
    await waitFor(() => {
      expect(lastQuery().period).toBe("today");
    });
    first.unmount();

    render(<MyQueuePage />);

    await waitFor(() => {
      expect(lastQuery().period).toBe("today");
    });
  });
});
