import { useEffect, useMemo, useState } from "react";
import { ApiError, listHrUsers, transferCandidate } from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import { useToast } from "../../design-system/components/ToastContext";
import type { Candidate, CandidateTransfer, User, UserListItem } from "../../types";

interface TransferDialogProps {
  open: boolean;
  candidate: Candidate;
  user: User;
  onClose: () => void;
  onDone: (candidate: Candidate, transfer: CandidateTransfer) => void;
}

/** Two-step ownership transfer: pick a new HR + reason, then confirm. */
export function TransferDialog({ open, candidate, onClose, onDone }: TransferDialogProps) {
  const { pushToast } = useToast();
  const [step, setStep] = useState<"pick" | "confirm">("pick");
  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [newOwnerId, setNewOwnerId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const selectable = useMemo(
    () => directory.filter((item) => item.id !== candidate.owner_user_id),
    [directory, candidate.owner_user_id]
  );
  const newOwner = selectable.find((item) => item.id === newOwnerId) ?? null;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {
        if (!cancelled) setError("Не удалось загрузить список HR.");
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const reset = () => {
    setStep("pick");
    setNewOwnerId("");
    setReason("");
    setError(null);
    setSending(false);
  };

  const close = () => {
    reset();
    onClose();
  };

  const proceedToConfirm = () => {
    if (!newOwnerId) {
      setError("Выберите нового ответственного.");
      return;
    }
    if (!reason.trim()) {
      setError("Укажите причину передачи.");
      return;
    }
    setError(null);
    setStep("confirm");
  };

  const confirmTransfer = async () => {
    if (!newOwnerId || !reason.trim()) return;
    setSending(true);
    setError(null);
    try {
      const result = await transferCandidate(candidate.id, {
        new_owner_user_id: newOwnerId,
        reason: reason.trim(),
      });
      reset();
      onDone(result.candidate, result.transfer);
    } catch (caught) {
      // Nothing is visually transferred on failure — return to step 1.
      setStep("pick");
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Не удалось передать кандидата. Попробуйте ещё раз."
      );
      pushToast("danger", "Передача не выполнена.");
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title={step === "pick" ? "Передача кандидата" : "Подтвердите передачу"}
      description={
        step === "pick"
          ? `Шаг 1 из 2: выберите нового ответственного HR и укажите причину передачи.`
          : `Шаг 2 из 2: проверьте данные — после подтверждения операция попадёт в неизменяемую историю кандидата.`
      }
      size="md"
      footer={
        step === "pick" ? (
          <>
            <Button variant="secondary" onClick={close}>
              Отмена
            </Button>
            <Button onClick={proceedToConfirm}>Далее</Button>
          </>
        ) : (
          <>
            <Button variant="secondary" onClick={() => setStep("pick")} disabled={sending}>
              Назад
            </Button>
            <Button loading={sending} disabled={sending} onClick={() => void confirmTransfer()}>
              Подтвердить передачу
            </Button>
          </>
        )
      }
    >
      {step === "pick" && (
        <div className="transfer-form">
          <Field label="Новый ответственный HR" required error={error ?? undefined}>
            {(id, describedBy) => (
              <SelectInput
                id={id}
                aria-describedby={describedBy}
                value={newOwnerId}
                onChange={(event) => setNewOwnerId(event.target.value)}
              >
                <option value="">Выберите HR…</option>
                {selectable.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.full_name || item.username}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Причина передачи" required>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Например: перераспределение нагрузки"
              />
            )}
          </Field>
        </div>
      )}

      {step === "confirm" && newOwner && (
        <div className="transfer-confirm" role="group" aria-label="Детали передачи">
          <div className="transfer-summary-row">
            <span className="transfer-summary-label">Кандидат</span>
            <span className="transfer-summary-value">{candidate.full_name}</span>
          </div>
          <div className="transfer-summary-row">
            <span className="transfer-summary-label">Текущий ответственный</span>
            <span className="transfer-summary-value">{candidate.owner_username}</span>
          </div>
          <div className="transfer-summary-row">
            <span className="transfer-summary-label">Новый ответственный</span>
            <span className="transfer-summary-value">
              {newOwner.full_name || newOwner.username}
            </span>
          </div>
          <div className="transfer-summary-row">
            <span className="transfer-summary-label">Причина</span>
            <span className="transfer-summary-value">{reason.trim()}</span>
          </div>
        </div>
      )}
    </Modal>
  );
}
