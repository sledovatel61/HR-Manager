import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import type { Candidate, CandidateTransfer, User } from "../../types";
import { TransferDialog } from "./TransferDialog";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    listHrUsers: vi.fn(),
    transferCandidate: vi.fn(),
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

const CANDIDATE: Candidate = {
  id: "44444444-4444-4444-4444-444444444444",
  full_name: "Петров Пётр Петрович",
  phone: null,
  email: null,
  source: "site",
  position: "Инженер",
  owner_user_id: HR.id,
  owner_username: "hr1",
  stage: "new",
  created_at: "2026-09-01T10:00:00Z",
  updated_at: "2026-09-02T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

const TRANSFER: CandidateTransfer = {
  id: "t-1",
  candidate_id: CANDIDATE.id,
  initiator_user_id: HR.id,
  initiator_username: "hr1",
  from_user_id: HR.id,
  from_username: "hr1",
  to_user_id: "33333333-3333-3333-3333-333333333333",
  to_username: "hr2",
  reason: "Перераспределение нагрузки",
  created_at: "2026-09-03T10:00:00Z",
};

function renderDialog() {
  const onDone = vi.fn();
  render(
    <ToastProvider>
      <TransferDialog
        open
        candidate={CANDIDATE}
        user={HR}
        onClose={vi.fn()}
        onDone={onDone}
      />
    </ToastProvider>
  );
  return { onDone };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({
    items: [
      { id: "33333333-3333-3333-3333-333333333333", username: "hr2", full_name: "HR Два", role: "hr", is_active: true },
    ],
    total: 1,
  });
});

describe("TransferDialog (two-step flow)", () => {
  it("requires a target HR and a non-blank reason before proceeding", async () => {
    renderDialog();

    await userEvent.click(await screen.findByRole("button", { name: "Далее" }));
    expect(await screen.findByText("Выберите нового ответственного.")).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText(/Новый ответственный HR/), "33333333-3333-3333-3333-333333333333");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    expect(await screen.findByText("Укажите причину передачи.")).toBeInTheDocument();
  });

  it("confirms with old/new names and calls the API on success", async () => {
    vi.mocked(api.transferCandidate).mockResolvedValue({
      transfer: TRANSFER,
      candidate: { ...CANDIDATE, owner_user_id: TRANSFER.to_user_id, owner_username: "hr2" },
    });
    const { onDone } = renderDialog();

    await userEvent.selectOptions(
      await screen.findByLabelText(/Новый ответственный HR/),
      "33333333-3333-3333-3333-333333333333"
    );
    await userEvent.type(screen.getByLabelText(/Причина передачи/), "Перераспределение нагрузки");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));

    expect(await screen.findByRole("heading", { name: "Подтвердите передачу" })).toBeInTheDocument();
    expect(screen.getByText("hr1")).toBeInTheDocument();
    expect(screen.getByText("HR Два")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Подтвердить передачу" }));

    await waitFor(() =>
      expect(api.transferCandidate).toHaveBeenCalledWith(CANDIDATE.id, {
        new_owner_user_id: "33333333-3333-3333-3333-333333333333",
        reason: "Перераспределение нагрузки",
      })
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("returns to step one with an error when the server rejects the transfer", async () => {
    vi.mocked(api.transferCandidate).mockRejectedValue(
      new api.ApiError(409, "Ответственный кандидата уже изменился; обновите данные и повторите.")
    );
    const { onDone } = renderDialog();

    await userEvent.selectOptions(
      await screen.findByLabelText(/Новый ответственный HR/),
      "33333333-3333-3333-3333-333333333333"
    );
    await userEvent.type(screen.getByLabelText(/Причина передачи/), "Причина");
    await userEvent.click(screen.getByRole("button", { name: "Далее" }));
    await userEvent.click(await screen.findByRole("button", { name: "Подтвердить передачу" }));

    expect(
      await screen.findByText("Ответственный кандидата уже изменился; обновите данные и повторите.")
    ).toBeInTheDocument();
    // Nothing is visually treated as transferred.
    expect(onDone).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "Передача кандидата" })).toBeInTheDocument();
  });
});
