import { useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { FilterBar } from "../features/candidates/FilterBar";
import { CandidateTable } from "../features/candidates/CandidateTable";
import { CandidateDrawer } from "../features/candidates/CandidateDrawer";
import { EmptyState, SkeletonRows } from "../components/ui/StateViews";
import { useAppState } from "../state/AppState";
import { useCandidateFilters } from "../features/candidates/useCandidateFilters";
import { useSimulatedLoading } from "../utils/useSimulatedLoading";

export function QueuePage() {
  const { candidates, currentUserId, updateCandidateStage, pushToast } = useAppState();
  const loading = useSimulatedLoading();
  const { filters, setFilters, filtered, sortKey, setSortKey, sortDir, setSortDir } = useCandidateFilters(candidates, currentUserId);
  const [openId, setOpenId] = useState<string | null>(null);

  const myQueue = filtered.filter((c) => c.ownerId === currentUserId);

  function handleSort(key: typeof sortKey) {
    if (key === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  return (
    <div>
      <PageHeader
        title="Моя очередь"
        description="Кандидаты, за которых вы отвечаете. Здесь удобно приоритизировать звонки и напоминания."
      />
      <FilterBar
        filters={{ ...filters, onlyMine: true }}
        onChange={(next) => setFilters({ ...next, onlyMine: true })}
        resultCount={myQueue.length}
      />

      {loading ? (
        <SkeletonRows rows={6} columns={7} />
      ) : myQueue.length === 0 ? (
        <EmptyState
          title="В очереди никого нет"
          description={
            filters.query || filters.stage !== "all"
              ? "Ничего не найдено по текущим фильтрам. Попробуйте изменить условия поиска."
              : "Как только вам назначат кандидата, он появится здесь."
          }
        />
      ) : (
        <CandidateTable
          candidates={myQueue}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={handleSort}
          onOpen={setOpenId}
          onTransfer={setOpenId}
          onQuickStage={(id) => {
            const c = myQueue.find((x) => x.id === id);
            if (!c) return;
            updateCandidateStage(id, c.stage);
            pushToast("info", "Откройте карточку, чтобы выбрать новый этап.");
          }}
          selectedId={openId ?? undefined}
        />
      )}

      <CandidateDrawer candidateId={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}
