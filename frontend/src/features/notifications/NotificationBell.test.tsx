import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AppNotification } from "../../types";
import { NotificationBell } from "./NotificationBell";

const ITEMS: AppNotification[] = Array.from({ length: 5 }, (_, i) => ({
  id: `notice-${i}`, title: `Уведомление ${i}`, body: "Подробности события",
  type: "system_alert", priority: "normal", source: "system",
  object_type: null, object_id: null, created_at: "2026-10-06T09:00:00Z",
  read_at: null, dismissed_at: null,
}));
let read: boolean;
let failRefresh: boolean;
let post: () => Promise<Response>;
const fetchMock = vi.fn<typeof fetch>();

beforeEach(() => {
  read = false;
  failRefresh = false;
  post = async () => { read = true; return new Response(null, { status: 204 }); };
  fetchMock.mockReset();
  fetchMock.mockImplementation(async (url, init) => {
    const path = String(url);
    if (path.endsWith("/mark-all-read")) {
      expect(init?.method).toBe("POST");
      return post();
    }
    if (failRefresh) return Response.json({ detail: "Недоступно" }, { status: 503 });
    if (path.endsWith("/unread-count")) return Response.json({ count: read ? 0 : 5 });
    if (path.includes("/notifications?")) {
      return Response.json({ items: ITEMS.map((item) => ({ ...item, read_at: read ? "2026-10-06T10:00:00Z" : null })), total: 5, unread_count: read ? 0 : 5, limit: 5, offset: 0 });
    }
    throw new Error(`Unexpected test endpoint: ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

async function openBell() {
  render(<NotificationBell onOpenCandidate={vi.fn()} />);
  await userEvent.click(await screen.findByRole("button", { name: "Уведомления, непрочитанных: 5" }));
  return screen.getByRole("dialog", { name: "Последние уведомления" });
}
const postCalls = () => fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/mark-all-read"));

describe("NotificationBell through the real API client", () => {
  it("POSTs mark-all-read, refreshes list/count and shows confirmed read state", async () => {
    const dialog = await openBell();
    expect(dialog.querySelectorAll(".is-unread")).toHaveLength(5);
    await userEvent.click(within(dialog).getByRole("button", { name: "Прочитать все" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Уведомления" })).toHaveTextContent("0"));
    expect(postCalls()).toHaveLength(1);
    expect(String(postCalls()[0][0])).toBe("/api/notifications/mark-all-read");
    expect(postCalls()[0][1]?.credentials).toBe("same-origin");
    expect(dialog.querySelectorAll(".is-unread")).toHaveLength(0);
    expect(within(dialog).getAllByRole("listitem")).toHaveLength(5);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/notifications?limit=5"))).toHaveLength(2);
    expect(within(dialog).getByRole("button", { name: "Прочитать все" })).toBeDisabled();
  });

  it("disables the action and rejects duplicate clicks while the request is pending", async () => {
    let finish!: (response: Response) => void;
    post = () => new Promise((resolve) => { finish = resolve; });
    const dialog = await openBell();
    const button = within(dialog).getByRole("button", { name: "Прочитать все" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent("Отмечаем…");
    expect(postCalls()).toHaveLength(1);
    read = true;
    finish(new Response(null, { status: 204 }));
    await screen.findByRole("button", { name: "Уведомления" });
  });

  it("shows an error without clearing badge/read state, then permits retry", async () => {
    post = async () => Response.json({ detail: "Ошибка" }, { status: 500 });
    const dialog = await openBell();
    await userEvent.click(within(dialog).getByRole("button", { name: "Прочитать все" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Не удалось отметить");
    expect(screen.getByRole("button", { name: "Уведомления, непрочитанных: 5" })).toBeInTheDocument();
    expect(dialog.querySelectorAll(".is-unread")).toHaveLength(5);
    post = async () => { read = true; return new Response(null, { status: 204 }); };
    await userEvent.click(within(dialog).getByRole("button", { name: "Прочитать все" }));
    await screen.findByRole("button", { name: "Уведомления" });
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("distinguishes a successful POST from a failed refresh and offers refresh", async () => {
    const dialog = await openBell();
    post = async () => { read = true; failRefresh = true; return new Response(null, { status: 204 }); };
    await userEvent.click(within(dialog).getByRole("button", { name: "Прочитать все" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Отметка сохранена, но список не обновился");
    failRefresh = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "Обновить" }));
    await screen.findByRole("button", { name: "Уведомления" });
    expect(postCalls()).toHaveLength(1);
  });

  it("supports keyboard focus, Escape and outside click", async () => {
    const dialog = await openBell();
    expect(dialog).toHaveFocus();
    await userEvent.tab();
    expect(within(dialog).getAllByRole("button")[0]).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const trigger = screen.getByRole("button", { name: "Уведомления, непрочитанных: 5" });
    expect(trigger).toHaveFocus();
    await userEvent.click(trigger);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});


it("does not let an older polling response overwrite the post-mutation read snapshot", async () => {
  let poll!: () => void;
  const originalInterval = window.setInterval.bind(window);
  vi.spyOn(window, "setInterval").mockImplementation((handler, delay) => {
    if (delay === 30_000) poll = handler as () => void;
    return originalInterval(handler, delay);
  });
  const dialog = await openBell();
  let finishOldCount!: (response: Response) => void;
  fetchMock.mockImplementationOnce(() => new Promise((resolve) => { finishOldCount = resolve; }));
  fetchMock.mockResolvedValueOnce(Response.json({ items: ITEMS, total: 5, unread_count: 5, limit: 5, offset: 0 }));
  await act(async () => poll());
  await userEvent.click(within(dialog).getByRole("button", { name: "Прочитать все" }));
  await screen.findByRole("button", { name: "Уведомления" });
  await act(async () => finishOldCount(Response.json({ count: 5 })));
  expect(screen.getByRole("button", { name: "Уведомления" })).toHaveTextContent("0");
  expect(dialog.querySelectorAll(".is-unread")).toHaveLength(0);
});
