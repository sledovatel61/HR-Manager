import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import { STAGE_LABELS, type Candidate, type CandidateStage, type User } from "../../types";
import KanbanPage from "./KanbanPage";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listCandidates: vi.fn(),
    listHrUsers: vi.fn(),
    updateCandidate: vi.fn(),
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
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-02T10:00:00Z",
    deleted_at: null,
    deleted_by_user_id: null,
    is_deleted: false,
  };
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
    // 11 columns × first pages were requested — bounded, per-column paging.
    expect(vi.mocked(api.listCandidates)).toHaveBeenCalledTimes(11);

    await userEvent.click(screen.getByRole("button", { name: "Показать ещё (5)" }));
    await waitFor(() => expect(vi.mocked(api.listCandidates)).toHaveBeenCalledTimes(12));
    expect(vi.mocked(api.listCandidates).mock.calls.at(-1)?.[0]).toMatchObject({
      stage: "new",
      offset: 20,
    });
  });

  it("labels columns with the shared STAGE_LABELS vocabulary", () => {
    expect(STAGE_LABELS.started).toBe("Вышел");
  });
});
