import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  applyCandidateDocumentList,
  getCandidateDocuments,
  listPublishedDocumentLists,
  sendCandidateDocumentMessage,
  updateCandidateDocumentItem,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput } from "../../design-system/components/Field";
import { EmptyState, ErrorState, SkeletonRows } from "../../design-system/components/StateViews";
import { Badge } from "../../design-system/components/StatusChip";
import { useToast } from "../../design-system/components/ToastContext";
import type {
  Candidate,
  CandidateChannelName,
  CandidateDocumentAssignment,
  CandidateDocumentItem,
  CandidateDocumentMessageType,
  CandidateDocuments,
  PublishedDocumentList,
} from "../../types";
import { formatDateTime } from "./format";
import "../automation/automation.css";

/** A client idempotency key: one per exact operation attempt. */
function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

const CHANNEL_LABELS: Record<CandidateChannelName, string> = {
  email: "Электронная почта",
  telegram: "Telegram",
};

interface DocumentsTabProps {
  candidate: Candidate;
}

/**
 * Вкладка «Документы»: точный снимок применённой версии списка.
 *
 * Кандидат получает копию элементов ровно той версии, которая была
 * опубликована в момент применения; последующие правки списка её не
 * меняют. Отметки «получен/нет» защищены оптимистической версией строки:
 * при конфликте показывается явное сообщение и снимок перечитывается.
 * Запрос/напоминание формирует сервер только из ещё недостающих
 * документов и только по разрешённым каналам — текст и адрес клиент не
 * передаёт.
 */
export function DocumentsTab({ candidate }: DocumentsTabProps) {
  const { pushToast } = useToast();
  const [data, setData] = useState<CandidateDocuments | null>(null);
  const [lists, setLists] = useState<PublishedDocumentList[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [selectedList, setSelectedList] = useState("");
  const [busyItem, setBusyItem] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [replaceOpen, setReplaceOpen] = useState(false);
  const [sending, setSending] = useState<CandidateDocumentMessageType | null>(null);
  const [channel, setChannel] = useState<CandidateChannelName | "">("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [documents, published] = await Promise.all([
        getCandidateDocuments(candidate.id),
        listPublishedDocumentLists(),
      ]);
      setData(documents);
      setLists(published.items);
      setConflict(null);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [candidate.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const reportError = (caught: unknown, fallback: string) => {
    if (caught instanceof ApiError) {
      if (caught.status === 409) {
        setConflict(caught.message);
        return;
      }
      pushToast("danger", caught.message || fallback);
      return;
    }
    pushToast("danger", fallback);
  };

  const apply = async (replace: boolean) => {
    if (!selectedList) {
      pushToast("info", "Выберите опубликованный список документов.");
      return;
    }
    setApplying(true);
    setConflict(null);
    try {
      const next = await applyCandidateDocumentList(candidate.id, selectedList, replace);
      setData(next);
      setSelectedList("");
      pushToast("success", "Список документов применён к кандидату.");
    } catch (caught) {
      reportError(caught, "Не удалось применить список.");
    } finally {
      setApplying(false);
      setReplaceOpen(false);
    }
  };

  const toggleItem = async (item: CandidateDocumentItem) => {
    setBusyItem(item.id);
    setConflict(null);
    try {
      const next = await updateCandidateDocumentItem(candidate.id, item.id, {
        status: item.status === "received" ? "missing" : "received",
        expected_version: item.version,
      });
      setData(next);
    } catch (caught) {
      reportError(caught, "Не удалось изменить отметку документа.");
    } finally {
      setBusyItem(null);
    }
  };

  const send = async (type: CandidateDocumentMessageType) => {
    setSending(type);
    setConflict(null);
    try {
      const result = await sendCandidateDocumentMessage(candidate.id, {
        message_type: type,
        channel: channel || undefined,
        idempotency_key: newIdempotencyKey(),
      });
      const channelsText = result.channels.map((name) => CHANNEL_LABELS[name]).join(", ");
      pushToast(
        "success",
        `${type === "document_request" ? "Запрос" : "Напоминание"} поставлен в очередь (${channelsText}). Отправка в разрешённое время; статус — во вкладке «Сообщения».`,
      );
    } catch (caught) {
      reportError(caught, "Не удалось поставить сообщение в очередь.");
    } finally {
      setSending(null);
    }
  };

  if (loading) {
    return <SkeletonRows rows={4} columns={2} />;
  }
  if (error || !data) {
    return <ErrorState onRetry={() => void load()} />;
  }

  const current = data.current;
  const missingCount = current ? current.items.filter((item) => item.status === "missing").length : 0;
  const readOnly = candidate.is_deleted;

  return (
    <div className="cand-docs">
      {conflict && (
        <div className="form-error" role="alert">
          {conflict}{" "}
          <Button variant="secondary" size="sm" onClick={() => void load()}>
            Обновить
          </Button>
        </div>
      )}

      {current ? (
        <section aria-label="Применённый список документов">
          <div className="cand-docs-head">
            <div>
              <h3 className="messages-section-title">
                {current.list_name} · версия {current.version_number}
              </h3>
              <p className="cand-docs-meta">
                применён {formatDateTime(current.assigned_at)}
                {current.assigned_by_username
                  ? ` · ${current.assigned_by_username}`
                  : current.assigned_by_rule_id
                    ? " · автоматически по правилу"
                    : ""}
              </p>
            </div>
            <div className="cand-docs-actions">
              <Badge tone={current.missing_required_count > 0 ? "amber" : "success"}>
                {current.missing_required_count > 0
                  ? `Не хватает обязательных: ${current.missing_required_count}`
                  : "Все обязательные получены"}
              </Badge>
            </div>
          </div>

          <ul className="cand-docs-list">
            {current.items.map((item) => {
              const received = item.status === "received";
              return (
                <li
                  key={item.id}
                  className={`cand-docs-item ${received ? "is-received" : ""}`}
                >
                  <input
                    type="checkbox"
                    id={`doc-${item.id}`}
                    checked={received}
                    disabled={readOnly || busyItem === item.id}
                    aria-label={`${item.name}: ${received ? "получен" : "не получен"}`}
                    onChange={() => void toggleItem(item)}
                  />
                  <label htmlFor={`doc-${item.id}`} className="cand-docs-item-name">
                    {item.name}
                    {!item.is_required && <span className="muted-text"> (необязательный)</span>}
                  </label>
                  <Badge tone={received ? "success" : "neutral"}>
                    {received ? "Получен" : "Не получен"}
                  </Badge>
                  {(item.explanation || item.changed_at) && (
                    <span className="cand-docs-item-note">
                      {item.explanation}
                      {item.explanation && item.changed_at ? " · " : ""}
                      {item.changed_at &&
                        `отмечено ${formatDateTime(item.changed_at)}${
                          item.changed_by_username ? `, ${item.changed_by_username}` : ""
                        }`}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          {!readOnly && (
            <div className="cand-docs-apply" aria-label="Сообщение о документах">
              <Field
                label="Канал"
                hint="Пусто — все каналы, разрешённые кандидатом. Текст формирует сервер из недостающих документов."
              >
                {(id, describedBy) => (
                  <SelectInput
                    id={id}
                    aria-describedby={describedBy}
                    value={channel}
                    onChange={(event) =>
                      setChannel(event.target.value as CandidateChannelName | "")
                    }
                  >
                    <option value="">— любой разрешённый —</option>
                    <option value="email">{CHANNEL_LABELS.email}</option>
                    <option value="telegram">{CHANNEL_LABELS.telegram}</option>
                  </SelectInput>
                )}
              </Field>
              <Button
                variant="primary"
                size="sm"
                icon="mail"
                disabled={missingCount === 0 || sending !== null}
                loading={sending === "document_request"}
                onClick={() => void send("document_request")}
              >
                Запросить документы
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={missingCount === 0 || sending !== null}
                loading={sending === "document_reminder"}
                onClick={() => void send("document_reminder")}
              >
                Напомнить
              </Button>
              {missingCount === 0 && (
                <p className="muted-text">Все документы получены — отправлять нечего.</p>
              )}
            </div>
          )}
        </section>
      ) : (
        <EmptyState
          icon="file-text"
          title="Список документов не применён"
          description="Выберите опубликованный список — кандидат получит точную копию его текущей версии."
        />
      )}

      {!readOnly && (
        <section className="cand-docs-apply" aria-label="Применить список документов">
          <Field label={current ? "Заменить список" : "Применить список"}>
            {(id) => (
              <SelectInput
                id={id}
                value={selectedList}
                onChange={(event) => setSelectedList(event.target.value)}
              >
                <option value="">— выберите список —</option>
                {lists.map((list) => (
                  <option key={list.id} value={list.id}>
                    {list.name} (версия {list.published_version_number})
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Button
            variant="secondary"
            size="sm"
            loading={applying}
            disabled={!selectedList}
            onClick={() => (current ? setReplaceOpen(true) : void apply(false))}
          >
            {current ? "Заменить" : "Применить"}
          </Button>
        </section>
      )}

      {data.history.length > 0 && (
        <details className="cand-docs-history">
          <summary>История списков ({data.history.length})</summary>
          <ul className="cand-docs-list">
            {data.history.map((assignment: CandidateDocumentAssignment) => (
              <li key={assignment.id} className="cand-docs-item">
                <span className="cand-docs-item-name">
                  {assignment.list_name} · версия {assignment.version_number}
                </span>
                <span className="cand-docs-item-note">
                  применён {formatDateTime(assignment.assigned_at)} · получено{" "}
                  {assignment.received_count} из {assignment.items.length}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <ConfirmDialog
        open={replaceOpen}
        onCancel={() => setReplaceOpen(false)}
        onConfirm={() => void apply(true)}
        title="Заменить список документов?"
        description="Текущий список будет закрыт и сохранён в истории; отметки о полученных документах в новом списке начнутся заново. Отложенные запросы по старому списку не уйдут."
        confirmLabel="Заменить"
      />
    </div>
  );
}
