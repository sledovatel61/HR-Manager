import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api";
import { usePositionOptions } from "./usePositionOptions";

vi.mock("../../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../api")>();
  return { ...original, listPositionOptions: vi.fn() };
});

import * as api from "../../api";

function directory(positions: string[], total = positions.length) {
  return {
    items: positions.map((position, index) => ({ position, count: index + 1 })),
    total,
    limit: 500,
    truncated: total > positions.length,
  };
}

describe("usePositionOptions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.listPositionOptions).mockResolvedValue(directory(["Инженер", "Монтажник РЭА"]));
  });

  it("asks the server for the directory and uses its order as is", async () => {
    const { result } = renderHook(() => usePositionOptions({ owner_id: undefined }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    // Справочник строит сервер: он же дедуплицирует и сортирует.
    expect(result.current.positions).toEqual(["Инженер", "Монтажник РЭА"]);
    expect(result.current.error).toBeNull();
    expect(vi.mocked(api.listPositionOptions).mock.calls[0]?.[0]).toEqual({
      owner_id: undefined,
      include_deleted: false,
    });
  });

  it("sends the RBAC scope (owner and deleted view) and nothing else", async () => {
    const { result } = renderHook(() =>
      usePositionOptions({ owner_id: "33333333-3333-3333-3333-333333333333", include_deleted: true }),
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(vi.mocked(api.listPositionOptions).mock.calls[0]?.[0]).toEqual({
      owner_id: "33333333-3333-3333-3333-333333333333",
      include_deleted: true,
    });
  });

  it("refetches when the RBAC scope changes or reload() is called", async () => {
    const { result, rerender } = renderHook(
      ({ ownerId }: { ownerId: string | undefined }) => usePositionOptions({ owner_id: ownerId }),
      { initialProps: { ownerId: undefined as string | undefined } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    rerender({ ownerId: "33333333-3333-3333-3333-333333333333" });
    await waitFor(() => {
      expect(vi.mocked(api.listPositionOptions)).toHaveBeenCalledTimes(2);
    });
    expect(vi.mocked(api.listPositionOptions).mock.calls[1]?.[0]).toMatchObject({
      owner_id: "33333333-3333-3333-3333-333333333333",
    });

    result.current.reload();
    await waitFor(() => {
      expect(vi.mocked(api.listPositionOptions)).toHaveBeenCalledTimes(3);
    });
  });

  it("reports a failed load instead of silently showing an empty list", async () => {
    vi.mocked(api.listPositionOptions).mockRejectedValue(new ApiError(500, "Сбой сервера"));
    const { result } = renderHook(() => usePositionOptions({}));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.positions).toEqual([]);
    // Ошибку видно: экран показывает текст и кнопку повтора (см. CandidatesListPage).
    expect(result.current.error).toBe("Не удалось загрузить список должностей.");
  });

  it("recovers when a retry succeeds", async () => {
    vi.mocked(api.listPositionOptions).mockRejectedValueOnce(new ApiError(500, "Сбой сервера"));
    const { result } = renderHook(() => usePositionOptions({}));

    await waitFor(() => {
      expect(result.current.error).not.toBeNull();
    });

    vi.mocked(api.listPositionOptions).mockResolvedValue(directory(["Инженер"]));
    result.current.reload();

    await waitFor(() => {
      expect(result.current.positions).toEqual(["Инженер"]);
    });
    expect(result.current.error).toBeNull();
  });
});
