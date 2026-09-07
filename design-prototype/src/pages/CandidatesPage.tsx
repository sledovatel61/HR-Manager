import { useEffect, useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { Button, IconButton } from "../components/ui/Button";
import { FilterBar } from "../features/candidates/FilterBar";
import { CandidateTable } from "../features/candidates/CandidateTable";
import { KanbanBoard } from "../features/candidates/KanbanBoard";
import { CandidateDrawer } from "../features/candidates/CandidateDrawer";
import { TransferDialog } from "../features/candidates/TransferDialog";
import { EmptyState, ErrorState, SkeletonRows } from "../components/ui/StateViews";
import { useAppState } from "../state/AppState";
import { useRouter } from "../router";
import { useCandidateFilters } from "../features/candidates/useCandidateFilters";
import { useSimulatedLoading } from "../utils/useSimulatedLoading";

export function CandidatesPage() {
  const { candidates, currentUserId, updateCandidateStage, simStatus } = useAppState();
  const { params, navigate } = useRouter();
  const loading = useSimulatedLoading();
  const { filters, setFilters, filtered, sortKey, setSortKey, sortDir, setSortDir } = useCandidateFilters(candidates, currentUserId);
  const [view, setView] = useState<"table" | "kanban">("table");
  const [openId, setOpenId] = useState<string | null>(params.id ?? null);
  const [transferId, setTransferId] = useState<string | null>(null);

  useEffect(() => {
    if (params.id) setOpenId(params.id);
  }, [params.id]);

  function handleSort(key: typeof sortKey) {
    if (key === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  function handleClose() {
    setOpenId(null);
    navigate("candidates");
  }

  return (
    <div>
      <PageHeader
        title="Кандидаты"
        description="Единая база: поиск, фильтры, сортировка и переключение между таблицей и Kanban."
        actions={
          <>
            <div className="view-toggle" role="group" aria-label="Вид отображения">
              <IconButton icon="table" label="Таблица" active={view === "table"} onClick={() => setView("table")} />
              <IconButton icon="kanban" label="Kanban" active={view === "kanban"} onClick={() => setView("kanban")} />
            </div>
            <Button variant="primary" icon="plus">Добавить кандидата</Button>
          </>
        }
      />

      <FilterBar filters={filters} onChange={setFilters} resultCount={filtered.length} showOnlyMineToggle />

      {simStatus === "offline" ? (
        <ErrorState onRetry={() => window.location.reload()} />
      ) : loading ? (
        view === "table" ? <SkeletonRows rows={8} columns={9} /> : <SkeletonRows rows={4} columns={4} />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="Кандидаты не найдены"
          description="Измените условия поиска или сбросьте фильтры, чтобы увидеть общую базу."
        />
      ) : view === "table" ? (
        <CandidateTable
          candidates={filtered}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={handleSort}
          onOpen={(id) => navigate("candidates", { id })}
          onTransfer={setTransferId}
          onQuickStage={(id) => navigate("candidates", { id })}
          selectedId={openId ?? undefined}
        />
      ) : (
        <KanbanBoard candidates={filtered} onOpen={(id) => navigate("candidates", { id })} onMove={updateCandidateStage} />
      )}

      <CandidateDrawer candidateId={openId} onClose={handleClose} />
      {transferId && <TransferDialog open={Boolean(transferId)} onClose={() => setTransferId(null)} candidateId={transferId} />}
    </div>
  );
}
