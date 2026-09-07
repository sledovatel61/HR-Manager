import { useState } from "react";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { Field, SelectInput, TextInput } from "../../components/ui/Field";
import { useAppState } from "../../state/AppState";
import { candidateById } from "../../data/mockData";
import type { EventType } from "../../types";

export function ScheduleEventForm({ open, onClose, candidateId }: { open: boolean; onClose: () => void; candidateId: string }) {
  const { addEvent, pushToast } = useAppState();
  const candidate = candidateById(candidateId);
  const [type, setType] = useState<EventType>("call");
  const [title, setTitle] = useState("Повторный звонок");
  const [date, setDate] = useState("2026-09-05");
  const [time, setTime] = useState("11:00");
  const [location, setLocation] = useState("Телефон");

  if (!candidate) return null;

  function handleSubmit() {
    addEvent({
      candidateId: candidate!.id,
      type,
      status: "planned",
      title,
      ownerId: candidate!.ownerId,
      startsAt: new Date(`${date}T${time}:00`).toISOString(),
      durationMinutes: 30,
      location,
    });
    pushToast("success", "Событие добавлено в календарь.");
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Запланировать событие"
      description={`Кандидат: ${candidate.fullName}`}
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Отмена</Button>
          <Button variant="primary" onClick={handleSubmit} disabled={!title.trim()}>
            Запланировать
          </Button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        <Field label="Тип события">
          {(id) => (
            <SelectInput id={id} value={type} onChange={(e) => setType(e.target.value as EventType)}>
              <option value="call">Звонок</option>
              <option value="interview">Собеседование</option>
              <option value="meeting">Встреча</option>
              <option value="reminder">Напоминание</option>
            </SelectInput>
          )}
        </Field>
        <Field label="Название" required>
          {(id) => <TextInput id={id} value={title} onChange={(e) => setTitle(e.target.value)} required />}
        </Field>
        <div style={{ display: "flex", gap: "var(--space-3)" }}>
          <Field label="Дата" required>
            {(id) => <TextInput id={id} type="date" value={date} onChange={(e) => setDate(e.target.value)} required />}
          </Field>
          <Field label="Время" required>
            {(id) => <TextInput id={id} type="time" value={time} onChange={(e) => setTime(e.target.value)} required />}
          </Field>
        </div>
        <Field label="Место / способ связи">
          {(id) => <TextInput id={id} value={location} onChange={(e) => setLocation(e.target.value)} />}
        </Field>
      </div>
    </Modal>
  );
}
