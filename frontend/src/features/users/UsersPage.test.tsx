/**
 * Tests for the admin UsersPage: list, create, edit (role/full name),
 * deactivate with confirmation, password reset with confirmation, unlock,
 * 403 handling and the self-deactivation restriction. The API layer is
 * mocked; security itself is enforced by the backend.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { ToastProvider } from "../../design-system/components/Toast";
import type { User } from "../../types";
import { UsersPage } from "./UsersPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listUsers: vi.fn(),
    createUser: vi.fn(),
    getUser: vi.fn(),
    updateUser: vi.fn(),
    unlockUser: vi.fn(),
  };
});

import * as api from "../../api";

const ADMIN: User = {
  id: "11111111-1111-1111-1111-111111111111",
  username: "admin",
  full_name: "Администратор Системный",
  role: "admin",
  is_active: true,
  locked_until: null,
  last_login_at: "2026-09-20T09:00:00Z",
  created_at: "2026-09-01T08:00:00Z",
};

const HR_USER: User = {
  id: "22222222-2222-2222-2222-222222222222",
  username: "hr.petrova",
  full_name: "Петрова Анна",
  role: "hr",
  is_active: true,
  locked_until: null,
  last_login_at: null,
  created_at: "2026-09-02T08:00:00Z",
};

const INACTIVE_HR: User = {
  id: "33333333-3333-3333-3333-333333333333",
  username: "hr.staraya",
  full_name: "Старая Мария",
  role: "hr",
  is_active: false,
  locked_until: null,
  last_login_at: "2026-09-05T10:00:00Z",
  created_at: "2026-09-03T08:00:00Z",
};

const LOCKED_MANAGER: User = {
  id: "44444444-4444-4444-4444-444444444444",
  username: "mgr.sidorov",
  full_name: "Сидоров Пётр",
  role: "manager",
  is_active: true,
  locked_until: "2099-01-01T00:00:00Z",
  last_login_at: "2026-09-19T12:00:00Z",
  created_at: "2026-09-04T08:00:00Z",
};

const CURRENT_HR: User = {
  id: "55555555-5555-5555-5555-555555555555",
  username: "hr.current",
  full_name: "Текущая Эйчар",
  role: "hr",
  is_active: true,
  locked_until: null,
  last_login_at: null,
  created_at: "2026-09-06T08:00:00Z",
};

function userList(): User[] {
  return [ADMIN, HR_USER, INACTIVE_HR, LOCKED_MANAGER];
}

function renderPage(currentUser: User = ADMIN) {
  return render(
    <ToastProvider>
      <UsersPage currentUser={currentUser} />
    </ToastProvider>,
  );
}

async function openEditFor(rowName: RegExp) {
  const row = screen.getByRole("row", { name: rowName });
  await userEvent.click(within(row).getByRole("button", { name: "Редактировать" }));
  return row;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listUsers).mockResolvedValue({
    items: userList(),
    total: userList().length,
    limit: 200,
    offset: 0,
  });
  vi.mocked(api.createUser).mockImplementation(async (input) => ({
    id: "99999999-9999-9999-9999-999999999999",
    username: input.username,
    full_name: input.full_name,
    role: input.role as User["role"],
    is_active: true,
    locked_until: null,
    last_login_at: null,
    created_at: "2026-09-25T09:00:00Z",
  }));
  vi.mocked(api.updateUser).mockImplementation(async (userId, input) => {
    const base = userList().find((user) => user.id === userId) ?? HR_USER;
    return {
      ...base,
      ...input,
    };
  });
  vi.mocked(api.unlockUser).mockResolvedValue({ ...LOCKED_MANAGER, locked_until: null });
});

describe("UsersPage", () => {
  it("renders the user list with roles, statuses and dates", async () => {
    renderPage();

    expect(await screen.findByText("Петрова Анна")).toBeInTheDocument();
    expect(screen.getByText("hr.petrova")).toBeInTheDocument();
    expect(screen.getByText("Сидоров Пётр")).toBeInTheDocument();

    const hrRow = screen.getByRole("row", { name: /hr\.petrova/ });
    expect(within(hrRow).getByText("HR")).toBeInTheDocument();
    expect(within(hrRow).getByText("Активна")).toBeInTheDocument();
    expect(within(hrRow).getByText("никогда")).toBeInTheDocument();
    expect(
      within(hrRow).getByText(new Date("2026-09-02T08:00:00Z").toLocaleString("ru-RU")),
    ).toBeInTheDocument();

    const inactiveRow = screen.getByRole("row", { name: /hr\.staraya/ });
    expect(within(inactiveRow).getByText("Отключена")).toBeInTheDocument();

    const lockedRow = screen.getByRole("row", { name: /mgr\.sidorov/ });
    expect(within(lockedRow).getByText(/Заблокирована до/)).toBeInTheDocument();
    expect(
      within(lockedRow).getByRole("button", { name: "Разблокировать" }),
    ).toBeInTheDocument();

    const activeRow = screen.getByRole("row", { name: /hr\.petrova/ });
    expect(
      within(activeRow).queryByRole("button", { name: "Разблокировать" }),
    ).not.toBeInTheDocument();
  });

  it("creates a user with username, full name, role and password", async () => {
    const user = userEvent;
    renderPage();

    await screen.findByText("Петрова Анна");
    await user.click(screen.getByRole("button", { name: "Добавить пользователя" }));

    await user.type(screen.getByLabelText(/Имя пользователя/), "hr.nova");
    await user.type(screen.getByLabelText("ФИО"), "Нова Мария");
    await user.selectOptions(screen.getByLabelText(/Роль/), "manager");
    await user.type(screen.getByLabelText(/^Пароль/), "Str0ng-Pass-2026");
    await user.click(screen.getByRole("button", { name: "Создать пользователя" }));

    expect(await screen.findByText("Пользователь hr.nova создан.")).toBeInTheDocument();
    expect(api.createUser).toHaveBeenCalledWith({
      username: "hr.nova",
      full_name: "Нова Мария",
      role: "manager",
      password: "Str0ng-Pass-2026",
    });
    // Список перезагружен после создания: initial load + reload.
    expect(api.listUsers).toHaveBeenCalledTimes(2);
  });

  it("edits role and full name of a user", async () => {
    renderPage();
    await screen.findByText("Петрова Анна");

    await openEditFor(/hr\.petrova/);
    expect(await screen.findByText("Редактирование: hr.petrova")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText(/Роль/), "admin");
    const fio = screen.getByLabelText("ФИО");
    await userEvent.clear(fio);
    await userEvent.type(fio, "Петрова Анна Сергеевна");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(api.updateUser).toHaveBeenCalledWith("22222222-2222-2222-2222-222222222222", {
        role: "admin",
        full_name: "Петрова Анна Сергеевна",
      }),
    );
    expect(await screen.findByText("Изменения для hr.petrova сохранены.")).toBeInTheDocument();
  });

  it("deactivates a user only after an explicit confirmation", async () => {
    renderPage();
    await screen.findByText("Петрова Анна");

    await openEditFor(/hr\.petrova/);
    const checkbox = await screen.findByLabelText("Учётная запись активна");
    expect(checkbox).toBeChecked();
    await userEvent.click(checkbox);
    expect(checkbox).not.toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    // Без подтверждения запрос не уходит.
    expect(api.updateUser).not.toHaveBeenCalled();
    expect(await screen.findByText("Отключить учётную запись?")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Отключить" }));

    await waitFor(() =>
      expect(api.updateUser).toHaveBeenCalledWith("22222222-2222-2222-2222-222222222222", {
        is_active: false,
      }),
    );
  });

  it("resets a password after confirmation and never shows it", async () => {
    renderPage();
    await screen.findByText("Петрова Анна");

    await openEditFor(/hr\.petrova/);
    const passwordInput = await screen.findByLabelText("Новый пароль (сброс пароля)");
    expect(passwordInput).toHaveAttribute("type", "password");
    await userEvent.type(passwordInput, "Str0ng-Pass-2026");

    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Сбросить пароль?")).toBeInTheDocument();
    expect(api.updateUser).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Сбросить пароль" }));

    await waitFor(() =>
      expect(api.updateUser).toHaveBeenCalledWith("22222222-2222-2222-2222-222222222222", {
        password: "Str0ng-Pass-2026",
      }),
    );
    expect(await screen.findByText(/Пароль для hr\.petrova сброшен/)).toBeInTheDocument();
    // Модалка закрыта, пароль нигде не отображается.
    expect(screen.queryByText("Редактирование: hr.petrova")).not.toBeInTheDocument();
    expect(screen.queryByText("Str0ng-Pass-2026")).not.toBeInTheDocument();
  });

  it("unlocks a locked user", async () => {
    renderPage();
    await screen.findByText("Сидоров Пётр");

    const lockedRow = screen.getByRole("row", { name: /mgr\.sidorov/ });
    await userEvent.click(within(lockedRow).getByRole("button", { name: "Разблокировать" }));

    await waitFor(() =>
      expect(api.unlockUser).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444"),
    );
    expect(
      await screen.findByText("Блокировка учётной записи mgr.sidorov снята."),
    ).toBeInTheDocument();
  });

  it("shows the permission-denied state when the backend answers 403", async () => {
    vi.mocked(api.listUsers).mockRejectedValueOnce(
      new ApiError(403, "Недостаточно прав."),
    );
    renderPage();

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить пользователя" })).not.toBeInTheDocument();
  });

  it("forbids deactivating the administrator's own account in the UI", async () => {
    renderPage();
    await screen.findByText("Администратор Системный");

    await openEditFor(/Системный/);
    const checkbox = await screen.findByLabelText("Учётная запись активна");
    expect(checkbox).toBeDisabled();
    expect(
      screen.getByText("Нельзя отключить собственную учётную запись."),
    ).toBeInTheDocument();

    // Попытка клика по заблокированному чекбоксу ничего не меняет.
    await userEvent.click(checkbox);
    expect(checkbox).toBeChecked();

    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(api.updateUser).not.toHaveBeenCalled());
  });

  it("surfaces a backend rejection as a readable Russian message", async () => {
    vi.mocked(api.updateUser).mockRejectedValueOnce(
      new ApiError(400, "Нельзя отключить собственную учётную запись."),
    );
    renderPage();
    await screen.findByText("Петрова Анна");

    await openEditFor(/hr\.petrova/);
    await userEvent.click(await screen.findByLabelText("Учётная запись активна"));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await userEvent.click(await screen.findByRole("button", { name: "Отключить" }));

    expect(
      await screen.findByText("Нельзя отключить собственную учётную запись."),
    ).toBeInTheDocument();
    // Модалка остаётся открытой — изменения не потеряны молча.
    expect(screen.getByText("Редактирование: hr.petrova")).toBeInTheDocument();
  });

  it("shows the permission-denied state to a non-admin without any API calls", async () => {
    renderPage(CURRENT_HR);

    expect(await screen.findByText("Недостаточно прав")).toBeInTheDocument();
    expect(api.listUsers).not.toHaveBeenCalled();
    expect(api.createUser).not.toHaveBeenCalled();
    expect(api.updateUser).not.toHaveBeenCalled();
    expect(api.unlockUser).not.toHaveBeenCalled();
  });
});
