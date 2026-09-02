import { useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { Icon, type IconName } from "../icons/Icon";
import { Badge } from "../components/ui/StatusChip";
import { PermissionDeniedState } from "../components/ui/StateViews";
import { SelectInput } from "../components/ui/Field";
import { useAppState } from "../state/AppState";
import { AUDIT_LOG, userById } from "../data/mockData";
import type { AuditAction } from "../types";
import { formatDateTime } from "../utils/format";
import "./auditPage.css";

const ACTION_LABELS: Record<AuditAction, string> = {
  login_success: "Успешный вход",
  login_failure: "Неудачная попытка входа",
  logout: "Выход",
  account_locked: "Блокировка учётной записи",
  user_created: "Создан пользователь",
  user_updated: "Изменён пользователь",
  role_changed: "Изменена роль",
  candidate_transferred: "Передача кандидата",
  candidate_status_changed: "Изменение статуса кандидата",
  candidate_exported: "Экспорт данных",
};

const ACTION_ICON: Record<AuditAction, IconName> = {
  login_success: "check-circle",
  login_failure: "alert-triangle",
  logout: "log-out",
  account_locked: "lock",
  user_created: "user-plus",
  user_updated: "edit",
  role_changed: "shield",
  candidate_transferred: "arrow-right-left",
  candidate_status_changed: "check-circle",
  candidate_exported: "download",
};

const ACTION_TONE: Record<AuditAction, "success" | "danger" | "neutral" | "violet" | "info"> = {
  login_success: "success",
  login_failure: "danger",
  logout: "neutral",
  account_locked: "danger",
  user_created: "info",
  user_updated: "info",
  role_changed: "violet",
  candidate_transferred: "info",
  candidate_status_changed: "success",
  candidate_exported: "neutral",
};

export function AuditPage() {
  const { currentUserId } = useAppState();
  const currentUser = userById(currentUserId)!;
  const [actionFilter, setActionFilter] = useState<"all" | AuditAction>("all");

  if (currentUser.role === "hr") {
    return (
      <div>
        <PageHeader title="Журнал аудита" description="Журнал безопасности и бизнес-событий." />
        <PermissionDeniedState />
      </div>
    );
  }

  const rows = AUDIT_LOG.filter((e) => actionFilter === "all" || e.action === actionFilter);

  return (
    <div>
      <PageHeader
        title="Журнал аудита"
        description="Вход/выход, блокировки, изменения ролей, передачи кандидатов и экспорт — неизменяемый журнал событий."
        actions={
          <label>
            <span className="sr-only">Фильтр по типу события</span>
            <SelectInput value={actionFilter} onChange={(e) => setActionFilter(e.target.value as typeof actionFilter)}>
              <option value="all">Все события</option>
              {Object.entries(ACTION_LABELS).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </SelectInput>
          </label>
        }
      />

      <ol className="audit-log">
        {rows.map((entry) => (
          <li key={entry.id} className="audit-row">
            <span className={`audit-icon audit-icon-${ACTION_TONE[entry.action]}`}>
              <Icon name={ACTION_ICON[entry.action]} size={14} />
            </span>
            <div className="audit-content">
              <div className="audit-head">
                <Badge tone={ACTION_TONE[entry.action]}>{ACTION_LABELS[entry.action]}</Badge>
                <time className="audit-time" dateTime={entry.createdAt}>{formatDateTime(entry.createdAt)}</time>
              </div>
              <p className="audit-details">{entry.details}</p>
              <p className="audit-meta">
                Инициатор: {entry.actorName}
                {entry.targetName && <> · Объект: {entry.targetName}</>}
                {" · "}IP: {entry.ipAddress}
              </p>
            </div>
          </li>
        ))}
        {rows.length === 0 && <p className="timeline-empty">Событий по выбранному фильтру нет.</p>}
      </ol>
    </div>
  );
}
