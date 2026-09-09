import { afterEach, describe, expect, it, vi } from "vitest";
import { claimFirstRun, fetchFirstRunState, fetchOpsReadiness, setFirstRunPassword } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "hrm_csrf=; Max-Age=0";
});

function stubFetch(payload: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => Response.json(payload, { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("first-run API client (phase 12)", () => {
  it("GETs the public first-run state without credentials in the URL", async () => {
    const fetchMock = stubFetch({
      pending: true,
      pending_work_role: "hr",
      pending_expires_in_seconds: 900,
      fresh_install: true,
      pilot_owner_exists: false,
    });
    const state = await fetchFirstRunState();
    expect(state.pending).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/setup/first-run/state");
    expect(init?.method).toBe("GET");
    expect(String(url)).not.toMatch(/code|password/i);
  });

  it("normalizes and POSTs the pairing code (never in a URL)", async () => {
    const fetchMock = stubFetch({ user: { username: "u" }, csrf_token: "t", must_set_password: true });
    const result = await claimFirstRun(" k7m2qx ");
    expect(result.must_set_password).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/setup/first-run/claim");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({ code: "K7M2QX" });
  });

  it("surfaces backend claim errors as ApiError with the server message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "Пара не найдена или код неверен." }, { status: 400 })
      )
    );
    await expect(claimFirstRun("AAAAAA")).rejects.toThrow(/код неверен/);
  });

  it("PUTs the chosen password with the CSRF header from the cookie", async () => {
    document.cookie = "hrm_csrf=csrf-1; path=/";
    const fetchMock = stubFetch({ id: "1", username: "ivanov-pilot-1" });
    await setFirstRunPassword("Str0ng-Pilot-2026");
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/setup/first-run/password");
    expect(init?.method).toBe("PUT");
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-1");
    expect(JSON.parse(init?.body as string)).toEqual({ password: "Str0ng-Pilot-2026" });
  });

  it("reads the ops readiness snapshot", async () => {
    const fetchMock = stubFetch({ status: "ok", release_sha: "aaaa", database: { status: "ok" } });
    const ops = await fetchOpsReadiness();
    expect(ops.status).toBe("ok");
    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/ops/status");
  });
});
