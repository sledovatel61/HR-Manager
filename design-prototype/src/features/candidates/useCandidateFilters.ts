import { useMemo, useState } from "react";
import type { Candidate, CandidateSource, CandidateStage } from "../../types";

export interface CandidateFilters {
  query: string;
  stage: CandidateStage | "all";
  ownerId: string | "all";
  source: CandidateSource | "all";
  onlyMine: boolean;
}

export const DEFAULT_FILTERS: CandidateFilters = {
  query: "",
  stage: "all",
  ownerId: "all",
  source: "all",
  onlyMine: false,
};

export function useCandidateFilters(candidates: Candidate[], currentUserId: string) {
  const [filters, setFilters] = useState<CandidateFilters>(DEFAULT_FILTERS);
  const [sortKey, setSortKey] = useState<"lastActivityAt" | "createdAt" | "fullName">("lastActivityAt");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const filtered = useMemo(() => {
    let list = candidates.filter((c) => !c.isDeleted);
    if (filters.onlyMine) list = list.filter((c) => c.ownerId === currentUserId);
    if (filters.stage !== "all") list = list.filter((c) => c.stage === filters.stage);
    if (filters.ownerId !== "all") list = list.filter((c) => c.ownerId === filters.ownerId);
    if (filters.source !== "all") list = list.filter((c) => c.source === filters.source);
    if (filters.query.trim()) {
      const q = filters.query.trim().toLowerCase();
      list = list.filter(
        (c) =>
          c.fullName.toLowerCase().includes(q) ||
          c.position.toLowerCase().includes(q) ||
          c.city.toLowerCase().includes(q) ||
          c.emailMasked.toLowerCase().includes(q),
      );
    }
    const sorted = [...list].sort((a, b) => {
      let cmp = 0;
      if (sortKey === "fullName") cmp = a.fullName.localeCompare(b.fullName, "ru");
      else cmp = a[sortKey] < b[sortKey] ? -1 : a[sortKey] > b[sortKey] ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return sorted;
  }, [candidates, filters, currentUserId, sortKey, sortDir]);

  return { filters, setFilters, filtered, sortKey, setSortKey, sortDir, setSortDir };
}
