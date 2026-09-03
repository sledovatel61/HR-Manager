import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createCandidateInteraction,
  deleteCandidate,
  getCandidate,
  listCandidateInteractions,
  listCandidateTransfers,
  listEvents,
  updateCandidate,
  updateEvent,
  type DuplicateCandidateError,
} from "../../api";
import { EventFormModal } from "../calendar/EventFormModal";
import { Button, IconButton } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Drawer } from "../../design-system/components/Drawer";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { StageChip } from "../../design-system/components/StatusChip";
import { Tabs } from "../../design-system/components/Tabs";
import { useToast } from "../../design-system/components/ToastContext";
import {
  CANDIDATE_STAGE_ORDER,
  EVENT_STATUS_LABELS,
  EVENT_TYPE_LABELS,
  SOURCE_LABELS,
  STAGE_LABELS,
  type CalendarEvent,
  type Candidate,
  type CandidateInteraction,
  type CandidateInteractionType,
  type CandidateSource,
  type CandidateStage,
  type CandidateTransfer,
  type User,
} from "../../types";
import { DuplicateResolveDialog } from "./DuplicateResolveDialog";
import { TransferDialog } from "./TransferDialog";
import { formatDateTime } from "./format";
import "./drawer.css";

const INTERACTION_PAGE_SIZE = 20;
const TRANSFER_PAGE_SIZE = 20;

type DrawerTab = "info" | "interactions" | "events" | "transfers";

interface CandidateDrawerProps {
  candidateId: string;
  user: User;
  onClose: () => void;
  /** Called after any successful mutation so lists behind the drawer refresh. */
  onChanged: () => void;
  /** Called when the duplicate flow asks to open a matching candidate. */
  onOpenCandidate: (id: string) => void;
}

export function CandidateDrawer({
  candidateId,
  user,
  onClose,
  onChanged,
  onOpenCandidate,
}: CandidateDrawerProps) {
  const { pushToast } = useToast();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<DrawerTab>("info");
  const [stageBusy, setStageBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await getCandidate(candidateId);
      setCandidate(data);
    } catch (caught) {
      setCandidate(null);
      setLoadError(caught instanceof ApiError ? caught.message : "Не удалось открыть карточку.");
    } finally {
      setLoading(false);
    }
  }, [candidateId]);

  useEffect(() => {
    void load();
  }, [load]);

  const changeStage = async (next: CandidateStage) => {
    if (!candidate || candidate.stage === next || stageBusy) return;
    const previous = candidate;
    // Optimistic UI with a hard rollback on failure.
    setCandidate({ ...candidate, stage: next });
    setStageBusy(true);
    try {
      const updated = await updateCandidate(candidate.id, { stage: next });
      setCandidate(updated);
      onChanged();
      pushToast("success", `Этап изменён: ${STAGE_LABELS[next]}`);
    } catch (caught) {
      setCandidate(previous);
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось изменить этап."
      );
    } finally {
      setStageBusy(false);
    }
  };

  const handleSaved = (updated: Candidate) => {
    setCandidate(updated);
    onChanged();
  };

  const handleTransferDone = (updated: Candidate, transfer: CandidateTransfer) => {
    setCandidate(updated);
    setTransferOpen(false);
    setTab("transfers");
    onChanged();
    pushToast("success", `Кандидат передан: ${transfer.to_username}`);
    if (user.role === "hr" && updated.owner_user_id !== user.id) {
      // The card is no longer accessible to this HR: close it per the flow.
      onClose();
    }
  };

  const confirmDelete = async () => {
    if (!candidate) return;
    try {
      await deleteCandidate(candidate.id);
      pushToast("success", `Кандидат «${candidate.full_name}» перемещён в удалённые.`);
      onChanged();
      onClose();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось удалить кандидата."
      );
    } finally {
      setDeleteOpen(false);
    }
  };

  const tabs = [
    { id: "info" as const, label: "Сведения" },
    { id: "interactions" as const, label: "Взаимодействия" },
    { id: "events" as const, label: "События" },
    { id: "transfers" as const, label: "Передачи" },
  ];

  return (
    <Drawer
      open
      onClose={onClose}
      title={candidate ? candidate.full_name : "Кандидат"}
      width={560}
      headerActions={
        candidate && !candidate.is_deleted ? (
          <>
            <IconButton
              icon="trash"
              label="Удалить кандидата"
              onClick={() => setDeleteOpen(true)}
            />
            <Button
              variant="secondary"
              size="sm"
              icon="arrow-right-left"
              onClick={() => setTransferOpen(true)}
            >
              Передать
            </Button>
          </>
        ) : undefined
      }
    >
      {loading && <SkeletonRows rows={5} columns={2} />}
      {!loading && loadError && <ErrorState onRetry={() => void load()} />}
      {!loading && !loadError && candidate && (
        <div className="drawer-content">
          <Tabs
            items={tabs}
            activeId={tab}
            onChange={(id) => setTab(id as DrawerTab)}
            ariaLabel="Разделы карточки"
          />

          {tab === "info" && (
            <InfoTab
              candidate={candidate}
              stageBusy={stageBusy}
              onChangeStage={(next) => void changeStage(next)}
              onSaved={handleSaved}
              onOpenCandidate={onOpenCandidate}
            />
          )}

          {tab === "interactions" && <InteractionsTab candidate={candidate} />}

          {tab === "events" && (
            <EventsTab
              candidate={candidate}
              user={user}
              onChanged={onChanged}
            />
          )}

          {tab === "transfers" && (
            <TransfersTab candidate={candidate} onOpenTransfer={() => setTransferOpen(true)} />
          )}
        </div>
      )}

      {candidate && (
        <TransferDialog
          open={transferOpen}
          candidate={candidate}
          user={user}
          onClose={() => setTransferOpen(false)}
          onDone={handleTransferDone}
        />
      )}

      <ConfirmDialog
        open={deleteOpen}
        danger
        title="Удалить кандидата?"
        description={
          candidate
            ? `«${candidate.full_name}» будет перемещён в удалённые. Его можно будет восстановить позже.`
            : ""
        }
        confirmLabel="Удалить"
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => void confirmDelete()}
      />
    </Drawer>
  );
}

interface InfoTabProps {
  candidate: Candidate;
  stageBusy: boolean;
  onChangeStage: (stage: CandidateStage) => void;
  onSaved: (candidate: Candidate) => void;
  onOpenCandidate: (id: string) => void;
}

interface EditPayload {
  full_name: string;
  phone: string | null;
  email: string | null;
  source: CandidateSource;
  position: string;
}

function InfoTab({ candidate, stageBusy, onChangeStage, onSaved, onOpenCandidate }: InfoTabProps) {
  const { pushToast } = useToast();
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({
    full_name: candidate.full_name,
    phone: candidate.phone ?? "",
    email: candidate.email ?? "",
    source: candidate.source,
    position: candidate.position,
  });
  const [formError, setFormError] = useState<string | null>(null);
  const [duplicate, setDuplicate] = useState<{
    error: DuplicateCandidateError;
    payload: EditPayload;
  } | null>(null);

  const startEditing = () => {
    setForm({
      full_name: candidate.full_name,
      phone: candidate.phone ?? "",
      email: candidate.email ?? "",
      source: candidate.source,
      position: candidate.position,
    });
    setFormError(null);
    setEditing(true);
  };

  const payloadFromForm = (): EditPayload => ({
    full_name: form.full_name.trim(),
    phone: form.phone.trim() || null,
    email: form.email.trim() || null,
    source: form.source,
    position: form.position.trim(),
  });

  const save = async (payload: EditPayload, confirmDuplicate: boolean) => {
    setSaving(true);
    setFormError(null);
    try {
      const updated = await updateCandidate(candidate.id, { ...payload, confirm_duplicate: confirmDuplicate });
      onSaved(updated);
      setDuplicate(null);
      setEditing(false);
      pushToast("success", "Карточка сохранена.");
    } catch (caught) {
      if (caught instanceof Error && caught.name === "DuplicateCandidateError" && !confirmDuplicate) {
        setDuplicate({ error: caught as DuplicateCandidateError, payload });
      } else {
        setFormError(caught instanceof ApiError ? caught.message : "Не удалось сохранить.");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSubmit = () => {
    if (!form.full_name.trim()) {
      setFormError("ФИО обязательно.");
      return;
    }
    void save(payloadFromForm(), false);
  };

  return (
    <div className="info-tab">
      <div className="info-row">
        <span className="info-label">Этап</span>
        <div className="stage-control">
          <StageChip stage={candidate.stage} />
          <SelectInput
            aria-label="Изменить этап"
            value={candidate.stage}
            disabled={stageBusy}
            onChange={(event) => onChangeStage(event.target.value as CandidateStage)}
          >
            {CANDIDATE_STAGE_ORDER.map((stage) => (
              <option key={stage} value={stage}>
                {STAGE_LABELS[stage]}
              </option>
            ))}
          </SelectInput>
        </div>
      </div>

      {!editing ? (
        <div className="info-view">
          <dl className="detail-list">
            <div className="detail-row">
              <dt>ФИО</dt>
              <dd>{candidate.full_name}</dd>
            </div>
            <div className="detail-row">
              <dt>Телефон</dt>
              <dd>{candidate.phone || "—"}</dd>
            </div>
            <div className="detail-row">
              <dt>Email</dt>
              <dd>{candidate.email || "—"}</dd>
            </div>
            <div className="detail-row">
              <dt>Источник</dt>
              <dd>{SOURCE_LABELS[candidate.source]}</dd>
            </div>
            <div className="detail-row">
              <dt>Должность</dt>
              <dd>{candidate.position || "—"}</dd>
            </div>
            <div className="detail-row">
              <dt>Ответственный</dt>
              <dd>{candidate.owner_username}</dd>
            </div>
          </dl>
          <Button variant="secondary" icon="edit" onClick={startEditing}>
            Редактировать
          </Button>
        </div>
      ) : (
        <form
          className="info-form"
          onSubmit={(event) => {
            event.preventDefault();
            handleSubmit();
          }}
        >
          <Field label="ФИО" required error={formError ?? undefined}>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={form.full_name}
                invalid={Boolean(formError)}
                onChange={(event) => setForm({ ...form, full_name: event.target.value })}
              />
            )}
          </Field>
          <Field label="Телефон">
            {(id) => (
              <TextInput
                id={id}
                value={form.phone}
                onChange={(event) => setForm({ ...form, phone: event.target.value })}
              />
            )}
          </Field>
          <Field label="Email">
            {(id) => (
              <TextInput
                id={id}
                type="email"
                value={form.email}
                onChange={(event) => setForm({ ...form, email: event.target.value })}
              />
            )}
          </Field>
          <Field label="Источник">
            {(id) => (
              <SelectInput
                id={id}
                value={form.source}
                onChange={(event) =>
                  setForm({ ...form, source: event.target.value as CandidateSource })
                }
              >
                {(Object.keys(SOURCE_LABELS) as CandidateSource[]).map((item) => (
                  <option key={item} value={item}>
                    {SOURCE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Должность">
            {(id) => (
              <TextInput
                id={id}
                value={form.position}
                onChange={(event) => setForm({ ...form, position: event.target.value })}
              />
            )}
          </Field>
          <div className="form-actions">
            <Button type="submit" loading={saving} disabled={saving}>
              Сохранить
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={saving}
              onClick={() => setEditing(false)}
            >
              Отмена
            </Button>
          </div>
        </form>
      )}

      {duplicate && (
        <DuplicateResolveDialog
          duplicates={duplicate.error.duplicates}
          busy={saving}
          onCancel={() => setDuplicate(null)}
          onConfirm={() => void save(duplicate.payload, true)}
          onOpenMatch={(id) => {
            setDuplicate(null);
            setEditing(false);
            onOpenCandidate(id);
          }}
        />
      )}
    </div>
  );
}

interface InteractionsTabProps {
  candidate: Candidate;
}

function InteractionsTab({ candidate }: InteractionsTabProps) {
  const { pushToast } = useToast();
  const [items, setItems] = useState<CandidateInteraction[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [type, setType] = useState<CandidateInteractionType>("call");
  const [comment, setComment] = useState("");
  const [sending, setSending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listCandidateInteractions(candidate.id, INTERACTION_PAGE_SIZE, offset);
      setItems(page.items);
      setTotal(page.total);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось загрузить историю.");
    } finally {
      setLoading(false);
    }
  }, [candidate.id, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!comment.trim()) return;
    setSending(true);
    try {
      const created = await createCandidateInteraction(candidate.id, {
        type,
        comment: comment.trim(),
      });
      // The new entry appears immediately without a page reload.
      setItems((current) => [created, ...current]);
      setTotal((current) => current + 1);
      setComment("");
      pushToast("success", "Запись добавлена в историю.");
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось добавить запись."
      );
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="interactions-tab">
      <form className="interaction-form" onSubmit={(event) => void submit(event)}>
        <Field label="Тип взаимодействия">
          {(id) => (
            <SelectInput
              id={id}
              value={type}
              onChange={(event) => setType(event.target.value as CandidateInteractionType)}
            >
              <option value="call">Звонок</option>
              <option value="email">Письмо</option>
              <option value="meeting">Встреча</option>
              <option value="note">Заметка</option>
              <option value="status_change">Смена статуса</option>
            </SelectInput>
          )}
        </Field>
        <Field label="Комментарий" required>
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              placeholder="Что произошло?"
            />
          )}
        </Field>
        <Button type="submit" loading={sending} disabled={sending || !comment.trim()}>
          Добавить запись
        </Button>
      </form>

      {loading && <SkeletonRows rows={3} columns={2} />}
      {!loading && error && <ErrorState onRetry={() => void load()} />}
      {!loading && !error && items.length === 0 && (
        <p className="muted-text">Взаимодействий пока нет.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <ol className="interaction-list">
            {items.map((item) => (
              <li key={item.id} className="interaction-item">
                <span className="interaction-meta">
                  <span className="interaction-author">{item.author_username}</span>
                  <span className="interaction-time">{formatDateTime(item.created_at)}</span>
                </span>
                <span className="interaction-type">{INTERACTION_LABELS[item.type]}</span>
                <span className="interaction-comment">{item.comment}</span>
              </li>
            ))}
          </ol>
          <div className="pagination">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((current) => Math.max(0, current - INTERACTION_PAGE_SIZE))}
            >
              Назад
            </Button>
            <span className="pagination-page">
              {offset + 1}–{Math.min(offset + INTERACTION_PAGE_SIZE, total)} из {total}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={offset + INTERACTION_PAGE_SIZE >= total}
              onClick={() => setOffset((current) => current + INTERACTION_PAGE_SIZE)}
            >
              Вперёд
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

const INTERACTION_LABELS: Record<CandidateInteractionType, string> = {
  call: "Звонок",
  email: "Письмо",
  meeting: "Встреча",
  note: "Заметка",
  status_change: "Смена статуса",
};

interface TransfersTabProps {
  candidate: Candidate;
  onOpenTransfer: () => void;
}

function TransfersTab({ candidate, onOpenTransfer }: TransfersTabProps) {
  const [items, setItems] = useState<CandidateTransfer[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listCandidateTransfers(candidate.id, TRANSFER_PAGE_SIZE, offset);
      setItems(page.items);
      setTotal(page.total);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : "Не удалось загрузить историю передач."
      );
    } finally {
      setLoading(false);
    }
  }, [candidate.id, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="transfers-tab">
      <Button variant="secondary" icon="arrow-right-left" onClick={onOpenTransfer}>
        Передать кандидата
      </Button>

      {loading && <SkeletonRows rows={2} columns={2} />}
      {!loading && error && <ErrorState onRetry={() => void load()} />}
      {!loading && !error && items.length === 0 && (
        <p className="muted-text">Передач ещё не было.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <ol className="transfer-list">
            {items.map((item) => (
              <li key={item.id} className="transfer-item">
                <span className="transfer-path">
                  {item.from_username} → {item.to_username}
                </span>
                <span className="interaction-time">{formatDateTime(item.created_at)}</span>
                <span className="interaction-meta">Инициатор: {item.initiator_username}</span>
                <span className="transfer-reason">{item.reason}</span>
              </li>
            ))}
          </ol>
          <div className="pagination">
            <Button
              variant="secondary"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((current) => Math.max(0, current - TRANSFER_PAGE_SIZE))}
            >
              Назад
            </Button>
            <span className="pagination-page">
              {offset + 1}–{Math.min(offset + TRANSFER_PAGE_SIZE, total)} из {total}
            </span>
            <Button
              variant="secondary"
              size="sm"
              disabled={offset + TRANSFER_PAGE_SIZE >= total}
              onClick={() => setOffset((current) => current + TRANSFER_PAGE_SIZE)}
            >
              Вперёд
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

interface EventsTabProps {
  candidate: Candidate;
  user: User;
  onChanged: () => void;
}

/** Events of the candidate: server-filtered list, create/edit dialog and
 * quick complete/postpone actions. */
function EventsTab({ candidate, user, onChanged }: EventsTabProps) {
  const { pushToast } = useToast();
  const [items, setItems] = useState<CalendarEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editEvent, setEditEvent] = useState<CalendarEvent | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  const refresh = () => {
    setOffset(0);
    setReloadTick((tick) => tick + 1);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await listEvents({
        candidate_id: candidate.id,
        sort: "starts_at",
        direction: "asc",
        limit: 20,
        offset,
      });
      // Accumulate pages so «Показать ещё» appends; a reload starts over.
      setItems((current) => (offset === 0 ? page.items : [...current, ...page.items]));
      setTotal(page.total);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось загрузить события.");
    } finally {
      setLoading(false);
    }
  }, [candidate.id, offset]);

  useEffect(() => {
    void load();
  }, [load, reloadTick]);

  const quickComplete = async (event: CalendarEvent) => {
    if (busyId) return;
    setBusyId(event.id);
    try {
      await updateEvent(event.id, { expected_version: event.version, status: "completed" });
      pushToast("success", "Событие выполнено.");
      refresh();
    } catch (caught) {
      pushToast(
        "danger",
        caught instanceof ApiError ? caught.message : "Не удалось выполнить событие."
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="interactions-tab">
      <Button variant="secondary" icon="plus" onClick={() => setCreateOpen(true)}>
        Запланировать событие
      </Button>

      {loading && <SkeletonRows rows={3} columns={2} />}
      {!loading && error && <ErrorState onRetry={() => void load()} />}
      {!loading && !error && items.length === 0 && (
        <p className="muted-text">Событий пока нет.</p>
      )}
      {!loading && !error && items.length > 0 && (
        <>
          <ul className="events-list">
            {items.map((event) => (
              <li key={event.id} className="events-item">
                <button
                  type="button"
                  className="events-item-main"
                  onClick={() => setEditEvent(event)}
                >
                  <span className="events-item-title">
                    {EVENT_TYPE_LABELS[event.type]}: {event.title}
                  </span>
                  <span className="events-item-meta">
                    {formatDateTime(event.starts_at)} · {EVENT_STATUS_LABELS[event.status]}
                  </span>
                </button>
                {event.status !== "completed" && (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={busyId === event.id}
                    onClick={() => void quickComplete(event)}
                  >
                    Выполнено
                  </Button>
                )}
              </li>
            ))}
          </ul>
          {total > items.length && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setOffset((current) => current + 20)}
            >
              Показать ещё ({total - items.length})
            </Button>
          )}
        </>
      )}

      <EventFormModal
        open={createOpen}
        user={user}
        candidate={candidate}
        onClose={() => setCreateOpen(false)}
        onSaved={() => {
          setCreateOpen(false);
          refresh();
        }}
      />

      {editEvent && (
        <EventFormModal
          open
          user={user}
          event={editEvent}
          onClose={() => setEditEvent(null)}
          onSaved={(updated) => {
            setEditEvent(null);
            if (updated.candidate_id !== candidate.id) onChanged();
            refresh();
          }}
        />
      )}
    </div>
  );
}
