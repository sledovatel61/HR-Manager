import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import { STAGE_LABELS, type Candidate, type CandidateStage, type User } from "../../types";
import KanbanPage from "./KanbanPage";
import { edgeScrollDirection } from "./boardScroll";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listCandidates: vi.fn(),
    listHrUsers: vi.fn(),
    updateCandidate: vi.fn(),
    fetchWorkScheduleSuggestions: vi.fn(),
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

function candidate(stage: CandidateStage, id = "44444444-4444-4444-4444-444444444444"): Candidate {
  return {
    id,
    full_name: `Кандидат ${stage}`,
    phone: null,
    email: null,
    source: "site",
    position: "Инженер",
    owner_user_id: HR.id,
    owner_username: "hr1",
    stage,
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
  };
}

/** Only per-column requests (have `stage`) — the board also asks once for the
 *  position-filter suggestions. */
function columnCalls() {
  return vi
    .mocked(api.listCandidates)
    .mock.calls.map(([query]) => query)
    .filter((query) => query?.stage !== undefined);
}

function renderKanban() {
  return render(
    <ToastProvider>
      <KanbanPage user={HR} />
    </ToastProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
  vi.mocked(api.fetchWorkScheduleSuggestions).mockResolvedValue({
    organizations: [],
    departments: [],
    shifts: [],
  });
  vi.mocked(api.listCandidates).mockImplementation(async (query) => {
    const stage = query?.stage;
    const items = stage === "new" ? [candidate("new")] : [];
    return { items, total: items.length, limit: 20, offset: 0 };
  });
});

describe("KanbanPage", () => {
  it("renders all funnel columns from CANDIDATE_STAGE_ORDER including «Вышел»", async () => {
    renderKanban();
    expect(await screen.findByText("Кандидат new")).toBeInTheDocument();

    // All 11 columns exist, labelled by STAGE_LABELS.
    expect(screen.getByRole("listitem", { name: /Новый/ })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Вышел/ })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Испытательный срок/ })).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: /Отказ/ })).toBeInTheDocument();
  });

  it("changes the stage via the keyboard-accessible select and calls PATCH", async () => {
    vi.mocked(api.updateCandidate).mockResolvedValue(candidate("offer"));
    renderKanban();

    await screen.findByText("Кандидат new");
    const newColumn = screen.getByRole("listitem", { name: /Новый/ });
    await userEvent.selectOptions(within(newColumn).getByLabelText("Изменить этап: Кандидат new"), "offer");

    await waitFor(() =>
      expect(api.updateCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444", {
        stage: "offer",
      })
    );
  });

  it("rolls back the optimistic move when PATCH fails", async () => {
    vi.mocked(api.updateCandidate).mockRejectedValue(new api.ApiError(500, "Сбой"));
    renderKanban();

    await screen.findByText("Кандидат new");
    const newColumn = screen.getByRole("listitem", { name: /Новый/ });
    await userEvent.selectOptions(
      within(newColumn).getByLabelText("Изменить этап: Кандидат new"),
      "hired"
    );

    await waitFor(() =>
      expect(within(newColumn).getByText("Кандидат new")).toBeInTheDocument()
    );
    expect(api.updateCandidate).toHaveBeenCalled();
  });

  it("loads more per column without requesting the whole board at once", async () => {
    vi.mocked(api.listCandidates).mockImplementation(async (query) => {
      const stage = query?.stage;
      const offset = query?.offset ?? 0;
      if (stage === "new") {
        if (offset === 0) {
          return {
            items: Array.from({ length: 20 }, (_, i) => ({
              ...candidate("new", `id-new-${i}`),
              full_name: `Кандидат new-${i}`,
            })),
            total: 25,
            limit: 20,
            offset: 0,
          };
        }
        return {
          items: Array.from({ length: 5 }, (_, i) => ({
            ...candidate("new", `id-new-${20 + i}`),
            full_name: `Кандидат new-${20 + i}`,
          })),
          total: 25,
          limit: 20,
          offset: 20,
        };
      }
      return { items: [], total: 0, limit: 20, offset: 0 };
    });
    renderKanban();

    expect(await screen.findByText("Кандидат new-0")).toBeInTheDocument();
    // Besides the column pages the board makes one request for the
    // «Должность» filter suggestions — count only per-column paging.
    expect(columnCalls()).toHaveLength(11);

    await userEvent.click(screen.getByRole("button", { name: "Показать ещё (5)" }));
    await waitFor(() => expect(columnCalls()).toHaveLength(12));
    expect(columnCalls().at(-1)).toMatchObject({
      stage: "new",
      offset: 20,
    });
  });

  it("applies the «Должность» filter to every funnel column, not just one", async () => {
    vi.mocked(api.listCandidates).mockImplementation(async (query) => {
      if (query?.limit === 100) {
        return {
          items: [
            { ...candidate("new", "s-1"), position: "Монтажник РЭА" },
            { ...candidate("new", "s-2"), position: "Инженер" },
          ],
          total: 2,
          limit: 100,
          offset: 0,
        };
      }
      const items = query?.stage === "new" ? [candidate("new")] : [];
      return { items, total: items.length, limit: 20, offset: 0 };
    });
    renderKanban();
    await screen.findByText("Кандидат new");

    const before = columnCalls().length;
    await userEvent.selectOptions(screen.getByLabelText("Должность"), "Монтажник РЭА");

    await waitFor(() => {
      expect(columnCalls().at(-1)).toMatchObject({ position: "Монтажник РЭА" });
    });
    const after = columnCalls().slice(before);
    // Фильтр уходит в каждую колонку воронки — иначе «останутся только
    // монтажники» выполнялось бы для одной колонки из одиннадцати.
    expect(after.length).toBeGreaterThan(1);
    expect(after.every((query) => query?.position === "Монтажник РЭА")).toBe(true);
  });

  it("labels columns with the shared STAGE_LABELS vocabulary", () => {
    expect(STAGE_LABELS.started).toBe("Вышел");
  });

  it("opens «Укажите дату выхода» when moving to «Вышел» without a date", async () => {
    vi.mocked(api.updateCandidate).mockResolvedValue(candidate("started"));
    renderKanban();

    await screen.findByText("Кандидат new");
    const newColumn = screen.getByRole("listitem", { name: /Новый/ });
    await userEvent.selectOptions(
      within(newColumn).getByLabelText("Изменить этап: Кандидат new"),
      "started"
    );

    // Сервер не примет «Вышел» без даты: сначала спрашиваем дату, PATCH ещё не ушёл.
    expect(
      await screen.findByRole("dialog", { name: "Укажите дату выхода" })
    ).toBeInTheDocument();
    expect(api.updateCandidate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/Дата выхода/), {
      target: { value: "2026-08-10" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Перевести в «Вышел»" }));

    await waitFor(() =>
      expect(api.updateCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444", {
        stage: "started",
        start_date: "2026-08-10",
        start_time: null,
      })
    );
  });

  it("shows the server error in the date dialog and keeps the card in place", async () => {
    vi.mocked(api.updateCandidate).mockRejectedValue(
      new api.ApiError(422, "Укажите дату выхода: перевод в этап «Вышел» без даты невозможен.")
    );
    renderKanban();

    await screen.findByText("Кандидат new");
    const newColumn = screen.getByRole("listitem", { name: /Новый/ });
    await userEvent.selectOptions(
      within(newColumn).getByLabelText("Изменить этап: Кандидат new"),
      "started"
    );
    await screen.findByRole("dialog", { name: "Укажите дату выхода" });
    await userEvent.click(screen.getByRole("button", { name: "Перевести в «Вышел»" }));

    const dialog = await screen.findByRole("dialog", { name: "Укажите дату выхода" });
    expect(
      await within(dialog).findByText(/перевод в этап «Вышел» без даты невозможен/)
    ).toBeInTheDocument();
    expect(within(newColumn).getByText("Кандидат new")).toBeInTheDocument();
  });
});

/**
 * UX feedback 2026-09-29, block C: the funnel has 11 columns, only a few fit
 * on screen, and moving a card from the first stage to the last meant
 * scrolling to each column in turn. The card-level stage picker and the
 * board auto-scroll must make that a single action.
 */
describe("KanbanPage — длинная воронка (block C)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
    vi.mocked(api.fetchWorkScheduleSuggestions).mockResolvedValue({
      organizations: [],
      departments: [],
      shifts: [],
    });
    vi.mocked(api.listCandidates).mockImplementation(async (query) => {
      const items = query?.stage === "new" ? [candidate("new")] : [];
      return { items, total: items.length, limit: 20, offset: 0 };
    });
  });

  it("moves a card from the first stage to the last one in a single action", async () => {
    // A request that never settles keeps the optimistic placement on screen
    // (the column reload that follows a success would otherwise race it).
    vi.mocked(api.updateCandidate).mockReturnValue(new Promise(() => {}));
    renderKanban();

    await screen.findByText("Кандидат new");
    const newColumn = screen.getByRole("listitem", { name: /Новый/ });
    // «Отказ» is the far end of the funnel and is nowhere near the visible
    // area — the picker reaches it without any scrolling at all.
    await userEvent.selectOptions(
      within(newColumn).getByLabelText("Изменить этап: Кандидат new"),
      "rejected"
    );

    await waitFor(() =>
      expect(api.updateCandidate).toHaveBeenCalledWith("44444444-4444-4444-4444-444444444444", {
        stage: "rejected",
      })
    );
    // Optimistic move: the card left the first column and landed in the last.
    await waitFor(() =>
      expect(within(newColumn).queryByText("Кандидат new")).not.toBeInTheDocument()
    );
    expect(
      within(screen.getByRole("listitem", { name: /Отказ/ })).getByText("Кандидат new")
    ).toBeInTheDocument();
  });

  it("labels the move control so the action is obvious", async () => {
    renderKanban();
    await screen.findByText("Кандидат new");
    const newColumn = screen.getByRole("listitem", { name: /Новый/ });
    expect(within(newColumn).getByText("Перенести в этап")).toBeInTheDocument();
    // ...and it stays keyboard operable through the same labelled control.
    expect(within(newColumn).getByLabelText("Изменить этап: Кандидат new")).toBeInTheDocument();
  });

  it("computes the scroll direction from the pointer position", () => {
    const rect = { left: 0, right: 1000 };
    expect(edgeScrollDirection(20, rect)).toBe(-1);
    expect(edgeScrollDirection(980, rect)).toBe(1);
    expect(edgeScrollDirection(500, rect)).toBe(0);
    // A board that already fits on screen has no edge to scroll to.
    expect(edgeScrollDirection(500, { left: 0, right: 100 })).toBe(0);
  });

  it("auto-scrolls the board while a drag hovers an edge", async () => {
    renderKanban();
    await screen.findByText("Кандидат new");

    const board = screen.getByRole("list", { name: "Воронка кандидатов по этапам" });
    // jsdom performs no layout: give the board a position and a scroll width.
    board.getBoundingClientRect = () =>
      ({
        left: 0,
        right: 1000,
        top: 0,
        bottom: 600,
        width: 1000,
        height: 600,
        x: 0,
        y: 0,
      }) as DOMRect;
    Object.defineProperty(board, "scrollLeft", { value: 0, writable: true });

    // jsdom does not implement DragEvent, so dispatch a MouseEvent carrying
    // clientX — React routes it to the very same onDragOver handler.
    const dragTo = (clientX: number) =>
      board.dispatchEvent(
        new MouseEvent("dragover", { clientX, bubbles: true, cancelable: true })
      );

    dragTo(960); // pointer near the right edge
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(board.scrollLeft).toBeGreaterThan(0);

    const scrolled = board.scrollLeft;
    // Leaving the board stops the scroll instead of letting it run away.
    board.dispatchEvent(
      new MouseEvent("dragleave", { bubbles: true, relatedTarget: document.body })
    );
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(board.scrollLeft).toBe(scrolled);
  });
});
