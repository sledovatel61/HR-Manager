/** UI-тесты раздела «Обновления» (Phase 13): loading/offline/error/retry/
 * 403, admin happy path, честные итоги updated/rolled_back, доступность. */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { UpdateStatus } from "../../types";
import { UpdateChannelPage } from "./UpdateChannelPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    fetchUpdateStatus: vi.fn(),
    checkUpdates: vi.fn(),
    downloadUpdate: vi.fn(),
    requestUpdateInstall: vi.fn(),
  };
});

import * as api from "../../api";

function statusOf(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    state: "up_to_date",
    installed_version: "0.13.0",
    installed_release_sha: "3".repeat(40),
    available_version: null,
    available_release_sha: null,
    available_published_at: null,
    notes_ru: null,
    download_progress: null,
    last_check_at: "2026-09-10T10:00:00Z",
    last_check_ok: true,
    error_code: null,
    last_result: null,
    channel_configured: true,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <ToastProvider>
      <UpdateChannelPage />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("UpdateChannelPage", () => {
  it("показывает загрузку, затем состояние up_to_date", async () => {
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(statusOf());
    renderPage();
    expect(screen.getByRole("status")).toBeTruthy();
    await waitFor(() => {
      expect(vi.mocked(api.fetchUpdateStatus)).toHaveBeenCalled();
    });
    expect(await screen.findByText("Установлена актуальная версия")).toBeTruthy();
    expect(screen.getByText("0.13.0")).toBeTruthy();
  });

  it("офлайн/ошибка: честное сообщение и кнопка повтора", async () => {
    vi.mocked(api.fetchUpdateStatus).mockRejectedValue(new Error("network"));
    renderPage();
    expect(
      await screen.findByText(/Не удалось получить состояние канала обновлений/)
    ).toBeTruthy();
    const retry = screen.getByRole("button", { name: /Повторить попытку/ });
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(statusOf());
    await userEvent.click(retry);
    expect(await screen.findByText("Установлена актуальная версия")).toBeTruthy();
  });

  it("403: показывает сообщение о правах", async () => {
    vi.mocked(api.fetchUpdateStatus).mockRejectedValue({ status: 403 });
    renderPage();
    expect(
      await screen.findByText("Недостаточно прав для управления каналом обновлений.")
    ).toBeTruthy();
  });

  it("admin happy path: проверка → доступна → скачать → установить", async () => {
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(statusOf());
    vi.mocked(api.checkUpdates).mockResolvedValue(
      statusOf({
        state: "available",
        available_version: "0.14.0",
        available_release_sha: "2".repeat(40),
        available_published_at: "2026-09-11T09:00:00Z",
        notes_ru: "Новый интерфейс и исправления.",
      })
    );
    vi.mocked(api.downloadUpdate).mockResolvedValue(
      statusOf({
        state: "ready",
        available_version: "0.14.0",
        available_release_sha: "2".repeat(40),
        download_progress: 100,
      })
    );
    vi.mocked(api.requestUpdateInstall).mockResolvedValue({ state: "installing", job_id: "job1", message: null });

    renderPage();
    await screen.findByText("Установлена актуальная версия");

    const check = screen.getByRole("button", { name: "Проверить обновления" });
    await userEvent.click(check);
    expect(await screen.findByText("Доступна новая версия")).toBeTruthy();
    expect(screen.getByText(/0\.14\.0/)).toBeTruthy();
    expect(screen.getByText("Новый интерфейс и исправления.")).toBeTruthy();

    const download = screen.getByRole("button", { name: "Скачать" });
    await userEvent.click(download);
    expect(await screen.findByText("Пакет проверен и готов к установке")).toBeTruthy();
    expect(
      screen.getByText(/Перед установкой будет создан проверенный шифрованный бэкап/)
    ).toBeTruthy();

    const install = screen.getByRole("button", { name: "Установить" });
    await userEvent.click(install);
    expect(await screen.findByText("Идёт установка…")).toBeTruthy();
    expect(
      screen.getByText(/автоматически вернётся к прежней версии/i)
    ).toBeTruthy();
  });

  it("итог rolled_back показывается честно, без «успешно»", async () => {
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(
      statusOf({ state: "failed", last_result: "rolled_back", error_code: "update_failed" })
    );
    renderPage();
    expect(
      await screen.findByText(
        /Установка не удалась — приложение автоматически вернулось к прежней рабочей версии/
      )
    ).toBeTruthy();
    expect(screen.queryByText(/успешно/i)).toBeNull();
  });

  it("manual_action_required и error_code отображаются понятно", async () => {
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(
      statusOf({ state: "manual_action_required", error_code: "manual_action_required" })
    );
    renderPage();
    expect(await screen.findByText("Требуется ручное обновление")).toBeTruthy();
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
  });

  it("канал не настроен — честная пометка", async () => {
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(
      statusOf({ state: "failed", error_code: "channel_not_configured", channel_configured: false })
    );
    renderPage();
    expect(await screen.findByText("Канал обновлений не настроен в конфигурации сервера.")).toBeTruthy();
  });

  it("кнопки доступны с клавиатуры и имеют понятные подписи", async () => {
    vi.mocked(api.fetchUpdateStatus).mockResolvedValue(
      statusOf({
        state: "available",
        available_version: "0.14.0",
        available_release_sha: "2".repeat(40),
      })
    );
    renderPage();
    const check = await screen.findByRole("button", { name: "Проверить обновления" });
    check.focus();
    expect(document.activeElement).toBe(check);
    const download = screen.getByRole("button", { name: "Скачать" });
    const install = screen.getByRole("button", { name: "Установить" });
    expect((install as HTMLButtonElement).disabled).toBe(true);
    expect((download as HTMLButtonElement).disabled).toBe(false);
  });
});
