/** «Администрирование» с вкладками (design-system Tabs).
 *
 * Вкладка «Диагностика запуска и обновлений» — бывший отдельный пункт меню
 * «Готовность пилота»: права прежние (раздел видят только администраторы,
 * а сервер дополнительно требует `update_channel_manage` и отвечает 403 без
 * него). Старая прямая ссылка `#/readiness` редиректит сюда — см.
 * `useWorkspaceSection`.
 */

import type { AdminTab } from "../../app-shell/useWorkspaceSection";
import { Tabs } from "../../design-system/components/Tabs";
import type { UserRole } from "../../types";
import { AdminQueuePage } from "../notifications/AdminQueuePage";
import { PilotReadinessPage } from "../readiness/PilotReadinessPage";
import "./admin.css";

interface AdminPageProps {
  role: UserRole;
  tab: AdminTab;
  onTabChange: (tab: AdminTab) => void;
}

const DIAGNOSTICS_INTRO =
  "Здесь видно, всё ли в порядке с установкой: база данных, резервные копии, " +
  "обновления, лицензия. Если что-то красное — сделайте отчёт ярлыком " +
  "«отчёт для разработчика» и отправьте его разработчику.";

const TAB_ITEMS: { id: AdminTab; label: string }[] = [
  { id: "queue", label: "Уведомления и пилот" },
  { id: "diagnostics", label: "Диагностика запуска и обновлений" },
];

export function AdminPage({ role, tab, onTabChange }: AdminPageProps) {
  // Диагностика — только для администраторов: у руководителя вкладки нет,
  // прямая ссылка на неё приводится к первой вкладке.
  const showDiagnostics = role === "admin";
  const active: AdminTab = tab === "diagnostics" && !showDiagnostics ? "queue" : tab;

  return (
    <div className="admin-page">
      {showDiagnostics && (
        <Tabs
          items={TAB_ITEMS}
          activeId={active}
          onChange={(id) => onTabChange(id as AdminTab)}
          ariaLabel="Разделы администрирования"
        />
      )}

      {active === "queue" && <AdminQueuePage />}

      {active === "diagnostics" && showDiagnostics && (
        <>
          <p className="admin-diagnostics-intro" role="note">
            {DIAGNOSTICS_INTRO}
          </p>
          <PilotReadinessPage />
        </>
      )}
    </div>
  );
}
