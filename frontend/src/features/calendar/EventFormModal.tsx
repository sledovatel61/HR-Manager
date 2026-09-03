import { useEffect, useState } from "react";
import {
  ApiError,
  createEvent,
  listCandidates,
  listEventHistory,
  listHrUsers,
  updateEvent,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Icon } from "../../design-system/icons/Icon";
import { Modal } from "../../design-system/components/Modal";
import { SkeletonRows } from "../../design-system/components/StateViews";
import { StageChip } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import {
  EVENT_STATUS_LABELS,
  EVENT_TYPE_LABELS,
  EVENT_HISTORY_KIND_LABELS,
  type CalendarEvent,
  type CalendarEventType,
  type Candidate,
  type EventHistoryEntry,
  type User,
  type UserListItem,
} from "../../types";
import { formatDateTime } from "../candidates/format";
import { fromLocalInput, toLocalInput } from "./time";
import "./calendar.css";

interface EventFormModalProps {
  open: boolean;
  user: User;
  /** Fixed candidate context (drawer flow); null = picker shown. */
  candidate?: Candidate | null;
  /** Edit mode when set; otherwise a create form. */
  event?: CalendarEvent | null;
  onClose: () => void;
  /** Called after a confirmed success (create or update). */
  onSaved: (event: CalendarEvent, isNew: boolean) => void;
  /** Opens the candidate card of the event. */
  onOpenCandidate?: (id: string) => void;
}

interface Draft {
  type: CalendarEventType;
  title: string;
  note: string;
  startsAt: string; // datetime-local (browser local)
  endsAt: string;
  remindAt: string;
}

function draftOf(event: CalendarEvent | null): Draft {
  return {
    type: event?.type ?? "call",
    title: event?.title ?? "",
    note: event?.note ?? "",
    startsAt: toLocalInput(event ? event.starts_at : null),
    endsAt: toLocalInput(event ? event.ends_at : null),
    remindAt: toLocalInput(event ? event.remind_at : null),
  };
}

function validate(draft: Draft): string | null {
  if (!draft.title.trim()) return "Название события обязательно.";
  if (!draft.startsAt) return "Дата начала обязательна.";
  if (draft.endsAt && draft.endsAt <= draft.startsAt) {
    return "Окончание должно быть позже начала.";
  }
  if (draft.remindAt && draft.remindAt > draft.startsAt) {
    return "Напоминание должно быть не позже начала события.";
  }
  return null;
}

/** Create/edit event dialog with complete/postpone actions and history. */
export function EventFormModal({
  open,
  user,
  candidate,
  event = null,
  onClose,
  onSaved,
  onOpenCandidate,
}: EventFormModalProps) {
  const { pushToast } = useToast();
  const editing = event !== null;
  const canAssign = user.role !== "hr";

  const [draft, setDraft] = useState<Draft>(() => draftOf(event));
  const [assigneeId, setAssigneeId] = useState(event?.assignee_user_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [history, setHistory] = useState<EventHistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Candidate picker (create-from-calendar flow without a fixed candidate).
  const [pickedCandidate, setPickedCandidate] = useState<Candidate | null>(null);
  const [candidateQuery, setCandidateQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Candidate[]>([]);
  const [searching, setSearching] = useState(false);

  const fixedCandidate = candidate ?? (editing ? null : pickedCandidate);

  useEffect(() => {
    if (!open) return;
    setDraft(draftOf(event));
    setAssigneeId(event?.assignee_user_id ?? "");
    setError(null);
    setSending(false);
    setPickedCandidate(null);
    setCandidateQuery("");
    setSuggestions([]);
    setHistory([]);
  }, [open, event]);

  // HR directory for manager/admin assignee pickers.
  useEffect(() => {
    if (!open || !canAssign) return;
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [open, canAssign]);

  // History of the event (edit mode).
  useEffect(() => {
    if (!open || !event) return;
    let cancelled = false;
    setHistoryLoading(true);
    void listEventHistory(event.id, 10, 0)
      .then((page) => {
        if (!cancelled) setHistory(page.items);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, event]);

  // Debounced candidate search for the create-from-calendar picker.
  useEffect(() => {
    if (!open || editing || candidate) return;
    const query = candidateQuery.trim();
    if (!query) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSearching(true);
      void listCandidates({ query, limit: 8, sort: "updated_at", direction: "desc" })
        .then((page) => {
          if (!cancelled) setSuggestions(page.items);
        })
        .catch(() => {
          if (!cancelled) setSuggestions([]);
        })
        .finally(() => {
          if (!cancelled) setSearching(false);
        });
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, editing, candidate, candidateQuery]);

  const submitCreate = async () => {
    const validation = validate(draft);
    if (validation) {
      setError(validation);
      return;
    }
    if (!fixedCandidate) {
      setError("Выберите кандидата.");
      return;
    }
    if (canAssign && !assigneeId) {
      setError("Выберите исполнителя — активного пользователя с ролью HR.");
      return;
    }
    setSending(true);
    setError(null);
    try {
      const created = await createEvent({
        candidate_id: fixedCandidate.id,
        type: draft.type,
        title: draft.title.trim(),
        note: draft.note.trim() || null,
        starts_at: fromLocalInput(draft.startsAt),
        ends_at: draft.endsAt ? fromLocalInput(draft.endsAt) : null,
        remind_at: draft.type === "reminder" ? null : draft.remindAt ? fromLocalInput(draft.remindAt) : null,
        assignee_user_id: canAssign && assigneeId ? assigneeId : null,
      });
      pushToast("success", `Событие «${created.title}» создано.`);
      onSaved(created, true);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Не удалось создать событие.");
    } finally {
      setSending(false);
    }
  };

  const submitUpdate = async (fields: {
    title?: string;
    note?: string | null;
    starts_at?: string;
    ends_at?: string | null;
    remind_at?: string | null;
    status?: CalendarEvent["status"];
    assignee_user_id?: string | null;
  }) => {
    if (!event) return;
    setSending(true);
    setError(null);
    try {
      const updated = await updateEvent(event.id, {
        expected_version: event.version,
        ...fields,
      });
      pushToast("success", "Событие сохранено.");
      onSaved(updated, false);
    } catch (caught) {
      // Version conflict or server error: nothing is treated as saved.
      const message = caught instanceof ApiError ? caught.message : "Не удалось сохранить событие.";
      setError(message);
      pushToast("danger", message);
      if (caught instanceof ApiError && caught.status === 409) {
        onClose(); // the caller refreshes the calendar
      }
    } finally {
      setSending(false);
    }
  };

  const handleEditSubmit = () => {
    const validation = validate(draft);
    if (validation) {
      setError(validation);
      return;
    }
    if (!event) return;
    if (canAssign && !assigneeId) {
      setError("Выберите исполнителя — активного пользователя с ролью HR.");
      return;
    }
    void submitUpdate({
      title: draft.title.trim(),
      note: draft.note.trim() || null,
      starts_at: fromLocalInput(draft.startsAt),
      ends_at: draft.endsAt ? fromLocalInput(draft.endsAt) : null,
      remind_at: draft.type === "reminder" ? null : draft.remindAt ? fromLocalInput(draft.remindAt) : null,
      assignee_user_id: canAssign && assigneeId && assigneeId !== event.assignee_user_id ? assigneeId : null,
    });
  };

  const completeEvent = () => {
    if (!event) return;
    void submitUpdate({ status: "completed" });
  };

  const postponeEvent = () => {
    const validation = validate(draft);
    if (validation || !draft.startsAt) {
      setError(validation ?? "Укажите новую дату начала для переноса.");
      return;
    }
    if (!event) return;
    void submitUpdate({
      status: "postponed",
      starts_at: fromLocalInput(draft.startsAt),
      ends_at: draft.endsAt ? fromLocalInput(draft.endsAt) : null,
    });
  };

  const isCompleted = event?.status === "completed";

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? (event ? event.title : "Событие") : "Новое событие"}
      description={
        editing
          ? "Редактирование, перенос и смена состояния события."
          : "Событие сохраняется на сервере и появляется в календаре команды."
      }
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={sending}>
            {editing ? "Закрыть" : "Отмена"}
          </Button>
          {editing && !isCompleted && (
            <>
              <Button
                variant="secondary"
                disabled={sending}
                onClick={postponeEvent}
                title="Требуется новая дата начала"
              >
                Отложить
              </Button>
              <Button
                variant="secondary"
                disabled={sending}
                onClick={completeEvent}
              >
                Выполнено
              </Button>
            </>
          )}
          <Button
            loading={sending}
            disabled={sending || isCompleted}
            onClick={() => void (editing ? handleEditSubmit() : submitCreate())}
          >
            {editing ? "Сохранить" : "Создать"}
          </Button>
        </>
      }
    >
      <div className="event-form">
        {editing && event && (
          <div className="event-form-context">
            <span className="event-form-status">
              {EVENT_STATUS_LABELS[event.status]}
            </span>
            {event.remind_at && (
              <span className="event-form-remind">
                <Icon name="bell" size={12} />
                напоминание
              </span>
            )}
          </div>
        )}

        {!editing && !candidate && (
          <Field label="Кандидат" required>
            {(id, describedBy) => (
              <div className="candidate-picker">
                {pickedCandidate ? (
                  <div className="candidate-picker-selected">
                    <span>{pickedCandidate.full_name}</span>
                    <StageChip stage={pickedCandidate.stage} size="sm" />
                    <Button variant="ghost" size="sm" onClick={() => setPickedCandidate(null)}>
                      Изменить
                    </Button>
                  </div>
                ) : (
                  <>
                    <TextInput
                      id={id}
                      aria-describedby={describedBy}
                      value={candidateQuery}
                      onChange={(event) => setCandidateQuery(event.target.value)}
                      placeholder="Поиск по ФИО, телефону или email…"
                    />
                    {searching && <p className="muted-text">Поиск…</p>}
                    {!searching && suggestions.length > 0 && (
                      <ul className="candidate-picker-list" role="listbox">
                        {suggestions.map((item) => (
                          <li key={item.id}>
                            <button
                              type="button"
                              role="option"
                              aria-selected={false}
                              onClick={() => {
                                setPickedCandidate(item);
                                setCandidateQuery("");
                              }}
                            >
                              <span className="candidate-picker-name">{item.full_name}</span>
                              <StageChip stage={item.stage} size="sm" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
              </div>
            )}
          </Field>
        )}

        <Field label="Тип события">
          {(id) => (
            <SelectInput
              id={id}
              value={draft.type}
              disabled={editing}
              onChange={(event) => setDraft({ ...draft, type: event.target.value as CalendarEventType })}
            >
              {(Object.keys(EVENT_TYPE_LABELS) as CalendarEventType[]).map((item) => (
                <option key={item} value={item}>
                  {EVENT_TYPE_LABELS[item]}
                </option>
              ))}
            </SelectInput>
          )}
        </Field>

        <Field label="Название" required>
          {(id) => (
            <TextInput
              id={id}
              value={draft.title}
              invalid={Boolean(error)}
              onChange={(event) => setDraft({ ...draft, title: event.target.value })}
            />
          )}
        </Field>

        <Field label="Начало" required>
          {(id) => (
            <TextInput
              id={id}
              type="datetime-local"
              value={draft.startsAt}
              onChange={(event) => setDraft({ ...draft, startsAt: event.target.value })}
            />
          )}
        </Field>

        <Field label="Окончание" hint="Необязательно">
          {(id) => (
            <TextInput
              id={id}
              type="datetime-local"
              value={draft.endsAt}
              onChange={(event) => setDraft({ ...draft, endsAt: event.target.value })}
            />
          )}
        </Field>

        <Field
          label="Напоминание"
          hint={draft.type === "reminder" ? "Момент напоминания — это дата начала" : "Необязательно, не позже начала"}
        >
          {(id) => (
            <TextInput
              id={id}
              type="datetime-local"
              value={draft.remindAt}
              disabled={draft.type === "reminder"}
              onChange={(event) => setDraft({ ...draft, remindAt: event.target.value })}
            />
          )}
        </Field>

        {canAssign && (
          <Field label="Исполнитель" required hint="Активный HR">
            {(id) => (
              <SelectInput
                id={id}
                value={assigneeId}
                onChange={(event) => setAssigneeId(event.target.value)}
              >
                <option value="">Выберите исполнителя</option>
                {directory.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.full_name || item.username}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
        )}

        <Field label="Заметка" hint="Необязательно">
          {(id) => (
            <TextInput
              id={id}
              value={draft.note}
              onChange={(event) => setDraft({ ...draft, note: event.target.value })}
            />
          )}
        </Field>

        {error && (
          <p className="field-error" role="alert">
            {error}
          </p>
        )}

        {editing && event && (
          <section className="event-history" aria-label="История изменений события">
            <h3 className="event-history-title">История изменений</h3>
            {historyLoading && <SkeletonRows rows={2} columns={2} />}
            {!historyLoading && history.length === 0 && (
              <p className="muted-text">Записей пока нет.</p>
            )}
            {!historyLoading && history.length > 0 && (
              <ul className="event-history-list">
                {history.map((item) => (
                  <li key={item.id} className="event-history-item">
                    <span className="event-history-kind">
                      {EVENT_HISTORY_KIND_LABELS[item.kind]}
                    </span>
                    <span className="event-history-meta">
                      {item.changed_by_username} · {formatDateTime(item.created_at)}
                    </span>
                    {item.starts_at_new && (
                      <span className="event-history-detail">
                        новое начало: {formatDateTime(item.starts_at_new)}
                      </span>
                    )}
                    {item.assignee_user_id_new && (
                      <span className="event-history-detail">исполнитель изменён</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {editing && event && onOpenCandidate && (
          <Button
            variant="secondary"
            size="sm"
            icon="users"
            onClick={() => onOpenCandidate(event.candidate_id)}
          >
            Открыть карточку кандидата
          </Button>
        )}
      </div>
    </Modal>
  );
}
