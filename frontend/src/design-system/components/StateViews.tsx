import type { ReactNode } from "react";
import { Icon, type IconName } from "../icons/Icon";
import { Button } from "./Button";
import "./stateViews.css";

interface StateProps {
  icon: IconName;
  title: string;
  description?: string;
  action?: ReactNode;
  tone?: "neutral" | "danger" | "warning";
}

export function StateView({ icon, title, description, action, tone = "neutral" }: StateProps) {
  return (
    <div className={`state-view state-view-${tone}`} role={tone === "danger" ? "alert" : undefined}>
      <div className="state-view-icon">
        <Icon name={icon} size={22} />
      </div>
      <h3 className="state-view-title">{title}</h3>
      {description && <p className="state-view-description">{description}</p>}
      {action}
    </div>
  );
}

export function EmptyState({
  title = "Здесь пока пусто",
  description = "Как только появятся кандидаты, они будут показаны здесь.",
  action,
  icon = "inbox",
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
  icon?: IconName;
}) {
  return <StateView icon={icon} title={title} description={description} action={action} />;
}

export function ErrorState({ onRetry }: { onRetry?: () => void }) {
  return (
    <StateView
      icon="wifi-off"
      tone="danger"
      title="Не удалось загрузить данные"
      description="Backend недоступен или сеть нестабильна. Изменения не потеряны — повторите попытку."
      action={
        onRetry && (
          <Button variant="secondary" icon="loader" onClick={onRetry}>
            Повторить попытку
          </Button>
        )
      }
    />
  );
}

export function PermissionDeniedState() {
  return (
    <StateView
      icon="lock"
      tone="warning"
      title="Недостаточно прав"
      description="Этот раздел доступен только определённым ролям. Если это ошибка — обратитесь к администратору системы."
    />
  );
}

export function SessionExpiredState({ onRestore }: { onRestore: () => void }) {
  return (
    <StateView
      icon="clock"
      tone="warning"
      title="Сессия истекла"
      description="Для продолжения работы войдите в систему заново. Несохранённые черновики форм могут быть потеряны."
      action={
        <Button variant="primary" onClick={onRestore}>
          Войти снова
        </Button>
      }
    />
  );
}

export function SkeletonRows({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="skeleton-block" aria-hidden="true">
      {Array.from({ length: rows }).map((_, r) => (
        <div className="skeleton-row" key={r}>
          {Array.from({ length: columns }).map((_, c) => (
            <span className="skeleton-cell" key={c} style={{ width: c === 0 ? "28%" : `${60 - c * 8}%` }} />
          ))}
        </div>
      ))}
      <span className="sr-only">Загрузка данных…</span>
    </div>
  );
}

export function SkeletonCards({ count = 6 }: { count?: number }) {
  return (
    <div className="skeleton-cards" aria-hidden="true">
      {Array.from({ length: count }).map((_, i) => (
        <div className="skeleton-card" key={i}>
          <span className="skeleton-line" style={{ width: "60%" }} />
          <span className="skeleton-line" style={{ width: "40%" }} />
          <span className="skeleton-line" style={{ width: "80%" }} />
        </div>
      ))}
      <span className="sr-only">Загрузка данных…</span>
    </div>
  );
}
