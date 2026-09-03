import { useCallback, useEffect, useState } from "react";

export type WorkspaceSection = "queue" | "candidates" | "kanban" | "deleted";

const SECTION_HASHES: Record<WorkspaceSection, string> = {
  queue: "#/queue",
  candidates: "#/candidates",
  kanban: "#/kanban",
  deleted: "#/deleted",
};

function sectionFromHash(hash: string, fallback: WorkspaceSection): WorkspaceSection {
  const match = Object.entries(SECTION_HASHES).find(([, value]) => value === hash);
  return (match?.[0] as WorkspaceSection | undefined) ?? fallback;
}

/**
 * Tiny hash-based section routing (no router dependency): deep-links work,
 * filters/pages inside a section are preserved while drawers open over it.
 */
export function useWorkspaceSection(fallback: WorkspaceSection): [
  WorkspaceSection,
  (section: WorkspaceSection) => void,
] {
  const [section, setSection] = useState<WorkspaceSection>(() =>
    sectionFromHash(window.location.hash, fallback)
  );

  useEffect(() => {
    const handleHashChange = () => {
      setSection(sectionFromHash(window.location.hash, fallback));
    };
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [fallback]);

  const navigate = useCallback((next: WorkspaceSection) => {
    if (window.location.hash !== SECTION_HASHES[next]) {
      window.location.hash = SECTION_HASHES[next];
    }
    setSection(next);
  }, []);

  return [section, navigate];
}
