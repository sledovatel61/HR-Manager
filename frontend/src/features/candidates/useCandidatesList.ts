import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, listCandidates } from "../../api";
import type { Candidate, CandidateListQuery } from "../../types";

export interface CandidateListState {
  items: Candidate[];
  total: number;
  loading: boolean;
  error: string | null;
}

const SEARCH_DEBOUNCE_MS = 300;

/**
 * Server-side candidate list state: debounced search input and a sequence
 * guard so out-of-order responses never overwrite newer results.
 */
export function useCandidatesList(query: CandidateListQuery): CandidateListState & {
  reload: () => void;
} {
  const [debouncedQuery, setDebouncedQuery] = useState(query.query ?? "");
  const [state, setState] = useState<CandidateListState>({
    items: [],
    total: 0,
    loading: true,
    error: null,
  });
  const sequenceRef = useRef(0);
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedQuery(query.query ?? ""),
      SEARCH_DEBOUNCE_MS
    );
    return () => window.clearTimeout(timer);
  }, [query.query]);

  const load = useCallback(async () => {
    const sequence = ++sequenceRef.current;
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const page = await listCandidates({ ...query, query: debouncedQuery });
      if (sequence !== sequenceRef.current) return; // stale response guard
      setState({ items: page.items, total: page.total, loading: false, error: null });
    } catch (caught) {
      if (sequence !== sequenceRef.current) return;
      setState((current) => ({
        ...current,
        loading: false,
        error:
          caught instanceof ApiError
            ? caught.message
            : "Не удалось загрузить список кандидатов.",
      }));
    }
  }, [query, debouncedQuery]);

  useEffect(() => {
    void load();
  }, [load, reloadTick]);

  return {
    ...state,
    reload: useCallback(() => setReloadTick((tick) => tick + 1), []),
  };
}
