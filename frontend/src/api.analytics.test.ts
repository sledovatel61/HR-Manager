import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  exportAnalyticsCsv,
  fetchAnalyticsFunnel,
  fetchAnalyticsKpi,
  onUnauthorized,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const QUERY = {
  from: "2026-01-01T00:00:00+00:00",
  to: "2026-02-01T00:00:00+00:00",
  timezone: "Europe/Moscow",
  hr_id: "11111111-1111-1111-1111-111111111111",
  source: "site" as const,
};

describe("Analytics API client", () => {
  it("fetchAnalyticsKpi sends the exact period/filters/timezone", async () => {
    const fetchMock = stubFetch(Response.json({ kpis: {} }, { status: 200 }));

    await fetchAnalyticsKpi(QUERY);

    const [url, init] = fetchMock.mock.calls[0];
    const parsed = new URL(String(url), "http://localhost");
    expect(parsed.pathname).toBe("/api/analytics/kpi");
    expect(parsed.searchParams.get("from")).toBe(QUERY.from);
    expect(parsed.searchParams.get("to")).toBe(QUERY.to);
    expect(parsed.searchParams.get("timezone")).toBe("Europe/Moscow");
    expect(parsed.searchParams.get("hr_id")).toBe(QUERY.hr_id);
    expect(parsed.searchParams.get("source")).toBe("site");
    expect(init?.credentials).toBe("same-origin");
  });

  it("fetchAnalyticsKpi omits optional filters when absent", async () => {
    const fetchMock = stubFetch(Response.json({}, { status: 200 }));

    await fetchAnalyticsKpi({ from: QUERY.from, to: QUERY.to });

    const [url] = fetchMock.mock.calls[0];
    const parsed = new URL(String(url), "http://localhost");
    expect(parsed.searchParams.has("hr_id")).toBe(false);
    expect(parsed.searchParams.has("source")).toBe(false);
    expect(parsed.searchParams.has("timezone")).toBe(false);
  });

  it("fetchAnalyticsFunnel hits the funnel endpoint with the same params", async () => {
    const fetchMock = stubFetch(Response.json({ stages: [] }, { status: 200 }));

    await fetchAnalyticsFunnel(QUERY);

    const [url] = fetchMock.mock.calls[0];
    const parsed = new URL(String(url), "http://localhost");
    expect(parsed.pathname).toBe("/api/analytics/funnel");
    expect(parsed.searchParams.get("from")).toBe(QUERY.from);
    expect(parsed.searchParams.get("hr_id")).toBe(QUERY.hr_id);
  });

  it("exportAnalyticsCsv returns the blob and the server filename on success", async () => {
    // jsdom's Response cannot take a Blob body — a text body exercises
    // response.blob() (which is what the production code calls).
    stubFetch(
      new Response("a,b", {
        status: 200,
        headers: {
          "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": 'attachment; filename="analytics-2026-01-01-2026-02-01.csv"',
        },
      })
    );

    const result = await exportAnalyticsCsv(QUERY);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    const parsed = new URL(String(url), "http://localhost");
    expect(parsed.pathname).toBe("/api/analytics/export");
    expect(parsed.searchParams.get("format")).toBe("csv");
    expect(parsed.searchParams.get("timezone")).toBe("Europe/Moscow");
    expect(init?.credentials).toBe("same-origin");
    expect(result.filename).toBe("analytics-2026-01-01-2026-02-01.csv");
    await expect(result.blob.text()).resolves.toBe("a,b");
  });

  it("exportAnalyticsCsv raises ApiError with the backend detail on failure", async () => {
    stubFetch(Response.json({ detail: "Период не может превышать 366 дней." }, { status: 422 }));

    await expect(exportAnalyticsCsv(QUERY)).rejects.toMatchObject({
      status: 422,
      message: "Период не может превышать 366 дней.",
    });
  });

  it("exportAnalyticsCsv raises ApiError on a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fail"))));

    await expect(exportAnalyticsCsv(QUERY)).rejects.toBeInstanceOf(ApiError);
  });

  it("exportAnalyticsCsv notifies the session-expiry listeners on 401", async () => {
    stubFetch(Response.json({ detail: "Требуется вход." }, { status: 401 }));
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);

    await expect(exportAnalyticsCsv(QUERY)).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("exportAnalyticsCsv never resolves for a non-2xx response", async () => {
    stubFetch(new Response("oops", { status: 500 }));

    await expect(exportAnalyticsCsv(QUERY)).rejects.toBeInstanceOf(ApiError);
  });
});
