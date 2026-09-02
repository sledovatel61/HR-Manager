import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, login, logout, readCsrfCookie } from "./api";

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
