import { Button } from "../../design-system/components/Button";
import { Modal } from "../../design-system/components/Modal";
import { StageChip } from "../../design-system/components/StatusChip";
import type { Candidate } from "../../types";

interface DuplicateResolveDialogProps {
  duplicates: Candidate[];
  busy?: boolean;
  onCancel: () => void;
  /** Resubmit the create/update with confirm_duplicate: true. */
  onConfirm: () => void;
  /** Open one of the matching candidates. */
  onOpenMatch: (id: string) => void;
}

/** PRODUCT_SPEC §4 duplicate flow: show matches, open one or confirm a copy. */
export function DuplicateResolveDialog({
  duplicates,
  busy = false,
  onCancel,
  onConfirm,
  onOpenMatch,
}: DuplicateResolveDialogProps) {
  return (
    <Modal
      open
      onClose={onCancel}
      title="Найдены похожие кандидаты"
      description="Телефон или email совпадают с уже существующими кандидатами. Откройте совпадение или явно подтвердите создание/сохранение."
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onCancel}>
            Отмена
          </Button>
          <Button loading={busy} disabled={busy} onClick={onConfirm}>
            Всё равно сохранить
          </Button>
        </>
      }
    >
      <ul className="duplicate-list">
        {duplicates.map((item) => (
          <li key={item.id} className="duplicate-item">
            <span className="duplicate-name">{item.full_name}</span>
            <StageChip stage={item.stage} size="sm" />
            <span className="duplicate-owner">Ответственный: {item.owner_username}</span>
            <Button variant="secondary" size="sm" onClick={() => onOpenMatch(item.id)}>
              Открыть
            </Button>
          </li>
        ))}
      </ul>
    </Modal>
  );
}
