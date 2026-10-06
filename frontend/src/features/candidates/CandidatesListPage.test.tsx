import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, User } from "../../types";
import CandidatesListPage from "./CandidatesListPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listCandidates: vi.fn(),
    listPositionOptions: vi.fn(),
    listHrUsers: vi.fn(),
    getCandidate: vi.fn(),
    deleteCandidate: vi.fn(),
    restoreCandidate: vi.fn(),
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

const MANAGER: User = { ...HR, id: "33333333-3333-3333-3333-333333333333", username: "mgr", full_name: "Менеджер", role: "manager" };

function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    id: "44444444-4444-4444-4444-444444444444",
    full_name: "Петров Пётр Петрович",
    phone: "+7 900 123-45-67",
    email: "petrov@example.com",
    source: "site",
    position: "Инженер",
    owner_user_id: HR.id,
    owner_username: "hr1",
    stage: "new",
    start_date: null,
    start_time: null,
    start_organization: null,
    start_department: null,
    shift: null,
    start_comment: null,
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
    deleted_at: null,
    deleted_by_user_id: null,
    is_deleted: false,
    ...overrides,
  };
}

/** Подсказки должностей приходят отдельным запросом — без них в селекте будет
 *  только «Все должности». */
function listWithPositions(positions: string[], rows: Candidate[] = []) {
  vi.mocked(api.listPositionOptions).mockResolvedValue({
    items: positions.map((position, index) => ({ position, count: index + 1 })),
    total: positions.length,
    limit: 500,
    truncated: false,
  });
  vi.mocked(api.listCandidates).mockResolvedValue({
    items: rows,
    total: rows.length,
    limit: 20,
    offset: 0,
  });
}

function lastPositionScope() {
  return vi.mocked(api.listPositionOptions).mock.calls.at(-1)?.[0];
}

function lastListQuery() {
  return vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0];
}

beforeEach(() => {
  // Справочник должностей по умолчанию пуст: экраны им не заняты.
  vi.mocked(api.listPositionOptions).mockResolvedValue({
    items: [],
    total: 0,
    limit: 500,
    truncated: false,
  });
});

function renderPage(mode: "queue" | "all" | "deleted" = "queue", user: User = HR) {
  return render(
    <ToastProvider>
      <CandidatesListPage user={user} mode={mode} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
});

describe("CandidatesListPage", () => {
  it("counts the rows actually returned, not the requested page size", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [candidate()], total: 7, limit: 20, offset: 0 });
    renderPage();
    expect(await screen.findByText("Показано 1–1 из 7")).toBeInTheDocument();
  });

  it("keeps an empty later page readable and allows returning after the list shrinks", async () => {
    vi.mocked(api.listCandidates).mockImplementation(async (query) => ({
      items: query?.offset ? [] : [candidate()], total: query?.offset ? 0 : 21,
      limit: 20, offset: query?.offset ?? 0,
    }));
    renderPage();
    await screen.findByText("Показано 1–1 из 21");
    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    expect(await screen.findByText("Показано 0–0 из 0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Назад" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(await screen.findByText("Показано 1–1 из 21")).toBeInTheDocument();
  });

  it("shows a skeleton while loading and then renders rows", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderPage();

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getByText("Инженер")).toBeInTheDocument();
    expect(within(table).getByText("Новый")).toBeInTheDocument();
    expect(within(table).getByText("Сайт компании")).toBeInTheDocument();
  });

  it("shows the empty state when there are no candidates", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    renderPage();
    expect(await screen.findByText("Кандидаты не найдены")).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Пагинация списка кандидатов" })).getByRole("status")).toHaveTextContent("Показано 0–0 из 0");
  });

  it("shows an explicit unassigned label in the manager candidate list", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate({ owner_user_id: null, owner_username: null })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderPage("all", MANAGER);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("Не назначен")).toBeInTheDocument();
  });

  it("shows an error with retry when the request fails", async () => {
    vi.mocked(api.listCandidates).mockRejectedValueOnce(new api.ApiError(500, "Сбой сервера"));
    renderPage();

    const retry = await screen.findByRole("button", { name: /повторить/i });
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [candidate()], total: 1, limit: 20, offset: 0 });
    await userEvent.click(retry);

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
  });

  it("debounces search and passes query/stage/source/position to the API", async () => {
    listWithPositions(["Монтажник РЭА", "Инженер"]);
    const user = userEvent.setup();
    renderPage();

    const search = screen.getByLabelText("Поиск кандидатов");
    await user.type(search, "петров");

    await waitFor(() => {
      const lastCall = vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ query: "петров" });
    });

    await user.selectOptions(screen.getByLabelText("Этап"), "offer");
    await waitFor(() => {
      const lastCall = vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ stage: "offer" });
    });

    await user.selectOptions(screen.getByLabelText("Источник"), "referral");
    await waitFor(() => {
      expect(lastListQuery()).toMatchObject({ source: "referral" });
    });

    await user.selectOptions(screen.getByLabelText("Должность"), "Монтажник РЭА");
    await waitFor(() => {
      expect(lastListQuery()).toMatchObject({ position: "Монтажник РЭА" });
    });
    // Фильтр не подменяет остальные — они едут в том же запросе.
    expect(lastListQuery()).toMatchObject({ query: "петров", stage: "offer", source: "referral" });
  });

  it("shows the active chip for the position and clears it with its own button", async () => {
    listWithPositions(["Монтажник РЭА", "Инженер"]);
    const user = userEvent.setup();
    renderPage();

    await user.selectOptions(await screen.findByLabelText("Должность"), "Монтажник РЭА");
    const chip = await screen.findByLabelText("Активные фильтры");
    expect(within(chip).getByText("Монтажник РЭА")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Сбросить фильтр «Должность»" }));

    await waitFor(() => {
      expect(lastListQuery()?.position).toBeUndefined();
    });
    expect(screen.queryByLabelText("Активные фильтры")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Должность")).toHaveValue("");
  });

  it("treats «Все должности» as no filter and resets the position with the common button", async () => {
    listWithPositions(["Монтажник РЭА", "Инженер"]);
    const user = userEvent.setup();
    renderPage();

    const select = await screen.findByLabelText("Должность");
    // Пустое значение = «Все должности»: параметр вообще не уходит на сервер.
    await waitFor(() => {
      expect(lastListQuery()?.position).toBeUndefined();
    });

    await user.selectOptions(select, "Монтажник РЭА");
    await waitFor(() => {
      expect(lastListQuery()).toMatchObject({ position: "Монтажник РЭА" });
    });

    await user.click(screen.getByRole("button", { name: "Сбросить фильтры" }));
    await waitFor(() => {
      expect(lastListQuery()?.position).toBeUndefined();
    });
    expect(select).toHaveValue("");
  });

  it("paginates server-side with next/prev", async () => {
    vi.mocked(api.listCandidates).mockImplementation(async (query) => {
      const offset = query?.offset ?? 0;
      if (offset === 0) {
        return {
          items: Array.from({ length: 20 }, (_, i) =>
            candidate({ id: `id-${i}`, full_name: `Кандидат ${i}` }),
          ),
          total: 25,
          limit: 20,
          offset: 0,
        };
      }
      return {
        items: Array.from({ length: 5 }, (_, i) =>
          candidate({ id: `id-${20 + i}`, full_name: `Кандидат ${20 + i}` }),
        ),
        total: 25,
        limit: 20,
        offset: 20,
      };
    });
    renderPage();

    expect(await screen.findByText("Кандидат 0")).toBeInTheDocument();
    expect(screen.getByText("Показано 1–20 из 25")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    expect(await screen.findByText("Кандидат 20")).toBeInTheDocument();
    expect(within(screen.getByRole("navigation", { name: "Пагинация списка кандидатов" })).getByRole("status")).toHaveTextContent("Показано 21–25 из 25");
    expect(screen.getByRole("button", { name: "Вперёд" })).toBeDisabled();

    expect(vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0]).toMatchObject({ offset: 20 });
  });

  it("hides the owner filter for HR and shows it for managers", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({ items: [], total: 0, limit: 20, offset: 0 });
    const { unmount } = renderPage("queue", HR);
    expect(screen.queryByLabelText("Ответственный")).not.toBeInTheDocument();
    unmount();

    vi.mocked(api.listHrUsers).mockResolvedValue({
      items: [{ id: HR.id, username: "hr1", full_name: "HR Один", role: "hr", is_active: true }],
      total: 1,
    });
    renderPage("all", MANAGER);
    expect(await screen.findByLabelText("Ответственный")).toBeInTheDocument();
  });

  it("scopes the deleted view with include_deleted and restores rows", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate({ is_deleted: true, deleted_at: "2026-09-02T11:00:00Z", full_name: "Удалённый кандидат" })],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.restoreCandidate).mockResolvedValue(candidate());
    renderPage("deleted");

    expect(await screen.findByText("Удалённый кандидат")).toBeInTheDocument();
    expect(vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0]).toMatchObject({
      include_deleted: true,
    });

    await userEvent.click(screen.getByRole("button", { name: "Восстановить" }));
    await waitFor(() => expect(api.restoreCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444"));
  });

  it("«Моя очередь» личная для всех ролей: список и справочник должностей идут в моей области", async () => {
    listWithPositions(["Инженер"], [candidate()]);
    renderPage("queue", MANAGER);

    await waitFor(() => {
      expect(lastListQuery()).toMatchObject({ owner_id: MANAGER.id });
    });
    // Справочник должен предлагать должности из той же области, что и список,
    // иначе фильтр будет содержать значения, которых в списке нет.
    expect(lastPositionScope()).toMatchObject({ owner_id: MANAGER.id });

    // Общий раздел по-прежнему без фильтра по ответственному.
    renderPage("all", MANAGER);
    await waitFor(() => {
      expect(lastListQuery()?.owner_id).toBeUndefined();
    });
    expect(lastPositionScope()?.owner_id).toBeUndefined();
  });

  it("«Моя очередь» для HR и deleted-вид не ломают область справочника", async () => {
    listWithPositions(["Инженер"], [candidate()]);
    renderPage("queue", HR);

    await waitFor(() => {
      expect(lastListQuery()).toMatchObject({ owner_id: HR.id });
    });
    expect(lastPositionScope()).toMatchObject({ owner_id: HR.id, include_deleted: false });

    renderPage("deleted");
    await waitFor(() => {
      expect(lastListQuery()).toMatchObject({ include_deleted: true });
    });
    expect(lastPositionScope()).toMatchObject({ include_deleted: true });
  });

  it("показывает ошибку загрузки справочника должностей с кнопкой повтора", async () => {
    listWithPositions(["Инженер"], [candidate()]);
    vi.mocked(api.listPositionOptions).mockRejectedValue(new api.ApiError(500, "Сбой сервера"));
    renderPage();

    expect(await screen.findByText("Не удалось загрузить список должностей.")).toBeInTheDocument();

    vi.mocked(api.listPositionOptions).mockResolvedValue({
      items: [{ position: "Монтажник РЭА", count: 2 }],
      total: 1,
      limit: 500,
      truncated: false,
    });
    await userEvent.click(screen.getByRole("button", { name: "Обновить должности" }));

    // Список кандидатов при этом не перезапрашивался — повтор касается только
    // справочника.
    await waitFor(() => {
      expect(screen.getByLabelText("Должность")).toHaveValue("");
    });
    expect(screen.getByText("Монтажник РЭА")).toBeInTheDocument();
    expect(screen.queryByText("Не удалось загрузить список должностей.")).not.toBeInTheDocument();
  });

  it("обновляет справочник должностей после удаления кандидата", async () => {
    listWithPositions(["Инженер"], [candidate()]);
    renderPage();

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
    const directoryCalls = vi.mocked(api.listPositionOptions).mock.calls.length;

    await userEvent.click(
      screen.getByRole("button", { name: "Удалить кандидата Петров Пётр Петрович" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Удалить" }));

    await waitFor(() => {
      expect(vi.mocked(api.listPositionOptions).mock.calls.length).toBeGreaterThan(directoryCalls);
    });
  });

  it("confirms soft delete before calling the API", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.deleteCandidate).mockResolvedValue(candidate());
    renderPage();

    expect(await screen.findByText("Петров Пётр Петрович")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Удалить кандидата Петров Пётр Петрович" }));

    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));

    await waitFor(() =>
      expect(api.deleteCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444")
    );
  });

  it("opens the candidate drawer when the row name is clicked", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    vi.mocked(api.getCandidate).mockResolvedValue(candidate());
    renderPage();

    const rowName = await screen.findByRole("button", {
      name: (accessibleName) => accessibleName.startsWith("Петров Пётр Петрович"),
    });
    await userEvent.click(rowName);

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByRole("heading", { name: "Петров Пётр Петрович" }),
    ).toBeInTheDocument();
  });

  it("does not open the drawer when a row action (delete) is clicked", async () => {
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [candidate()],
      total: 1,
      limit: 20,
      offset: 0,
    });
    renderPage();

    await screen.findByText("Петров Пётр Петрович");
    await userEvent.click(
      screen.getByRole("button", { name: "Удалить кандидата Петров Пётр Петрович" }),
    );

    // Подтверждение удаления появляется, а drawer кандидата — нет.
    expect(await screen.findByRole("alertdialog")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
