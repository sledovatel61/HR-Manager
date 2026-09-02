import { useAppState } from "../../state/AppState";
import { Icon } from "../../icons/Icon";
import "./toast.css";

const TONE_ICON = { success: "check-circle", info: "info", danger: "alert-triangle" } as const;

/**
 * Toast-контейнер. aria-live="polite" + role="status" объявляет новые
 * сообщения screen reader без похищения фокуса (WCAG 4.1.3 Status Messages).
 */
export function ToastViewport() {
  const { toasts, dismissToast } = useAppState();
  return (
    <div className="toast-viewport" aria-live="polite" role="status">
      {toasts.map((t) => (
        <div className={`toast toast-${t.tone}`} key={t.id}>
          <Icon name={TONE_ICON[t.tone]} size={16} />
          <span className="toast-message">{t.message}</span>
          <button type="button" className="toast-close" onClick={() => dismissToast(t.id)} aria-label="Скрыть уведомление">
            <Icon name="close" size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}
