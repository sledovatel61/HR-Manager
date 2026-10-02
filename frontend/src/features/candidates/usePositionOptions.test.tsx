import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Candidate } from "../../types";
import { usePositionOptions } from "./usePositionOptions";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return { ...original, listCandidates: vi.fn() };
});

import * as api from "../../api";

function candidate(position: string, id: string): Candidate {
  return {
    id,
    full_name: `Кандидат ${id}`,
    phone: null,
    email: null,
    source: "site",
    position,
    owner_user_id: "22222222-2222-2222-2222-222222222222",
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
  };
}

describe("usePositionOptions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listCandidates).mockResolvedValue({
      items: [
        candidate("Монтажник РЭА", "s-1"),
        candidate("Инженер", "s-2"),
        // Дубликат и пустая строка: в подсказки не попадают.
        candidate("Монтажник РЭА", "s-3"),
        candidate("", "s-4"),
        candidate("   ", "s-5"),
      ],
      total: 5,
      limit: 100,
      offset: 0,
    });
  });

  it("asks for suggestions without the search text and without pagination", async () => {
    const { result } = renderHook(() =>
      // Пользователь уже ищет «петров» и листает вторую страницу — подсказки
      // обязаны остаться полными, иначе выбор должности сузится сам собой.
      usePositionOptions({ query: "петров", offset: 20, limit: 20 }),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.positions).toEqual(["Инженер", "Монтажник РЭА"]);
    expect(vi.mocked(api.listCandidates).mock.calls[0]?.[0]).toEqual({
      owner_id: undefined,
      include_deleted: false,
      limit: 100,
      offset: 0,
    });
  });

  it("does not refetch when the page or the search changes", async () => {
    const { result, rerender } = renderHook(
      ({ offset, query }: { offset: number; query: string }) =>
        usePositionOptions({ offset, query }),
      { initialProps: { offset: 0, query: "" } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(vi.mocked(api.listCandidates)).toHaveBeenCalledTimes(1);

    rerender({ offset: 20, query: "петров" });
    rerender({ offset: 40, query: "петров" });

    expect(vi.mocked(api.listCandidates)).toHaveBeenCalledTimes(1);
  });

  it("refetches when the RBAC scope changes or reload() is called", async () => {
    const { result, rerender } = renderHook(
      ({ ownerId }: { ownerId: string | undefined }) =>
        usePositionOptions({ owner_id: ownerId }),
      { initialProps: { ownerId: undefined as string | undefined } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    rerender({ ownerId: "33333333-3333-3333-3333-333333333333" });
    await waitFor(() => {
      expect(vi.mocked(api.listCandidates)).toHaveBeenCalledTimes(2);
    });
    expect(vi.mocked(api.listCandidates).mock.calls[1]?.[0]).toMatchObject({
      owner_id: "33333333-3333-3333-3333-333333333333",
      limit: 100,
    });

    result.current.reload();
    await waitFor(() => {
      expect(vi.mocked(api.listCandidates)).toHaveBeenCalledTimes(3);
    });
  });

  it("stays usable when the request fails", async () => {
    vi.mocked(api.listCandidates).mockRejectedValue(new api.ApiError(500, "Сбой сервера"));
    const { result } = renderHook(() => usePositionOptions({}));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    // Подсказки — не критичная функция: фильтр продолжает работать, просто
    // без списка вариантов.
    expect(result.current.positions).toEqual([]);
  });
});
