import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type {
  AnalyticsFunnelReport,
  AnalyticsKpiReport,
  AnalyticsKpis,
  User,
  UserListItem,
} from "../../types";
import { ApiError } from "../../api";
import AnalyticsPage from "./AnalyticsPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    ApiError: original.ApiError,
    fetchAnalyticsKpi: vi.fn(),
    fetchAnalyticsFunnel: vi.fn(),
    listHrUsers: vi.fn(),
    exportAnalyticsCsv: vi.fn(),
  };
});

import * as api from "../../api";

const MANAGER: User = {
  id: "33333333-3333-3333-3333-333333333333",
  username: "mgr",
  full_name: "Менеджер Один",
  role: "manager",
  is_active: true,
  locked_until: null,
  last_login_at: null,
  created_at: "2026-09-01T10:00:00Z",
};

const HR_LIST: UserListItem = {
  id: "22222222-2222-2222-2222-222222222222",
  username: "hr1",
  full_name: "HR Один",
  role: "hr",
  is_active: true,
};

function kpis(overrides: Partial<AnalyticsKpis> = {}): AnalyticsKpis {
  return {
    created_candidates: 5,
    processed_candidates: 3,
    calls: 7,
    reached: 2,
    interviews_scheduled: 1,
    interviews_done: 1,
    offers: 1,
    hired: 1,
    dismissed: 1,
    terminated: 0,
    ...overrides,
  };
}

function kpiReport(overrides: Partial<AnalyticsKpiReport> = {}): AnalyticsKpiReport {
  return {
    period: { from: "2026-01-01T00:00:00Z", to: "2026-02-01T00:00:00Z", timezone: "UTC" },
    filters: { hr_id: null, source: null },
    scope: "team",
    kpis: kpis(),
    conversions: [
      { from_stage: "new", to_stage: "contacted", numerator: 2, denominator: 5, rate: 40.0 },
      { from_stage: "contacted", to_stage: "reached", numerator: 0, denominator: 2, rate: 0.0 },
      { from_stage: "reached", to_stage: "interview_scheduled", numerator: 0, denominator: 0, rate: null },
    ],
    by_source: [
      { source: "site", created: 3, hired: 1, dismissed: 1, terminated: 0 },
      { source: "referral", created: 2, hired: 0, dismissed: 0, terminated: 0 },
    ],
    by_hr: [
      { hr_id: HR_LIST.id, username: "hr1", created: 5, processed: 3, hired: 1, dismissed: 1, terminated: 0 },
    ],
    ...overrides,
  };
}

function funnelReport(overrides: Partial<AnalyticsFunnelReport> = {}): AnalyticsFunnelReport {
  return {
    period: { from: "2026-01-01T00:00:00Z", to: "2026-02-01T00:00:00Z", timezone: "UTC" },
    filters: { hr_id: null, source: null },
    stages: [
      { stage: "new", reached: 5 },
      { stage: "contacted", reached: 2 },
      { stage: "reached", reached: 0 },
    ],
    conversions: kpiReport().conversions,
    ...overrides,
  };
}

function renderPage(user: User = MANAGER) {
  return render(
    <ToastProvider>
      <AnalyticsPage user={user} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.fetchAnalyticsKpi).mockResolvedValue(kpiReport());
  vi.mocked(api.fetchAnalyticsFunnel).mockResolvedValue(funnelReport());
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [HR_LIST], total: 1 });
});

describe("AnalyticsPage", () => {
  it("renders the KPI strip with values and definitions", async () => {
    renderPage();

    expect(await screen.findByText("Создано кандидатов")).toBeInTheDocument();
    // Values are scoped to their KPI cards (the same numbers may appear in
    // the funnel table). The tooltip (definition) identifies the card.
    const createdCard = screen.getByTitle(/Уникальные кандидаты, созданные в периоде/);
    expect(within(createdCard).getByText("5")).toBeInTheDocument();
    const callsCard = screen.getByTitle(/Записи взаимодействий типа «звонок»/);
    expect(within(callsCard).getByText("7")).toBeInTheDocument();
  });

  it("requests the report with the exact chosen period/filters/timezone", async () => {
    renderPage();

    const timezone = await screen.findByLabelText("Таймзона");
    await userEvent.selectOptions(timezone, "Europe/Moscow");
    const hr = screen.getByLabelText("Ответственный");
    await userEvent.selectOptions(hr, HR_LIST.id);
    const source = screen.getByLabelText("Источник");
    await userEvent.selectOptions(source, "referral");

    await waitFor(() => {
      const calls = vi.mocked(api.fetchAnalyticsKpi).mock.calls;
      expect(calls.length).toBeGreaterThanOrEqual(1);
      const last = calls[calls.length - 1][0];
      expect(last.timezone).toBe("Europe/Moscow");
      expect(last.hr_id).toBe(HR_LIST.id);
      expect(last.source).toBe("referral");
      expect(new Date(last.to).getTime() - new Date(last.from).getTime()).toBeGreaterThan(0);
    });

    // The funnel request receives the SAME parameters.
    const funnelCalls = vi.mocked(api.fetchAnalyticsFunnel).mock.calls;
    const last = funnelCalls[funnelCalls.length - 1][0];
    expect(last.timezone).toBe("Europe/Moscow");
    expect(last.hr_id).toBe(HR_LIST.id);
    expect(last.source).toBe("referral");
  });

  it("preset tabs recompute the period (day vs quarter differ)", async () => {
    renderPage();

    await screen.findByText("Создано кандидатов");
    const first = vi.mocked(api.fetchAnalyticsKpi).mock.calls[0][0];

    await userEvent.click(screen.getByRole("tab", { name: "Квартал" }));

    await waitFor(() => {
      const calls = vi.mocked(api.fetchAnalyticsKpi).mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last.from).not.toBe(first.from);
    });
  });

  it("shows N/A for null-rate conversions and keeps real zeros", async () => {
    renderPage();

    const conversionRow = await screen.findByRole("row", {
      name: /Дозвон → Собеседование назначено/,
    });
    expect(within(conversionRow).getByText("N/A")).toBeInTheDocument();

    const zeroRow = screen.getByRole("row", { name: /Контакт → Дозвон/ });
    expect(within(zeroRow).getByText("0.00%")).toBeInTheDocument();
    expect(within(zeroRow).queryByText("N/A")).not.toBeInTheDocument();
  });

  it("keeps filters and period when switching views", async () => {
    renderPage();

    const source = await screen.findByLabelText("Источник");
    await userEvent.selectOptions(source, "site");
    await waitFor(() => {
      const calls = vi.mocked(api.fetchAnalyticsKpi).mock.calls;
      expect(calls[calls.length - 1][0].source).toBe("site");
    });

    await userEvent.click(screen.getByRole("tab", { name: "Воронка" }));
    expect((await screen.findAllByRole("table")).length).toBeGreaterThanOrEqual(2);

    await userEvent.click(screen.getByRole("tab", { name: "Разрезы" }));
    await waitFor(() => {
      const calls = vi.mocked(api.fetchAnalyticsKpi).mock.calls;
      const last = calls[calls.length - 1][0];
      expect(last.source).toBe("site");
    });
    expect(await screen.findByText("По ответственным")).toBeInTheDocument();
    const byHrBlock = screen.getByRole("region", { name: "По ответственным" });
    expect(within(byHrBlock).getByText("hr1")).toBeInTheDocument();
    expect(screen.getByText("По источникам")).toBeInTheDocument();
    // The source select still shows the chosen value.
    expect((screen.getByLabelText("Источник") as HTMLSelectElement).value).toBe("site");
  });

  it("shows the rejections/terminations block with numbers", async () => {
    renderPage();
    expect(await screen.findByText("Отказы и увольнения")).toBeInTheDocument();
    const block = screen.getByRole("region", { name: "Отказы и увольнения" });
    expect(within(block).getByText("1")).toBeInTheDocument(); // dismissed
  });

  it("shows the empty-period note when every KPI is zero", async () => {
    vi.mocked(api.fetchAnalyticsKpi).mockResolvedValue(kpiReport({ kpis: kpis({
      created_candidates: 0, processed_candidates: 0, calls: 0, reached: 0,
      interviews_scheduled: 0, interviews_done: 0, offers: 0, hired: 0,
      dismissed: 0, terminated: 0,
    }) }));
    renderPage();
    expect(
      await screen.findByText(/нет зафиксированных фактов/)
    ).toBeInTheDocument();
  });

  it("shows the error state and retries", async () => {
    vi.mocked(api.fetchAnalyticsKpi)
      .mockRejectedValueOnce(new ApiError(500, "Ошибка запроса (500)."))
      .mockResolvedValue(kpiReport());
    vi.mocked(api.fetchAnalyticsFunnel)
      .mockRejectedValueOnce(new ApiError(500, "Ошибка запроса (500)."))
      .mockResolvedValue(funnelReport());
    renderPage();

    expect(await screen.findByText("Не удалось загрузить данные")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить попытку" }));
    expect(await screen.findByText("Создано кандидатов")).toBeInTheDocument();
  });

  it("shows the permission-denied state on 403 (direct URL case)", async () => {
    vi.mocked(api.fetchAnalyticsKpi).mockRejectedValue(
      new ApiError(403, "Доступ к аналитике разрешён только менеджеру или администратору.")
    );
    vi.mocked(api.fetchAnalyticsFunnel).mockRejectedValue(
      new ApiError(403, "Доступ к аналитике разрешён только менеджеру или администратору.")
    );
    renderPage();

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
  });

  it("exports only after a successful response and shows a success toast", async () => {
    const createObjectURL = vi.fn(() => "blob:fake");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL,
      revokeObjectURL,
    });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.mocked(api.exportAnalyticsCsv).mockResolvedValue({
      blob: new Blob(["a,b"], { type: "text/csv" }),
      filename: "analytics-2026-01-01-2026-02-01.csv",
    });

    renderPage();
    await screen.findByText("Создано кандидатов");
    await userEvent.click(screen.getByRole("button", { name: "Экспорт CSV" }));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("Отчёт выгружен.")).toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("does not fake a download when the export fails", async () => {
    const createObjectURL = vi.fn(() => "blob:fake");
    vi.stubGlobal("URL", { ...URL, createObjectURL });
    vi.mocked(api.exportAnalyticsCsv).mockRejectedValue(
      new ApiError(422, "Период не может превышать 366 дней.")
    );

    renderPage();
    await screen.findByText("Создано кандидатов");
    await userEvent.click(screen.getByRole("button", { name: "Экспорт CSV" }));

    expect(await screen.findByText("Период не может превышать 366 дней.")).toBeInTheDocument();
    expect(createObjectURL).not.toHaveBeenCalled();
    expect(screen.queryByText("Отчёт выгружен.")).not.toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("ignores stale responses when the period changes quickly", async () => {
    let resolveFirst: (value: AnalyticsKpiReport) => void = () => {};
    let resolveSecond: (value: AnalyticsKpiReport) => void = () => {};
    const first = new Promise<AnalyticsKpiReport>((resolve) => {
      resolveFirst = resolve;
    });
    const second = new Promise<AnalyticsKpiReport>((resolve) => {
      resolveSecond = resolve;
    });
    vi.mocked(api.fetchAnalyticsKpi).mockReturnValueOnce(first).mockReturnValue(second);
    vi.mocked(api.fetchAnalyticsFunnel).mockResolvedValue(funnelReport());

    renderPage();
    await screen.findByLabelText("Период отчёта");

    await userEvent.click(screen.getByRole("tab", { name: "День" }));
    resolveSecond(kpiReport({ kpis: kpis({ created_candidates: 99 }) }));
    resolveFirst(kpiReport({ kpis: kpis({ created_candidates: 1 }) }));

    await waitFor(() => {
      const createdCard = screen.getByTitle(/Уникальные кандидаты, созданные в периоде/);
      expect(within(createdCard).getByText("99")).toBeInTheDocument();
    });
    const createdCard = screen.getByTitle(/Уникальные кандидаты, созданные в периоде/);
    expect(within(createdCard).queryByText("1")).not.toBeInTheDocument();
  });

  it("supports keyboard navigation across view tabs", async () => {
    renderPage();
    await screen.findByText("Создано кандидатов");

    const kpiTab = screen.getByRole("tab", { name: "KPI" });
    kpiTab.focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Воронка" })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Разрезы" })).toHaveAttribute("aria-selected", "true");
  });

  it("renders accessible tables with captions and scoped headers", async () => {
    renderPage();

    await userEvent.click(await screen.findByRole("tab", { name: "Воронка" }));
    const tables = await screen.findAllByRole("table");
    expect(tables.length).toBeGreaterThanOrEqual(2);
    for (const table of tables) {
      const caption = table.querySelector("caption");
      expect(caption).not.toBeNull();
      const headers = table.querySelectorAll("th");
      expect(headers.length).toBeGreaterThan(0);
      headers.forEach((th) => {
        expect(["col", "row"]).toContain(th.getAttribute("scope"));
      });
    }
  });

  it("hides the HR/source filters for an HR role but the API 403 still lands in the denied state", async () => {
    vi.mocked(api.fetchAnalyticsKpi).mockRejectedValue(new ApiError(403, "нет доступа"));
    vi.mocked(api.fetchAnalyticsFunnel).mockRejectedValue(new ApiError(403, "нет доступа"));
    const hrUser: User = { ...MANAGER, role: "hr", username: "hr1" };
    renderPage(hrUser);

    expect(screen.queryByLabelText("Ответственный")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Источник")).not.toBeInTheDocument();
    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
  });
});
