import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  listCandidates,
  listHrUsers,
  updateCandidate,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput } from "../../design-system/components/Field";
import { EmptyState, ErrorState } from "../../design-system/components/StateViews";
import { StageChip } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import {
  CANDIDATE_STAGE_ORDER,
  STAGE_LABELS,
  type Candidate,
  type CandidateStage,
  type User,
  type UserListItem,
} from "../../types";
import { CandidateDrawer } from "./CandidateDrawer";
import { CandidateFormModal } from "./CandidateFormModal";
import "./kanban.css";

/**
 * Documented loading strategy: per-column server-side pagination. Every
 * column fetches its own first page via GET /candidates?stage=…&limit=… and
 * grows with «Показать ещё» — the board never requests the whole database.
 */
const COLUMN_PAGE_SIZE = 20;

interface ColumnState {
  items: Candidate[];
  total: number;
  loading: boolean;
  error: string | null;
}

type Columns = Record<CandidateStage, ColumnState>;

function emptyColumns(): Columns {
  const columns = {} as Columns;
  for (const stage of CANDIDATE_STAGE_ORDER) {
    columns[stage] = { items: [], total: 0, loading: true, error: null };
  }
  return columns;
}

interface KanbanPageProps {
  user: User;
}

export default function KanbanPage({ user }: KanbanPageProps) {
  const { pushToast } = useToast();
  const canSeeAll = user.role !== "hr";
  const [ownerId, setOwnerId] = useState("");
  const [columns, setColumns] = useState<Columns>(emptyColumns);
  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [drawerCandidateId, setDrawerCandidateId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  const draggingRef = useRef<{ id: string; from: CandidateStage } | null>(null);

  const loadColumn = useCallback(
    async (stage: CandidateStage, offset: number) => {
      setColumns((current) => ({
        ...current,
        [stage]: { ...current[stage], loading: true, error: null },
      }));
      try {
        const page = await listCandidates({
          stage,
          owner_id: canSeeAll && ownerId ? ownerId : undefined,
          sort: "updated_at",
          direction: "desc",
          limit: COLUMN_PAGE_SIZE,
          offset,
        });
        setColumns((current) => {
          const column = current[stage];
          const items =
            offset === 0
              ? page.items
              : [...column.items, ...page.items.filter((item) => !column.items.some((x) => x.id === item.id))];
          return {
            ...current,
            [stage]: { items, total: page.total, loading: false, error: null },
          };
        });
      } catch (caught) {
        setColumns((current) => ({
          ...current,
          [stage]: {
            ...current[stage],
            loading: false,
            error: caught instanceof ApiError ? caught.message : "Не удалось загрузить колонку.",
          },
        }));
      }
    },
    [canSeeAll, ownerId]
  );

  useEffect(() => {
    for (const stage of CANDIDATE_STAGE_ORDER) {
      void loadColumn(stage, 0);
    }
  }, [loadColumn, reloadTick]);

  useEffect(() => {
    if (!canSeeAll) return;
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [canSeeAll]);

  const moveCandidate = useCallback(
    async (candidate: Candidate, from: CandidateStage, to: CandidateStage) => {
      if (from === to || busy) return;
      setBusy(true);

      // Optimistic move between columns (guarded by `busy` against repeats).
      setColumns((current) => ({
        ...current,
        [from]: {
          ...current[from],
          items: current[from].items.filter((item) => item.id !== candidate.id),
          total: Math.max(0, current[from].total - 1),
        },
        [to]: {
          ...current[to],
          items: [{ ...candidate, stage: to }, ...current[to].items],
          total: current[to].total + 1,
        },
      }));

      try {
        await updateCandidate(candidate.id, { stage: to });
        pushToast("success", `Этап изменён: ${STAGE_LABELS[to]}`);
        setReloadTick((tick) => tick + 1);
      } catch (caught) {
        // Hard rollback to the pre-move state.
        setColumns((current) => ({
          ...current,
          [from]: {
            ...current[from],
            items: [candidate, ...current[from].items.filter((item) => item.id !== candidate.id)],
            total: current[from].total + 1,
          },
          [to]: {
            ...current[to],
            items: current[to].items.filter((item) => item.id !== candidate.id),
            total: Math.max(0, current[to].total - 1),
          },
        }));
        pushToast(
          "danger",
          caught instanceof ApiError ? caught.message : "Не удалось изменить этап."
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, pushToast]
  );

  const anyLoading = CANDIDATE_STAGE_ORDER.some((stage) => columns[stage].loading);
  const anyError = CANDIDATE_STAGE_ORDER.find((stage) => columns[stage].error);
  const anyItems = CANDIDATE_STAGE_ORDER.some((stage) => columns[stage].items.length > 0);

  const openCandidate = (id: string) => setDrawerCandidateId(id);

  const handleStageSelect = (candidate: Candidate, from: CandidateStage, to: CandidateStage) => {
    if (from === to) return;
    void moveCandidate(candidate, from, to);
  };

  const handleDrop = (event: React.DragEvent, to: CandidateStage) => {
    event.preventDefault();
    const dragging = draggingRef.current;
    draggingRef.current = null;
    if (!dragging) return;
    const from = dragging.from;
    const card = columns[from].items.find((item) => item.id === dragging.id);
    if (card) void moveCandidate(card, from, to);
  };

  return (
    <div className="kanban-page">
      <div className="kanban-toolbar">
        {canSeeAll && (
          <Field label="Ответственный">
            {(id) => (
              <SelectInput
                id={id}
                value={ownerId}
                onChange={(event) => {
                  setOwnerId(event.target.value);
                  setColumns(emptyColumns());
                }}
              >
                <option value="">Все HR</option>
                {directory.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.full_name || item.username}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
        )}
        <span className="kanban-hint">
          Перетащите карточку между колонками или используйте выбор этапа на карточке.
        </span>
        <Button icon="plus" onClick={() => setCreateOpen(true)}>
          Добавить кандидата
        </Button>
      </div>

      {anyError && !anyItems && <ErrorState onRetry={() => setReloadTick((tick) => tick + 1)} />}
      {!anyError && !anyLoading && !anyItems && (
        <EmptyState
          title="Кандидатов пока нет"
          description="Добавьте первого кандидата — он появится в колонке «Новый»."
          action={
            <Button icon="plus" onClick={() => setCreateOpen(true)}>
              Добавить кандидата
            </Button>
          }
        />
      )}

      <div className="kanban-board" role="list" aria-label="Канбан-доска по этапам воронки">
        {CANDIDATE_STAGE_ORDER.map((stage) => {
          const column = columns[stage];
          return (
            <section
              key={stage}
              className="kanban-column"
              role="listitem"
              aria-label={`Колонка: ${STAGE_LABELS[stage]}`}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => handleDrop(event, stage)}
            >
              <header className="kanban-column-head">
                <StageChip stage={stage} size="sm" />
                <span className="kanban-count">{column.total}</span>
              </header>

              <div className="kanban-cards">
                {column.loading && column.items.length === 0 && (
                  <p className="muted-text">Загрузка…</p>
                )}
                {!column.loading && column.error && (
                  <button
                    type="button"
                    className="kanban-retry"
                    onClick={() => void loadColumn(stage, 0)}
                  >
                    Ошибка загрузки — повторить
                  </button>
                )}
                {!column.error &&
                  column.items.map((candidate) => (
                    <article
                      key={candidate.id}
                      className="kanban-card"
                      draggable={!busy}
                      onDragStart={(event) => {
                        draggingRef.current = { id: candidate.id, from: stage };
                        event.dataTransfer.effectAllowed = "move";
                      }}
                      onDragEnd={() => {
                        draggingRef.current = null;
                      }}
                    >
                      <button
                        type="button"
                        className="kanban-card-name"
                        onClick={() => openCandidate(candidate.id)}
                      >
                        {candidate.full_name}
                      </button>
                      {candidate.position && (
                        <span className="kanban-card-position">{candidate.position}</span>
                      )}
                      <div className="kanban-card-foot">
                        {canSeeAll && (
                          <span className="kanban-card-owner">{candidate.owner_username}</span>
                        )}
                        <SelectInput
                          aria-label={`Изменить этап: ${candidate.full_name}`}
                          value={candidate.stage}
                          disabled={busy}
                          onChange={(event) =>
                            handleStageSelect(
                              candidate,
                              stage,
                              event.target.value as CandidateStage
                            )
                          }
                        >
                          {CANDIDATE_STAGE_ORDER.map((item) => (
                            <option key={item} value={item}>
                              {STAGE_LABELS[item]}
                            </option>
                          ))}
                        </SelectInput>
                      </div>
                    </article>
                  ))}

                {column.total > column.items.length && (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={column.loading}
                    onClick={() => void loadColumn(stage, column.items.length)}
                  >
                    Показать ещё ({column.total - column.items.length})
                  </Button>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {drawerCandidateId && (
        <CandidateDrawer
          candidateId={drawerCandidateId}
          user={user}
          onClose={() => setDrawerCandidateId(null)}
          onChanged={() => setReloadTick((tick) => tick + 1)}
          onOpenCandidate={(id) => setDrawerCandidateId(id)}
        />
      )}

      <CandidateFormModal
        open={createOpen}
        user={user}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          setReloadTick((tick) => tick + 1);
        }}
        onOpenCandidate={(id) => setDrawerCandidateId(id)}
      />
    </div>
  );
}
