import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  DuplicateCandidateError,
  createCandidate,
  createCandidateInteraction,
  deleteCandidate,
  listCandidateInteractions,
  listCandidateTransfers,
  listCandidates,
  listHrUsers,
  login,
  logout,
  onUnauthorized,
  readCsrfCookie,
  restoreCandidate,
  transferCandidate,
} from "./api";

const API_BASE = "/api";

afterEach(() => {
  vi.unstubAllGlobals();
  document.cookie = "hrm_csrf=; Max-Age=0";
});

function stubFetch(response: Response): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("API client", () => {
  it("sends credentials and posts JSON for login", async () => {
    const fetchMock = stubFetch(
      Response.json({ user: { username: "hr1" }, csrf_token: "tok" }, { status: 200 })
    );

    const result = await login("hr1", "pw");

    expect(result.csrf_token).toBe("tok");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/auth/login");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("same-origin");
    expect(JSON.parse(init?.body as string)).toEqual({
      username: "hr1",
      password: "pw",
    });
  });

  it("attaches the CSRF header from the cookie on mutations", async () => {
    document.cookie = "hrm_csrf=abc123; path=/";
    const fetchMock = stubFetch(new Response(null, { status: 200 }));

    await logout();

    const init = fetchMock.mock.calls[0][1];
    expect((init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("abc123");
  });

  it("raises ApiError with the backend detail on failure", async () => {
    stubFetch(
      Response.json({ detail: "Неверное имя пользователя или пароль." }, { status: 401 })
    );

    await expect(login("x", "y")).rejects.toMatchObject({
      status: 401,
      message: "Неверное имя пользователя или пароль.",
    });
  });

  it("raises a network ApiError when fetch throws", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("fail")))
    );
    await expect(login("x", "y")).rejects.toBeInstanceOf(ApiError);
  });

  it("reads the CSRF cookie", () => {
    document.cookie = "hrm_csrf=zzz; path=/";
    expect(readCsrfCookie()).toBe("zzz");
  });
});

// --- Candidates database ----------------------------------------------------

const candidate = {
  id: "3c2f8e2a-0000-4000-8000-000000000001",
  full_name: "Петров Пётр Петрович",
  phone: "+7 900 123-45-67",
  email: "petrov@example.com",
  source: "site",
  position: "Инженер",
  owner_user_id: "3c2f8e2a-0000-4000-8000-000000000002",
  owner_username: "hr1",
  stage: "new",
  created_at: "2026-09-02T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

describe("Candidates API client", () => {
  it("listCandidates serializes search/filter/sort/pagination params", async () => {
    const fetchMock = stubFetch(Response.json({ items: [], total: 0, limit: 50, offset: 0 }));
    await listCandidates({
      query: "петров",
      stage: "offer",
      source: "referral",
      owner_id: "owner-1",
      sort: "stage",
      direction: "desc",
      limit: 10,
      offset: 20,
    });
    const [url] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      `${API_BASE}/candidates?query=%D0%BF%D0%B5%D1%82%D1%80%D0%BE%D0%B2&stage=offer&source=referral&owner_id=owner-1&sort=stage&direction=desc&limit=10&offset=20`
    );
  });

  it("createCandidate posts the payload and returns the candidate", async () => {
    const fetchMock = stubFetch(Response.json(candidate, { status: 201 }));
    const result = await createCandidate({
      full_name: candidate.full_name,
      phone: candidate.phone,
      email: candidate.email,
      source: "site",
      position: "Инженер",
    });
    expect(result.id).toBe(candidate.id);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${API_BASE}/candidates`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toMatchObject({ full_name: candidate.full_name });
  });

  it("createCandidate turns a 409 into DuplicateCandidateError with matches", async () => {
    const detail = {
      message:
        "Кандидат с таким телефоном или email уже существует. " +
        "Для создания точного дубликата повторите запрос с confirm_duplicate=true.",
      duplicates: [candidate],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ detail }, { status: 409 }))
    );

    await expect(
      createCandidate({ full_name: "Дубль", phone: candidate.phone! })
    ).rejects.toBeInstanceOf(DuplicateCandidateError);
    try {
      await createCandidate({ full_name: "Дубль", phone: candidate.phone! });
    } catch (error) {
      const duplicateError = error as DuplicateCandidateError;
      expect(duplicateError.status).toBe(409);
      expect(duplicateError.duplicates).toHaveLength(1);
      expect(duplicateError.duplicates[0].id).toBe(candidate.id);
      expect(duplicateError.message).toBe(detail.message);
    }
  });

  it("deleteCandidate, restoreCandidate and interactions use mutating methods", async () => {
    document.cookie = "hrm_csrf=tok123; path=/";
    const fetchMock = stubFetch(Response.json(candidate));

    await deleteCandidate(candidate.id);
    await restoreCandidate(candidate.id);
    await createCandidateInteraction(candidate.id, { type: "call", comment: "Звонок" });
    await listCandidateInteractions(candidate.id);

    const calls = fetchMock.mock.calls;
    expect(String(calls[0][0])).toBe(`${API_BASE}/candidates/${candidate.id}`);
    expect(calls[0][1]?.method).toBe("DELETE");
    expect(String(calls[1][0])).toBe(`${API_BASE}/candidates/${candidate.id}/restore`);
    expect(calls[1][1]?.method).toBe("POST");
    expect(String(calls[2][0])).toBe(`${API_BASE}/candidates/${candidate.id}/interactions`);
    expect(calls[2][1]?.method).toBe("POST");
    expect(JSON.parse(calls[2][1]?.body as string)).toEqual({
      type: "call",
      comment: "Звонок",
    });
    expect(String(calls[3][0])).toBe(
      `${API_BASE}/candidates/${candidate.id}/interactions?limit=50&offset=0`
    );
    expect(calls[3][1]?.method).toBe("GET");
    for (const [, init] of calls.slice(0, 3)) {
      expect((init?.headers as Record<string, string>)?.["X-CSRF-Token"]).toBe("tok123");
    }
    expect((calls[3][1]?.headers as Record<string, string>)?.["X-CSRF-Token"]).toBeUndefined();
  });
});

// --- Phase 4: HR directory, transfers, session-expiry notifications ---------

describe("Phase 4 API surface", () => {
  it("listHrUsers reads the HR directory endpoint", async () => {
    const fetchMock = stubFetch(Response.json({ items: [], total: 0 }));
    await listHrUsers();
    expect(String(fetchMock.mock.calls[0][0])).toBe(`${API_BASE}/admin/users/hr`);
  });

  it("transferCandidate posts reason and new owner", async () => {
    const fetchMock = stubFetch(Response.json({ transfer: {}, candidate: {} }));
    await transferCandidate("c-1", {
      new_owner_user_id: "u-2",
      reason: "Перераспределение",
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(`${API_BASE}/candidates/c-1/transfer`);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      new_owner_user_id: "u-2",
      reason: "Перераспределение",
    });
  });

  it("listCandidateTransfers paginates the history endpoint", async () => {
    const fetchMock = stubFetch(Response.json({ items: [], total: 0, limit: 20, offset: 0 }));
    await listCandidateTransfers("c-1", 20, 40);
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `${API_BASE}/candidates/c-1/transfers?limit=20&offset=40`
    );
  });

  it("emits onUnauthorized for 401 responses outside login", async () => {
    stubFetch(Response.json({ detail: "Сессия истекла." }, { status: 401 }));
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);

    await expect(listCandidates({})).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    await expect(listCandidates({})).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1); // unsubscribed
  });
});
