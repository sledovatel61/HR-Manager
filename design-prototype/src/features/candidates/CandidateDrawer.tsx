import { useState } from "react";
import { Drawer } from "../../components/ui/Drawer";
import { Avatar } from "../../components/ui/Avatar";
import { StageChip } from "../../components/ui/StatusChip";
import { Button } from "../../components/ui/Button";
import { Tabs } from "../../components/ui/Tabs";
import { Field, SelectInput } from "../../components/ui/Field";
import { Icon } from "../../icons/Icon";
import { Timeline } from "./Timeline";
import { TransferDialog } from "./TransferDialog";
import { ScheduleEventForm } from "./ScheduleEventForm";
import { useAppState } from "../../state/AppState";
import { candidateById, userById } from "../../data/mockData";
import { SOURCE_LABELS, STAGE_LABELS, STAGE_ORDER, type CandidateStage } from "../../types";
import { formatDate, formatDateTime } from "../../utils/format";
import "./candidateDrawer.css";

export function CandidateDrawer({ candidateId, onClose }: { candidateId: string | null; onClose: () => void }) {
  const { interactions, events, updateCandidateStage, addInteraction, pushToast } = useAppState();
  const [tab, setTab] = useState("overview");
  const [transferOpen, setTransferOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState("");

  if (!candidateId) return null;
  const candidate = candidateById(candidateId);
  if (!candidate) return null;

  const owner = userById(candidate.ownerId);
  const liveInteractions = interactions
    .filter((i) => i.candidateId === candidateId)
    .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
  const candidateEvents = events.filter((e) => e.candidateId === candidateId);

  function handleAddNote() {
    if (!noteDraft.trim()) return;
    addInteraction({
      candidateId: candidate!.id,
      type: "note",
      authorId: owner?.id ?? "u-anna",
      summary: "Комментарий рекрутера",
      detail: noteDraft.trim(),
    });
    setNoteDraft("");
    pushToast("success", "Комментарий добавлен в историю взаимодействий.");
  }

  return (
    <Drawer
      open={Boolean(candidateId)}
      onClose={onClose}
      title={candidate.fullName}
      width={560}
      headerActions={
        <Button variant="secondary" size="sm" icon="arrow-right-left" onClick={() => setTransferOpen(true)}>
          Передать
        </Button>
      }
    >
      <div className="candidate-profile">
        <div className="candidate-profile-head">
          <Avatar initials={candidate.initials} color={candidate.avatarColor} size="lg" name={candidate.fullName} />
          <div>
            <h3 className="candidate-profile-name">{candidate.fullName}</h3>
            <p className="candidate-profile-position">{candidate.position} · {candidate.department}</p>
            <div className="candidate-profile-badges">
              <StageChip stage={candidate.stage} />
              {candidate.tags.map((t) => (
                <span key={t} className="candidate-tag">{t}</span>
              ))}
            </div>
          </div>
        </div>

        <div className="candidate-quick-grid">
          <div>
            <span className="quick-label">Телефон</span>
            <span className="quick-value"><Icon name="phone" size={13} /> {candidate.phoneMasked}</span>
          </div>
          <div>
            <span className="quick-label">Email</span>
            <span className="quick-value"><Icon name="mail" size={13} /> {candidate.emailMasked}</span>
          </div>
          <div>
            <span className="quick-label">Город</span>
            <span className="quick-value">{candidate.city}</span>
          </div>
          <div>
            <span className="quick-label">Источник</span>
            <span className="quick-value">{SOURCE_LABELS[candidate.source]}</span>
          </div>
          <div>
            <span className="quick-label">Ожидания по ЗП</span>
            <span className="quick-value">{candidate.salaryExpectation}</span>
          </div>
          <div>
            <span className="quick-label">Ответственный</span>
            <span className="quick-value">{owner?.fullName ?? "—"}</span>
          </div>
        </div>

        <Field label="Изменить этап">
          {(id) => (
            <SelectInput
              id={id}
              value={candidate.stage}
              onChange={(e) => {
                const next = e.target.value as CandidateStage;
                updateCandidateStage(candidate.id, next);
                addInteraction({
                  candidateId: candidate.id,
                  type: "status_change",
                  authorId: owner?.id ?? "u-anna",
                  summary: "Статус изменён",
                  fromStage: candidate.stage,
                  toStage: next,
                });
                pushToast("success", `Статус обновлён: «${STAGE_LABELS[next]}».`);
              }}
            >
              {STAGE_ORDER.map((s) => (
                <option key={s} value={s}>{STAGE_LABELS[s]}</option>
              ))}
            </SelectInput>
          )}
        </Field>

        <Tabs
          ariaLabel="Разделы карточки кандидата"
          activeId={tab}
          onChange={setTab}
          items={[
            { id: "overview", label: "Обзор" },
            { id: "timeline", label: "История", count: liveInteractions.length },
            { id: "events", label: "События", count: candidateEvents.length },
          ]}
        />

        {tab === "overview" && (
          <div className="tab-panel">
            <h4 className="tab-panel-title">Комментарий</h4>
            <p className="candidate-note-preview">{candidate.notesPreview}</p>
            <h4 className="tab-panel-title">Добавить взаимодействие</h4>
            <Field label="Новый комментарий">
              {(id, describedBy) => (
                <textarea
                  id={id}
                  aria-describedby={describedBy}
                  className="note-textarea"
                  rows={3}
                  value={noteDraft}
                  onChange={(e) => setNoteDraft(e.target.value)}
                  placeholder="Например: кандидат просил перезвонить после 18:00"
                />
              )}
            </Field>
            <div className="tab-panel-actions">
              <Button variant="secondary" icon="calendar" onClick={() => setScheduleOpen(true)}>
                Запланировать событие
              </Button>
              <Button variant="primary" icon="plus" onClick={handleAddNote} disabled={!noteDraft.trim()}>
                Добавить запись
              </Button>
            </div>
          </div>
        )}

        {tab === "timeline" && (
          <div className="tab-panel">
            <Timeline interactions={liveInteractions} />
          </div>
        )}

        {tab === "events" && (
          <div className="tab-panel">
            {candidateEvents.length === 0 && <p className="timeline-empty">Событий пока нет.</p>}
            <ul className="event-list">
              {candidateEvents.map((ev) => (
                <li key={ev.id} className="event-list-item">
                  <div>
                    <p className="event-list-title">{ev.title}</p>
                    <p className="event-list-meta">{formatDateTime(ev.startsAt)} · {ev.location}</p>
                  </div>
                  <span className={`event-status event-status-${ev.status}`}>
                    {ev.status === "planned" ? "Запланировано" : ev.status === "done" ? "Выполнено" : ev.status === "postponed" ? "Перенесено" : "Отменено"}
                  </span>
                </li>
              ))}
            </ul>
            <Button variant="secondary" icon="plus" onClick={() => setScheduleOpen(true)}>
              Добавить событие
            </Button>
          </div>
        )}

        <p className="candidate-created-at">Кандидат создан {formatDate(candidate.createdAt)}</p>
      </div>

      <TransferDialog open={transferOpen} onClose={() => setTransferOpen(false)} candidateId={candidate.id} />
      <ScheduleEventForm open={scheduleOpen} onClose={() => setScheduleOpen(false)} candidateId={candidate.id} />
    </Drawer>
  );
}
