import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../design-system/components/Toast";
import { DuplicateCandidateError } from "../../api";
import type { Candidate, User } from "../../types";
import { CandidateFormModal } from "./CandidateFormModal";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return {
    ...original,
    createCandidate: vi.fn(),
    listHrUsers: vi.fn(),
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

const CREATED: Candidate = {
  id: "44444444-4444-4444-4444-444444444444",
  full_name: "Новый Кандидат",
  phone: "+7 900 123-45-67",
  email: "new@example.com",
  source: "site",
  position: "Инженер",
  owner_user_id: HR.id,
  owner_username: "hr1",
  stage: "new",
  created_at: "2026-09-03T10:00:00Z",
  updated_at: "2026-09-03T10:00:00Z",
  deleted_at: null,
  deleted_by_user_id: null,
  is_deleted: false,
};

function renderModal() {
  const onCreated = vi.fn();
  render(
    <ToastProvider>
      <CandidateFormModal
        open
        user={HR}
        onClose={vi.fn()}
        onCreated={onCreated}
        onOpenCandidate={vi.fn()}
      />
    </ToastProvider>
  );
  return { onCreated };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listHrUsers).mockResolvedValue({ items: [], total: 0 });
});

describe("CandidateFormModal", () => {
  it("creates a candidate through the API with client-side validation", async () => {
    vi.mocked(api.createCandidate).mockResolvedValue(CREATED);
    const { onCreated } = renderModal();

    await userEvent.type(screen.getByLabelText(/ФИО/), "Новый Кандидат");
    await userEvent.type(screen.getByLabelText(/Телефон/), "+7 900 123-45-67");
    await userEvent.type(screen.getByLabelText(/Email/), "new@example.com");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(api.createCandidate).toHaveBeenCalledWith(
        expect.objectContaining({
          full_name: "Новый Кандидат",
          confirm_duplicate: false,
        })
      )
    );
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(CREATED));
  });

  it("blocks an empty full name", async () => {
    renderModal();
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    expect(await screen.findByText("ФИО обязательно.")).toBeInTheDocument();
    expect(api.createCandidate).not.toHaveBeenCalled();
  });

  it("shows duplicate matches on 409 and resubmits with confirm_duplicate", async () => {
    vi.mocked(api.createCandidate)
      .mockRejectedValueOnce(
        new DuplicateCandidateError({
          message: "Найден похожий кандидат.",
          duplicates: [CREATED],
        })
      )
      .mockResolvedValueOnce(CREATED);
    renderModal();

    await userEvent.type(screen.getByLabelText(/ФИО/), "Дубль Кандидат");
    await userEvent.type(screen.getByLabelText(/Телефон/), "+7 900 123-45-67");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));

    expect(await screen.findByRole("heading", { name: "Найдены похожие кандидаты" })).toBeInTheDocument();
    expect(screen.getByText("Новый Кандидат")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Всё равно сохранить" }));

    await waitFor(() =>
      expect(api.createCandidate).toHaveBeenLastCalledWith(
        expect.objectContaining({ confirm_duplicate: true })
      )
    );
  });

  it("opens a matching candidate from the duplicate dialog", async () => {
    const onOpenCandidate = vi.fn();
    vi.mocked(api.createCandidate).mockRejectedValue(
      new DuplicateCandidateError({
        message: "Найден похожий кандидат.",
        duplicates: [CREATED],
      })
    );
    render(
      <ToastProvider>
        <CandidateFormModal
          open
          user={HR}
          onClose={vi.fn()}
          onCreated={vi.fn()}
          onOpenCandidate={onOpenCandidate}
        />
      </ToastProvider>
    );

    await userEvent.type(screen.getByLabelText(/ФИО/), "Дубль");
    await userEvent.click(screen.getByRole("button", { name: "Создать" }));
    await screen.findByRole("heading", { name: "Найдены похожие кандидаты" });
    await userEvent.click(screen.getByRole("button", { name: "Открыть" }));

    expect(onOpenCandidate).toHaveBeenCalledWith(CREATED.id);
  });
});
