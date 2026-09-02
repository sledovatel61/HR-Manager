import { useState } from "react";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { Field, SelectInput } from "../../components/ui/Field";
import { Avatar } from "../../components/ui/Avatar";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { useAppState } from "../../state/AppState";
import { candidateById, USERS, userById } from "../../data/mockData";

/**
 * Передача кандидата другому HR — обязательный сценарий из PRODUCT_SPEC.md:
 * "Передача кандидата — отдельная операция с причиной, инициатором, старым
 * и новым ответственным и записью в audit log" (agents.md, п.4). Здесь это
 * промоделировано двумя шагами: выбор нового ответственного + причины, затем
 * явное подтверждение.
 */
export function TransferDialog({ open, onClose, candidateId }: { open: boolean; onClose: () => void; candidateId: string }) {
  const { transferCandidate, addInteraction, pushToast } = useAppState();
  const candidate = candidateById(candidateId);
  const hrUsers = USERS.filter((u) => u.role === "hr" && u.id !== candidate?.ownerId);
  const [newOwnerId, setNewOwnerId] = useState(hrUsers[0]?.id ?? "");
  const [reason, setReason] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  if (!candidate) return null;
  const currentOwner = userById(candidate.ownerId);
  const nextOwner = userById(newOwnerId);

  function handleConfirm() {
    if (!nextOwner) return;
    transferCandidate(candidate!.id, nextOwner.id, reason);
    addInteraction({
      candidateId: candidate!.id,
      type: "transfer",
      authorId: currentOwner?.id ?? "u-anna",
      summary: "Кандидат передан другому HR",
      detail: reason || undefined,
      fromOwnerId: currentOwner?.id,
      toOwnerId: nextOwner.id,
    });
    setConfirmOpen(false);
    onClose();
    pushToast("success", `Кандидат передан: ${nextOwner.fullName}.`);
  }

  return (
    <>
      <Modal
        open={open && !confirmOpen}
        onClose={onClose}
        title="Передать кандидата"
        description={`Текущий ответственный: ${currentOwner?.fullName ?? "—"}`}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={onClose}>Отмена</Button>
            <Button variant="primary" onClick={() => setConfirmOpen(true)} disabled={!newOwnerId}>
              Далее
            </Button>
          </>
        }
      >
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
          <Field label="Новый ответственный" required>
            {(id) => (
              <SelectInput id={id} value={newOwnerId} onChange={(e) => setNewOwnerId(e.target.value)}>
                {hrUsers.map((u) => (
                  <option key={u.id} value={u.id}>{u.fullName}</option>
                ))}
              </SelectInput>
            )}
          </Field>
          {nextOwner && (
            <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", fontSize: "var(--text-size-sm)" }}>
              <Avatar initials={nextOwner.initials} color={nextOwner.avatarColor} size="sm" />
              {nextOwner.fullName} — {nextOwner.title}
            </div>
          )}
          <Field label="Причина передачи" hint="Будет сохранена в истории и журнале аудита.">
            {(id, describedBy) => (
              <textarea
                id={id}
                aria-describedby={describedBy}
                rows={3}
                className="note-textarea"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Например: перераспределение нагрузки между HR"
              />
            )}
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={handleConfirm}
        title="Подтвердите передачу"
        description={`Кандидат «${candidate.fullName}» будет передан от ${currentOwner?.fullName ?? "—"} к ${nextOwner?.fullName ?? "—"}. Действие будет зафиксировано в журнале аудита.`}
        confirmLabel="Передать кандидата"
      />
    </>
  );
}
