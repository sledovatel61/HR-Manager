import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  deleteCandidate,
  listHrUsers,
  restoreCandidate,
} from "../../api";
import { Button, IconButton } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { EmptyState, ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { StageChip } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import { Icon } from "../../design-system/icons/Icon";
import {
  CANDIDATE_STAGE_ORDER,
  SOURCE_LABELS,
  STAGE_LABELS,
  type Candidate,
  type CandidateSource,
  type CandidateStage,
  type User,
  type UserListItem,
} from "../../types";
import { CandidateDrawer } from "./CandidateDrawer";
import { CandidateFormModal } from "./CandidateFormModal";
import { formatDate } from "./format";
import { useCandidatesList } from "./useCandidatesList";
import "./candidates.css";

const PAGE_SIZE = 20;

export type CandidatesMode = "queue" | "all" | "deleted";

type SortField = "created_at" | "updated_at" | "full_name" | "stage";

interface CandidatesListPageProps {
  user: User;
  mode: CandidatesMode;
}

/** Table view over GET /candidates: server-side search, filters, sort, page. */
export default function CandidatesListPage({ user, mode }: CandidatesListPageProps) {
  const isDeleted = mode === "deleted";
  const canSeeAll = user.role !== "hr";

  const [query, setQuery] = useState("");
  const [stage, setStage] = useState<CandidateStage | "">("");
  const [source, setSource] = useState<CandidateSource | "">("");
  const [ownerId, setOwnerId] = useState("");
  const [sort, setSort] = useState<SortField>("updated_at");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const [offset, setOffset] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  const [drawerCandidateId, setDrawerCandidateId] = useState<string | null>(null);

  const queryObject = useMemo(
    () => ({
      query: query || undefined,
      stage: (stage || undefined) as CandidateStage | undefined,
      source: (source || undefined) as CandidateSource | undefined,
      owner_id: canSeeAll && ownerId ? ownerId : undefined,
      include_deleted: isDeleted,
      sort,
      direction,
      limit: PAGE_SIZE,
      offset,
    }),
    [query, stage, source, ownerId, canSeeAll, isDeleted, sort, direction, offset]
  );

  const { items, total, loading, error, reload } = useCandidatesList(queryObject);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="candidates-page">
      <div className="list-toolbar">
        <div className="list-toolbar-row">
          <div className="search-box" role="search">
            <Icon name="search" size={15} />
            <TextInput
              type="search"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setOffset(0);
              }}
              placeholder="Поиск: ФИО, телефон, email"
              aria-label="Поиск кандидатов"
            />
          </div>
          <Button
            icon="plus"
            iconPosition="left"
            onClick={() => setCreateOpen(true)}
            disabled={isDeleted}
          >
            Добавить кандидата
          </Button>
        </div>

        <div className="list-toolbar-row list-filters">
          <Field label="Этап">
            {(id) => (
              <SelectInput
                id={id}
                value={stage}
                onChange={(event) => {
                  setStage(event.target.value as CandidateStage | "");
                  setOffset(0);
                }}
              >
                <option value="">Все этапы</option>
                {CANDIDATE_STAGE_ORDER.map((item) => (
                  <option key={item} value={item}>
                    {STAGE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>

          <Field label="Источник">
            {(id) => (
              <SelectInput
                id={id}
                value={source}
                onChange={(event) => {
                  setSource(event.target.value as CandidateSource | "");
                  setOffset(0);
                }}
              >
                <option value="">Все источники</option>
                {(Object.keys(SOURCE_LABELS) as CandidateSource[]).map((item) => (
                  <option key={item} value={item}>
                    {SOURCE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>

          {canSeeAll && (
            <OwnerFilter
              value={ownerId}
              onChange={(value) => {
                setOwnerId(value);
                setOffset(0);
              }}
            />
          )}

          <Field label="Сортировка">
            {(id) => (
              <div className="sort-controls">
                <SelectInput
                  id={id}
                  value={sort}
                  onChange={(event) => {
                    setSort(event.target.value as SortField);
                    setOffset(0);
                  }}
                >
                  <option value="updated_at">Обновление</option>
                  <option value="created_at">Создание</option>
                  <option value="full_name">ФИО</option>
                  <option value="stage">Этап</option>
                </SelectInput>
                <Button
                  variant="secondary"
                  size="sm"
                  icon={direction === "desc" ? "chevron-down" : "chevron-up-down"}
                  aria-label={
                    direction === "desc"
                      ? "Сортировать по возрастанию"
                      : "Сортировать по убыванию"
                  }
                  onClick={() => setDirection(direction === "desc" ? "asc" : "desc")}
                />
              </div>
            )}
          </Field>
        </div>
      </div>

      {loading && <SkeletonRows rows={6} columns={6} />}

      {!loading && error && <ErrorState onRetry={reload} />}

      {!loading && !error && items.length === 0 && (
        <EmptyState
          icon={isDeleted ? "trash" : "inbox"}
          title={isDeleted ? "Нет удалённых кандидатов" : "Кандидаты не найдены"}
          description={
            isDeleted
              ? "Мягко удалённые кандидаты появятся здесь — их можно восстановить."
              : "Измените фильтры или добавьте нового кандидата."
          }
        />
      )}

      {!loading && !error && items.length > 0 && (
        <div className="table-wrap">
          <table className="candidates-table">
            <thead>
              <tr>
                <th scope="col">Кандидат</th>
                <th scope="col">Должность</th>
                <th scope="col">Этап</th>
                <th scope="col">Источник</th>
                {canSeeAll && <th scope="col">Ответственный</th>}
                <th scope="col">Обновлён</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((candidate) => (
                <tr key={candidate.id}>
                  <td>
                    <button
                      type="button"
                      className="row-name"
                      onClick={() => {
                        if (!candidate.is_deleted) setDrawerCandidateId(candidate.id);
                      }}
                    >
                      <span className="row-avatar" aria-hidden="true">
                        {candidate.full_name.slice(0, 2).toUpperCase()}
                      </span>
                      <span>
                        <span className="row-fullname">{candidate.full_name}</span>
                        {candidate.phone && <span className="row-contact">{candidate.phone}</span>}
                        {!candidate.phone && candidate.email && (
                          <span className="row-contact">{candidate.email}</span>
                        )}
                      </span>
                    </button>
                  </td>
                  <td>{candidate.position || "—"}</td>
                  <td>
                    <StageChip stage={candidate.stage} size="sm" />
                  </td>
                  <td>{SOURCE_LABELS[candidate.source]}</td>
                  {canSeeAll && <td>{candidate.owner_username}</td>}
                  <td>{formatDate(candidate.updated_at)}</td>
                  <td className="row-actions">
                    {isDeleted ? (
                      <RestoreAction candidate={candidate} onDone={reload} />
                    ) : (
                      <DeleteAction candidate={candidate} onDone={reload} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && !error && total > 0 && (
        <nav className="pagination" aria-label="Пагинация списка кандидатов">
          <span className="pagination-info">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} из {total}
          </span>
          <div className="pagination-buttons">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}
            >
              Назад
            </Button>
            <span className="pagination-page">
              {currentPage} / {totalPages}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset((current) => current + PAGE_SIZE)}
            >
              Вперёд
            </Button>
          </div>
        </nav>
      )}

      {drawerCandidateId && (
        <CandidateDrawer
          candidateId={drawerCandidateId}
          user={user}
          onClose={() => setDrawerCandidateId(null)}
          onChanged={reload}
          onOpenCandidate={(id) => setDrawerCandidateId(id)}
        />
      )}

      <CandidateFormModal
        open={createOpen}
        user={user}
        onClose={() => setCreateOpen(false)}
        onCreated={(candidate) => {
          setCreateOpen(false);
          reload();
          if (!candidate.is_deleted) setDrawerCandidateId(candidate.id);
        }}
        onOpenCandidate={(id) => setDrawerCandidateId(id)}
      />
    </div>
  );
}

/** Manager/admin-only filter by responsible HR (real API, no mock list). */
function OwnerFilter({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const [directory, setDirectory] = useState<UserListItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {
        // The filter simply stays empty when the directory is unavailable;
        // server-side RBAC remains the source of truth.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Field label="Ответственный">
      {(id) => (
        <SelectInput id={id} value={value} onChange={(event) => onChange(event.target.value)}>
          <option value="">Все</option>
          {directory.map((item) => (
            <option key={item.id} value={item.id}>
              {item.full_name || item.username}
            </option>
          ))}
        </SelectInput>
      )}
    </Field>
  );
}

function DeleteAction({ candidate, onDone }: { candidate: Candidate; onDone: () => void }) {
  const { pushToast } = useToast();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const handleConfirm = async () => {
    setBusy(true);
    try {
      await deleteCandidate(candidate.id);
      pushToast("success", `Кандидат «${candidate.full_name}» перемещён в удалённые.`);
      onDone();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось удалить кандидата."
      );
    } finally {
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  return (
    <>
      <IconButton
        icon="trash"
        label={`Удалить кандидата ${candidate.full_name}`}
        onClick={() => setConfirmOpen(true)}
      />
      <ConfirmDialog
        open={confirmOpen}
        danger
        title="Удалить кандидата?"
        description={`«${candidate.full_name}» будет перемещён в удалённые. Его можно будет восстановить позже.`}
        confirmLabel={busy ? "Удаляем…" : "Удалить"}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => void handleConfirm()}
      />
    </>
  );
}

function RestoreAction({ candidate, onDone }: { candidate: Candidate; onDone: () => void }) {
  const { pushToast } = useToast();
  const [busy, setBusy] = useState(false);

  const handleRestore = async () => {
    setBusy(true);
    try {
      await restoreCandidate(candidate.id);
      pushToast("success", `Кандидат «${candidate.full_name}» восстановлен.`);
      onDone();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось восстановить кандидата."
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      variant="secondary"
      size="sm"
      icon="undo"
      disabled={busy}
      onClick={() => void handleRestore()}
    >
      Восстановить
    </Button>
  );
}
