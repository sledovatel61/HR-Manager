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
import { StartDateModal } from "../schedule/StartDateModal";
import { CandidateFormModal } from "./CandidateFormModal";
import { EDGE_SPEED_PX, edgeScrollDirection } from "./boardScroll";
import { usePositionOptions } from "./usePositionOptions";
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
  const [position, setPosition] = useState("");
  const [columns, setColumns] = useState<Columns>(emptyColumns);
  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [drawerCandidateId, setDrawerCandidateId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [reloadTick, setReloadTick] = useState(0);
  /** Кандидат, для которого окно «Укажите дату выхода» открыто. */
  const [startDateFor, setStartDateFor] = useState<{
    candidate: Candidate;
    from: CandidateStage;
  } | null>(null);
  const [startDateError, setStartDateError] = useState<string | null>(null);
  const draggingRef = useRef<{ id: string; from: CandidateStage } | null>(null);
  /** Board element for DnD auto-scroll. */
  const boardRef = useRef<HTMLDivElement | null>(null);
  /** Direction currently requested by the pointer (-1 left, 1 right, 0 none). */
  const autoScrollRef = useRef(0);
  const autoScrollTimerRef = useRef<number | null>(null);

  const stopAutoScroll = useCallback(() => {
    autoScrollRef.current = 0;
    if (autoScrollTimerRef.current !== null) {
      window.clearInterval(autoScrollTimerRef.current);
      autoScrollTimerRef.current = null;
    }
  }, []);

  useEffect(() => stopAutoScroll, [stopAutoScroll]);

  /** Recompute the scroll direction from the pointer position. */
  const updateAutoScroll = useCallback((clientX: number) => {
    const board = boardRef.current;
    if (!board) return;
    const direction = edgeScrollDirection(clientX, board.getBoundingClientRect());

    if (direction === autoScrollRef.current) return;
    autoScrollRef.current = direction;
    if (autoScrollTimerRef.current !== null) {
      window.clearInterval(autoScrollTimerRef.current);
      autoScrollTimerRef.current = null;
    }
    if (direction === 0) return;
    autoScrollTimerRef.current = window.setInterval(() => {
      const target = boardRef.current;
      if (!target || autoScrollRef.current === 0) return;
      target.scrollLeft += EDGE_SPEED_PX * autoScrollRef.current;
    }, 16);
  }, []);

  const loadColumn = useCallback(
    async (stage: CandidateStage, offset: number) => {
      setColumns((current) => ({
        ...current,
        [stage]: { ...current[stage], loading: true, error: null },
      }));
      try {
        const page = await listCandidates({
          stage,
          position: position || undefined,
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
    [canSeeAll, ownerId, position]
  );

  useEffect(() => {
    for (const stage of CANDIDATE_STAGE_ORDER) {
      void loadColumn(stage, 0);
    }
  }, [loadColumn, reloadTick]);

  const positionOptions = usePositionOptions({
    owner_id: canSeeAll && ownerId ? ownerId : undefined,
  });
  const { reload: reloadPositions } = positionOptions;

  /** Кандидат изменился (этап, карточка, создание) — перечитываем и доску,
   *  и справочник должностей: новая должность должна появиться в подсказках. */
  const reloadBoard = useCallback(() => {
    setReloadTick((tick) => tick + 1);
    reloadPositions();
  }, [reloadPositions]);

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

  /**
   * Перевод карточки: оптимистично двигаем между колонками, затем сохраняем.
   * Для этапа «Вышел» сервер требует дату (422) — она передаётся в `extra`
   * из окна «Укажите дату выхода». Возвращает текст ошибки или null.
   */
  const performMove = useCallback(
    async (
      candidate: Candidate,
      from: CandidateStage,
      to: CandidateStage,
      extra: { start_date?: string; start_time?: string | null } = {}
    ): Promise<string | null> => {
      if (from === to || busy) return null;
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
          items: [{ ...candidate, stage: to, ...extra }, ...current[to].items],
          total: current[to].total + 1,
        },
      }));

      try {
        await updateCandidate(candidate.id, { stage: to, ...extra });
        pushToast("success", `Этап изменён: ${STAGE_LABELS[to]}`);
        reloadBoard();
        return null;
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
        const message =
          caught instanceof ApiError ? caught.message : "Не удалось изменить этап.";
        pushToast("danger", message);
        return message;
      } finally {
        setBusy(false);
      }
    },
    [busy, pushToast, reloadBoard]
  );

  const moveCandidate = useCallback(
    (candidate: Candidate, from: CandidateStage, to: CandidateStage) => {
      if (from === to || busy) return;
      if (to === "started" && !candidate.start_date) {
        // «Вышел» без даты сервер не примет: спрашиваем дату заранее.
        setStartDateError(null);
        setStartDateFor({ candidate, from });
        return;
      }
      void performMove(candidate, from, to);
    },
    [busy, performMove]
  );

  const confirmStartDate = async (date: string, time: string | null) => {
    if (!startDateFor) return;
    const message = await performMove(startDateFor.candidate, startDateFor.from, "started", {
      start_date: date,
      start_time: time,
    });
    if (message) {
      setStartDateError(message);
    } else {
      setStartDateFor(null);
      setStartDateError(null);
    }
  };

  const anyLoading = CANDIDATE_STAGE_ORDER.some((stage) => columns[stage].loading);
  const anyError = CANDIDATE_STAGE_ORDER.find((stage) => columns[stage].error);
  const anyItems = CANDIDATE_STAGE_ORDER.some((stage) => columns[stage].items.length > 0);
  const totalCount = CANDIDATE_STAGE_ORDER.reduce((sum, stage) => sum + columns[stage].total, 0);

  const openCandidate = (id: string) => setDrawerCandidateId(id);

  const handleStageSelect = (candidate: Candidate, from: CandidateStage, to: CandidateStage) => {
    moveCandidate(candidate, from, to);
  };

  const handleDrop = (event: React.DragEvent, to: CandidateStage) => {
    event.preventDefault();
    stopAutoScroll();
    const dragging = draggingRef.current;
    draggingRef.current = null;
    if (!dragging) return;
    const from = dragging.from;
    const card = columns[from].items.find((item) => item.id === dragging.id);
    if (card) moveCandidate(card, from, to);
  };

  return (
    <div className="kanban-page">
      <header className="page-head">
        <div>
          <div className="eyebrow">Подбор</div>
          <p className="page-sub">
            {totalCount > 0
              ? `Всего на доске: ${totalCount} · перетащите карточку, чтобы сменить этап`
              : "Доска пуста"}
          </p>
        </div>
      </header>

      <div className="kanban-toolbar">
        <Field label="Должность" error={positionOptions.error ?? undefined}>
          {(id) => (
            <SelectInput
              id={id}
              value={position}
              onChange={(event) => {
                setPosition(event.target.value);
                setColumns(emptyColumns());
              }}
            >
              <option value="">Все должности</option>
              {positionOptions.positions.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>
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
          Перетащите карточку между колонками (у краёв доски список прокручивается
          сам) или выберите этап прямо на карточке — так можно перевести кандидата
          в любой этап за одно действие.
        </span>
        {positionOptions.error && (
          <Button variant="ghost" size="sm" onClick={reloadPositions}>
            Обновить должности
          </Button>
        )}
        <Button icon="plus" onClick={() => setCreateOpen(true)}>
          Добавить кандидата
        </Button>
      </div>

      {anyError && !anyItems && <ErrorState onRetry={reloadBoard} />}
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

      <div
        className="kanban-board"
        role="list"
        aria-label="Воронка кандидатов по этапам"
        ref={boardRef}
        onDragOver={(event) => {
          event.preventDefault();
          updateAutoScroll(event.clientX);
        }}
        onDragLeave={(event) => {
          // Only stop when the pointer actually leaves the board, not when it
          // crosses into a child column.
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          stopAutoScroll();
        }}
        onDrop={stopAutoScroll}
      >
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
                          <span className="kanban-card-owner">{candidate.owner_username ?? "Не назначен"}</span>
                        )}
                        <label className="kanban-move-label" htmlFor={`move-${candidate.id}`}>
                          Перенести в этап
                        </label>
                        <SelectInput
                          id={`move-${candidate.id}`}
                          aria-label={`Изменить этап: ${candidate.full_name}`}
                          className="kanban-move-select"
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
          onChanged={reloadBoard}
          onOpenCandidate={(id) => setDrawerCandidateId(id)}
        />
      )}

      <StartDateModal
        open={startDateFor !== null}
        candidateName={startDateFor?.candidate.full_name ?? ""}
        initialDate={startDateFor?.candidate.start_date ?? null}
        busy={busy}
        error={startDateError}
        onCancel={() => {
          setStartDateFor(null);
          setStartDateError(null);
        }}
        onConfirm={(date, time) => void confirmStartDate(date, time)}
      />

      <CandidateFormModal
        open={createOpen}
        user={user}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          reloadBoard();
        }}
        onOpenCandidate={(id) => setDrawerCandidateId(id)}
      />
    </div>
  );
}
