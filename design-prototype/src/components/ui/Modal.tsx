import { type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "./useFocusTrap";
import { IconButton } from "./Button";
import "./modal.css";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** Для необратимых действий: alertdialog вместо dialog. */
  destructive?: boolean;
}

/**
 * Базовый accessible-модал: role=dialog, aria-modal, focus trap, Escape,
 * возврат фокуса, клик по оверлею закрывает. См. design/ACCESSIBILITY.md.
 */
export function Modal({ open, onClose, title, description, children, footer, size = "md", destructive }: ModalProps) {
  const containerRef = useFocusTrap(open, onClose);

  if (!open) return null;

  return createPortal(
    <div className="modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        ref={containerRef}
        className={`modal-panel modal-${size}`}
        role={destructive ? "alertdialog" : "dialog"}
        aria-modal="true"
        aria-labelledby="modal-title"
        aria-describedby={description ? "modal-description" : undefined}
      >
        <div className="modal-head">
          <h2 id="modal-title" className="modal-title">{title}</h2>
          <IconButton icon="close" label="Закрыть окно" onClick={onClose} size="sm" />
        </div>
        {description && (
          <p id="modal-description" className="modal-description">
            {description}
          </p>
        )}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
