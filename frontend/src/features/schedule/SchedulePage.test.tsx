import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { User, WorkScheduleList, WorkScheduleRow } from "../../types";
import SchedulePage from "./SchedulePage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listWorkSchedule: vi.fn(),
    fetchWorkScheduleSuggestions: vi.fn(),
    listHrUsers: vi.fn(),
    exportWorkScheduleXlsx: vi.fn(),
    updateCandidate: vi.fn(),
    createScheduleEntry: vi.fn(),
    updateScheduleEntry: vi.fn(),
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

function candidateRow(overrides: Partial<WorkScheduleRow> = {}): WorkScheduleRow {
  return {
    kind: "candidate",
    id: "44444444-4444-4444-4444-444444444444",
    candidate_id: "44444444-4444-4444-4444-444444444444",
    entry_date: "2026-08-10",
    number: 1,
    start_time: "09:15:00",
    end_time: null,
    full_name: "Иванов Иван Иванович",
    display_name: "Иванов Иван Иванович",
    organization: "ООО Авион",
    department: "Производственный цех Сокол",
    position: "Грузчик",
    shift: "1 смена",
    comment: "при наличии места",
    owner_user_id: HR.id,
    owner_username: "hr1",
    stage: "offer",
    status: "planned",
    status_label: "Планируется",
    ...overrides,
  };
}

function serviceRow(overrides: Partial<WorkScheduleRow> = {}): WorkScheduleRow {
  return {
    kind: "entry",
    id: "77777777-7777-4777-8777-777777777777",
    candidate_id: null,
    entry_date: "2026-08-10",
    number: 2,
    start_time: "13:00:00",
    end_time: "14:00:00",
    full_name: null,
    display_name: "Увольнение",
    organization: null,
    department: null,
    position: "",
    shift: null,
    comment: null,
    owner_user_id: HR.id,
    owner_username: "hr1",
    stage: null,
    status: "planned",
    status_label: "",
    ...overrides,
  };
}

function listing(items: WorkScheduleRow[]): WorkScheduleList {
  return {
    items,
    total: items.length,
    days: new Set(items.map((item) => item.entry_date)).size,
    period_from: "2026-08-01",
    period_to: "2026-08-31",
    include_rejected: false,
  };
}

function renderPage() {
  return render(
    <ToastProvider>
      <SchedulePage user={HR} onOpenCandidate={vi.fn()} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fetchWorkScheduleSuggestions).mockResolvedValue({
    organizations: ["ООО Авион"],
    departments: ["Производственный цех Сокол"],
    shifts: ["1 смена"],
  });
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.listWorkSchedule).mockResolvedValue(listing([]));
  vi.mocked(api.exportWorkScheduleXlsx).mockResolvedValue({
    blob: new Blob(["x"]),
    filename: "work-schedule.xlsx",
  });
});

describe("SchedulePage — дневные блоки", () => {
  it("группирует строки по дням и показывает число выходов за день", async () => {
    vi.mocked(api.listWorkSchedule).mockResolvedValue(
      listing([
        candidateRow(),
        serviceRow(),
        candidateRow({
          id: "55555555-5555-4555-8555-555555555555",
          candidate_id: "55555555-5555-4555-8555-555555555555",
          full_name: "Петров Пётр Петрович",
          display_name: "Петров Пётр Петрович",
          entry_date: "2026-08-11",
          number: 1,
          start_time: null,
          stage: "started",
          status: "started",
          status_label: "Вышел",
        }),
      ])
    );
    renderPage();

    const monday = await screen.findByRole("region", { name: "пн, 10 авг. 2026" });
    const tuesday = screen.getByRole("region", { name: "вт, 11 авг. 2026" });
    // Служебная строка не считается выходом: 10 августа — один выход.
    expect(within(monday).getByText("выходов: 1")).toBeInTheDocument();
    expect(within(tuesday).getByText("выходов: 1")).toBeInTheDocument();
    expect(screen.getByText("Увольнение")).toBeInTheDocument();
    expect(screen.getByText("13:00–14:00")).toBeInTheDocument();
    expect(screen.getByText(/Петров Пётр Петрович/)).toBeInTheDocument();
  });

  it("открывает карточку кандидата по клику на ФИО", async () => {
    const onOpenCandidate = vi.fn();
    vi.mocked(api.listWorkSchedule).mockResolvedValue(listing([candidateRow()]));
    render(
      <ToastProvider>
        <SchedulePage user={HR} onOpenCandidate={onOpenCandidate} />
      </ToastProvider>
    );

    await userEvent.click(await screen.findByRole("button", { name: "Иванов Иван Иванович" }));
    expect(onOpenCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444");
  });

  it("переносит дату прямо в строке и сохраняет через карточку кандидата", async () => {
    vi.mocked(api.listWorkSchedule).mockResolvedValue(listing([candidateRow()]));
    vi.mocked(api.updateCandidate).mockResolvedValue({} as never);
    renderPage();

    await userEvent.click(
      await screen.findByRole("button", { name: "Изменить дату/время: Иванов Иван Иванович" })
    );
    const date = screen.getByLabelText("Дата выхода: Иванов Иван Иванович");
    await userEvent.clear(date);
    await userEvent.type(date, "2026-08-12");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(api.updateCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444", {
        start_date: "2026-08-12",
        start_time: "09:15",
      })
    );
  });
});

describe("SchedulePage — служебные строки", () => {
  it("добавляет служебную запись из дня графика", async () => {
    vi.mocked(api.listWorkSchedule).mockResolvedValue(listing([candidateRow()]));
    vi.mocked(api.createScheduleEntry).mockResolvedValue({
      id: "88888888-8888-4888-8888-888888888888",
      entry_date: "2026-08-10",
      time_from: "13:00:00",
      time_to: null,
      title: "Увольнение",
      organization: null,
      department: null,
      comment: null,
      author_user_id: HR.id,
      author_username: "hr1",
      created_at: "2026-08-10T10:00:00Z",
      updated_at: "2026-08-10T10:00:00Z",
    });
    renderPage();

    await screen.findByRole("heading", { name: "пн, 10 авг. 2026" });
    await userEvent.click(screen.getByRole("button", { name: "Служебная запись" }));

    const dialog = await screen.findByRole("dialog", { name: "Новая служебная запись" });
    await userEvent.type(within(dialog).getByLabelText(/Текст строки/), "Увольнение");
    await userEvent.click(within(dialog).getByRole("button", { name: "Добавить" }));

    await waitFor(() =>
      expect(api.createScheduleEntry).toHaveBeenCalledWith(
        expect.objectContaining({ entry_date: "2026-08-10", title: "Увольнение" })
      )
    );
  });
});

describe("SchedulePage — права на служебные строки", () => {
  it("не предлагает правку чужой служебной строки (сервер отвечает 403)", async () => {
    vi.mocked(api.listWorkSchedule).mockResolvedValue(
      listing([
        candidateRow(),
        // Служебная строка другого HR: HR без гранта её только читает.
        serviceRow({ owner_user_id: "99999999-9999-4999-8999-999999999999", owner_username: "hr2" }),
      ])
    );
    renderPage();

    await screen.findByRole("heading", { name: "пн, 10 авг. 2026" });
    expect(screen.getByText("только чтение")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Изменить дату/время: Увольнение" })
    ).not.toBeInTheDocument();
  });
});

describe("SchedulePage — фильтры, экспорт и печать", () => {
  it("передаёт фильтры и поиск на сервер", async () => {
    renderPage();
    await screen.findByText("На выбранный период выходов нет");

    await userEvent.type(screen.getByLabelText("Поиск по графику"), "Авион");
    await waitFor(() =>
      expect(api.listWorkSchedule).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "Авион" })
      )
    );

    await userEvent.type(screen.getByLabelText("Организация"), "Авион");
    await waitFor(() =>
      expect(api.listWorkSchedule).toHaveBeenLastCalledWith(
        expect.objectContaining({ organization: "Авион" })
      )
    );
  });

  it("включает отказавшихся по переключателю «Показать отказавшихся»", async () => {
    renderPage();
    await screen.findByText("На выбранный период выходов нет");

    await userEvent.click(screen.getByLabelText("Показать отказавшихся"));
    await waitFor(() =>
      expect(api.listWorkSchedule).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_rejected: true })
      )
    );
  });

  it("скачивает Excel с текущими фильтрами и печатает страницу", async () => {
    const print = vi.fn();
    vi.stubGlobal("print", print);
    vi.mocked(api.listWorkSchedule).mockResolvedValue(listing([candidateRow()]));
    renderPage();

    await screen.findByRole("heading", { name: "пн, 10 авг. 2026" });
    await userEvent.click(screen.getByRole("button", { name: "Скачать Excel" }));
    await waitFor(() => expect(api.exportWorkScheduleXlsx).toHaveBeenCalled());

    await userEvent.click(screen.getByRole("button", { name: "Печать" }));
    expect(print).toHaveBeenCalled();
  });
});
