import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { useFocusTrap } from "./useFocusTrap";
import { IconButton } from "./Button";
import "./drawer.css";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: number;
  headerActions?: ReactNode;
}

export function Drawer({ open, onClose, title, children, width = 480, headerActions }: DrawerProps) {
  const containerRef = useFocusTrap(open, onClose);
  if (!open) return null;

  return createPortal(
    <div className="drawer-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        ref={containerRef}
        className="drawer-panel"
        style={{ width }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
      >
        <div className="drawer-head">
          <h2 id="drawer-title" className="drawer-title">{title}</h2>
          <div className="drawer-head-actions">
            {headerActions}
            <IconButton icon="close" label="Закрыть панель" onClick={onClose} size="sm" />
          </div>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
