import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { AppNotification, NotificationListPayload } from "../../types";
import { NotificationCenterPage } from "./NotificationCenterPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listNotifications: vi.fn(),
    markNotificationsRead: vi.fn(),
    markAllNotificationsRead: vi.fn(),
    dismissNotifications: vi.fn(),
    resolveNotification: vi.fn(),
    notificationDelivery: vi.fn(),
  };
});

import * as api from "../../api";

const UNREAD: AppNotification = {
  id: "11111111-1111-4111-8111-111111111111",
  type: "event_assigned",
  title: "Вам назначено событие",
  body: null,
  priority: "normal",
  source: "system",
  object_type: "candidate",
  object_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  created_at: "2026-09-07T08:00:00Z",
  read_at: null,
  dismissed_at: null,
};

function payload(items: AppNotification[]): NotificationListPayload {
  return { items, total: items.length, limit: 20, offset: 0, unread_count: items.filter((i) => !i.read_at).length };
}

const mockedList = vi.mocked(api.listNotifications);

describe("NotificationCenterPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the list with unread highlighting and accessible actions", async () => {
    mockedList.mockResolvedValue(payload([UNREAD]));
    render(
      <ToastProvider>
        <NotificationCenterPage onOpenCandidate={() => undefined} />
      </ToastProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("Вам назначено событие")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "Прочитать всё на странице" })).toBeTruthy();
    // aria-live region announces list changes politely
    expect(document.querySelector('[aria-live="polite"]')).toBeTruthy();
  });

  it("marks a notification read from the action button", async () => {
    const markRead = vi.mocked(api.markNotificationsRead).mockResolvedValue();
    mockedList.mockResolvedValue(payload([UNREAD]));
    render(
      <ToastProvider>
        <NotificationCenterPage onOpenCandidate={() => undefined} />
      </ToastProvider>,
    );
    await waitFor(() => screen.getByText("Вам назначено событие"));

    await userEvent.click(screen.getByRole("button", { name: "Прочитано" }));
    await waitFor(() => {
      expect(markRead).toHaveBeenCalledWith([UNREAD.id]);
    });
  });

  it("shows the empty state when there are no notifications", async () => {
    mockedList.mockResolvedValue(payload([]));
    render(
      <ToastProvider>
        <NotificationCenterPage onOpenCandidate={() => undefined} />
      </ToastProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("Уведомлений нет")).toBeTruthy();
    });
  });

  it("shows the error state with retry on failure", async () => {
    mockedList.mockRejectedValue(new Error("network"));
    render(
      <ToastProvider>
        <NotificationCenterPage onOpenCandidate={() => undefined} />
      </ToastProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("Не удалось загрузить данные")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: /Повторить попытку/ })).toBeTruthy();
  });

  it("navigates to a candidate only after backend access resolution", async () => {
    mockedList.mockResolvedValue(payload([UNREAD]));
    const resolve = vi.mocked(api.resolveNotification).mockResolvedValue({
      allowed: true,
      object_type: "candidate",
      object_id: UNREAD.object_id!,
    });
    const markRead = vi.mocked(api.markNotificationsRead).mockResolvedValue();
    const onOpenCandidate = vi.fn();
    render(
      <ToastProvider>
        <NotificationCenterPage onOpenCandidate={onOpenCandidate} />
      </ToastProvider>,
    );
    await waitFor(() => screen.getByText("Вам назначено событие"));

    await userEvent.click(screen.getByRole("button", { name: "Открыть" }));
    await waitFor(() => {
      expect(resolve).toHaveBeenCalledWith(UNREAD.id);
      expect(markRead).toHaveBeenCalledWith([UNREAD.id]);
      expect(onOpenCandidate).toHaveBeenCalledWith(UNREAD.object_id);
    });
  });

  it("stays put when the backend denies access to the linked object", async () => {
    mockedList.mockResolvedValue(payload([UNREAD]));
    vi.mocked(api.resolveNotification).mockResolvedValue({
      allowed: false,
      object_type: "candidate",
      object_id: UNREAD.object_id,
    });
    const onOpenCandidate = vi.fn();
    render(
      <ToastProvider>
        <NotificationCenterPage onOpenCandidate={onOpenCandidate} />
      </ToastProvider>,
    );
    await waitFor(() => screen.getByText("Вам назначено событие"));
    await userEvent.click(screen.getByRole("button", { name: "Открыть" }));
    await waitFor(() => {
      expect(onOpenCandidate).not.toHaveBeenCalled();
      expect(
        screen.getByText(/права перепроверены сервером/i),
      ).toBeTruthy();
    });
  });
});
