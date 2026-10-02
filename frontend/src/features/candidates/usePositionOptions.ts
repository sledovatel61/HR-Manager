import { useCallback, useEffect, useState } from "react";
import { listCandidates } from "../../api";
import type { CandidateListQuery } from "../../types";

/** Server caps a single page at 100 rows — this is the ceiling for the
 *  suggestion list, not for the filter itself (filtering is server-side). */
const DIRECTORY_PAGE_SIZE = 100;

export interface PositionOptions {
  positions: string[];
  loading: boolean;
  reload: () => void;
}

/**
 * Distinct positions for the «Должность» filter.
 *
 * A vacancy directory does not exist in the project: `Candidate.position` is
 * free text (see `CandidateFormModal`). So the option list is collected from
 * the data the user can already see, in the same RBAC scope as the list
 * itself, and the filter itself is applied by the server — picking
 * «монтажник» leaves only монтажник applications on every page, not just on
 * the page currently loaded.
 */
export function usePositionOptions(scope: CandidateListQuery): PositionOptions {
  // Только параметры области видимости: текст поиска и пагинация в подсказки
  // не попадают, иначе список должностей сужался бы вместе с фильтрами.
  const ownerId = scope.owner_id;
  const includeDeleted = scope.include_deleted ?? false;
  const [positions, setPositions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    listCandidates({
      owner_id: ownerId,
      include_deleted: includeDeleted,
      limit: DIRECTORY_PAGE_SIZE,
      offset: 0,
    })
      .then((page) => {
        if (cancelled) return;
        const unique = Array.from(
          new Set(
            page.items
              .map((candidate) => candidate.position.trim())
              .filter((value) => value.length > 0),
          ),
        ).sort((a, b) => a.localeCompare(b, "ru"));
        setPositions(unique);
      })
      .catch(() => {
        // Список подсказок — не критичная функция: неудача просто оставляет
        // поле без вариантов (фильтр по-прежнему доступен).
        if (!cancelled) setPositions([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [ownerId, includeDeleted, reloadTick]);

  const reload = useCallback(() => setReloadTick((tick) => tick + 1), []);
  return { positions, loading, reload };
}
