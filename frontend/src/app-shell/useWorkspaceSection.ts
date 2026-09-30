import { useCallback, useEffect, useState } from "react";

export type WorkspaceSection =
  | "queue"
  | "candidates"
  | "calendar"
  | "kanban"
  | "schedule"
  | "deleted"
  | "analytics"
  | "notifications"
  | "reminders"
  | "settings"
  | "documents"
  | "templates"
  | "rules"
  | "preferences"
  | "integrations"
  | "updates"
  | "license"
  | "admin"
  | "users";

/** Вкладки внутри «Администрирование»: очередь/пилот и диагностика запуска. */
export type AdminTab = "queue" | "diagnostics";

const SECTION_HASHES: Record<WorkspaceSection, string> = {
  queue: "#/queue",
  candidates: "#/candidates",
  calendar: "#/calendar",
  kanban: "#/kanban",
  schedule: "#/schedule",
  deleted: "#/deleted",
  analytics: "#/analytics",
  notifications: "#/notifications",
  reminders: "#/reminders",
  settings: "#/settings",
  preferences: "#/preferences",
  documents: "#/documents",
  templates: "#/templates",
  rules: "#/rules",
  integrations: "#/integrations",
  updates: "#/updates",
  license: "#/license",
  admin: "#/admin",
  users: "#/users",
};

export const ADMIN_TAB_HASHES: Record<AdminTab, string> = {
  queue: "#/admin",
  diagnostics: "#/admin/diagnostics",
};

/** Старая прямая ссылка «Готовность пилота» — редирект на вкладку
 * «Диагностика запуска и обновлений» внутри администрирования. */
export const READINESS_LEGACY_HASH = "#/readiness";

function normalizeHash(hash: string): string {
  return hash === READINESS_LEGACY_HASH ? ADMIN_TAB_HASHES.diagnostics : hash;
}

function sectionFromHash(hash: string, fallback: WorkspaceSection): WorkspaceSection {
  const normalized = normalizeHash(hash);
  if (normalized === ADMIN_TAB_HASHES.diagnostics) return "admin";
  const match = Object.entries(SECTION_HASHES).find(([, value]) => value === normalized);
  return (match?.[0] as WorkspaceSection | undefined) ?? fallback;
}

/** Какая вкладка администрирования запрошена ссылкой. */
export function adminTabFromHash(hash: string): AdminTab {
  return normalizeHash(hash) === ADMIN_TAB_HASHES.diagnostics ? "diagnostics" : "queue";
}

/**
 * Tiny hash-based section routing (no router dependency): deep-links work,
 * filters/pages inside a section are preserved while drawers open over it.
 * «Готовность пилота» больше не отдельный раздел: старый хэш #/readiness
 * редиректит на вкладку диагностики в администрировании.
 */
export function useWorkspaceSection(fallback: WorkspaceSection): [
  WorkspaceSection,
  (section: WorkspaceSection) => void,
  AdminTab,
  (tab: AdminTab) => void,
] {
  const [section, setSection] = useState<WorkspaceSection>(() =>
    sectionFromHash(window.location.hash, fallback)
  );
  const [adminTab, setAdminTab] = useState<AdminTab>(() =>
    adminTabFromHash(window.location.hash)
  );

  useEffect(() => {
    const handleHashChange = () => {
      setSection(sectionFromHash(window.location.hash, fallback));
      setAdminTab(adminTabFromHash(window.location.hash));
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [fallback]);

  // Редирект со старой ссылки: без новой записи в истории и с уведомлением
  // всех слушателей hashchange.
  useEffect(() => {
    if (window.location.hash === READINESS_LEGACY_HASH) {
      history.replaceState(null, "", ADMIN_TAB_HASHES.diagnostics);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }
  }, []);

  const navigate = useCallback((next: WorkspaceSection) => {
    if (window.location.hash !== SECTION_HASHES[next]) {
      window.location.hash = SECTION_HASHES[next];
    }
    setSection(next);
    if (next === "admin") setAdminTab("queue");
  }, []);

  const navigateAdminTab = useCallback((tab: AdminTab) => {
    if (window.location.hash !== ADMIN_TAB_HASHES[tab]) {
      window.location.hash = ADMIN_TAB_HASHES[tab];
    }
    setSection("admin");
    setAdminTab(tab);
  }, []);

  return [section, navigate, adminTab, navigateAdminTab];
}
