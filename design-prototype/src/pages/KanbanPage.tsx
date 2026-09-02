import { useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { FilterBar } from "../features/candidates/FilterBar";
import { KanbanBoard } from "../features/candidates/KanbanBoard";
import { CandidateDrawer } from "../features/candidates/CandidateDrawer";
import { useAppState } from "../state/AppState";
import { useCandidateFilters } from "../features/candidates/useCandidateFilters";

export function KanbanPage() {
  const { candidates, currentUserId, updateCandidateStage } = useAppState();
  const { filters, setFilters, filtered } = useCandidateFilters(candidates, currentUserId);
  const [openId, setOpenId] = useState<string | null>(null);

  return (
    <div>
      <PageHeader
        title="Kanban"
        description="Перетаскивайте карточки между этапами или используйте выпадающий список для клавиатурного управления."
      />
      <FilterBar filters={filters} onChange={setFilters} resultCount={filtered.length} showOnlyMineToggle />
      <KanbanBoard candidates={filtered} onOpen={setOpenId} onMove={updateCandidateStage} />
      <CandidateDrawer candidateId={openId} onClose={() => setOpenId(null)} />
    </div>
  );
}
