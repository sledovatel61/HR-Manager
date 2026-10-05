import { useCallback, useEffect, useState } from "react";
import { listPositionOptions } from "../../api";
import type { PositionOptionsQuery } from "../../types";

export interface PositionOptions {
  positions: string[];
  loading: boolean;
  /** Текст ошибки загрузки справочника; null — справочник загружен. */
  error: string | null;
  reload: () => void;
}

/**
 * Distinct positions for the «Должность» filter.
 *
 * A vacancy directory does not exist in the project: `Candidate.position` is
 * free text (see `CandidateFormModal`), so the options come from the data the
 * user may see. The list is built by the server: `GET /candidates/positions`
 * groups the **whole** visible scope by the normalized position, so a value
 * that only exists in row 5 000 is selectable too.
 *
 * Before this endpoint existed the options were collected from one page of
 * `GET /candidates` (100 rows) — the filter is a `<select>` without free
 * input, so anything beyond that page simply could not be chosen.
 *
 * The filter itself has always been server-side: picking «монтажник» leaves
 * only монтажник applications on every page, not just on the loaded one.
 */
export function usePositionOptions(scope: PositionOptionsQuery = {}): PositionOptions {
  // Только параметры области видимости: текст поиска и пагинация в подсказки
  // не попадают, иначе список должностей сужался бы вместе с фильтрами.
  const ownerId = scope.owner_id;
  const includeDeleted = scope.include_deleted ?? false;
  const [positions, setPositions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listPositionOptions({ owner_id: ownerId, include_deleted: includeDeleted })
      .then((page) => {
        if (cancelled) return;
        // Порядок и дедупликация — на сервере (нормализация: регистр и
        // пробелы), поэтому клиент больше ничего не сортирует.
        setPositions(page.items.map((item) => item.position));
      })
      .catch(() => {
        if (cancelled) return;
        setPositions([]);
        setError("Не удалось загрузить список должностей.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ownerId, includeDeleted, reloadTick]);

  const reload = useCallback(() => setReloadTick((tick) => tick + 1), []);
  return { positions, loading, error, reload };
}
