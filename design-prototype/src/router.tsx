import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/**
 * Минимальный hash-роутер для изолированного прототипа (без react-router,
 * чтобы не добавлять новые зависимости без крайней необходимости).
 * Поддерживает вложенный путь вида #/candidates/c-3.
 */
export type RouteName =
  | "login"
  | "home"
  | "queue"
  | "candidates"
  | "kanban"
  | "calendar"
  | "analytics"
  | "templates"
  | "users"
  | "audit"
  | "settings";

interface RouterState {
  route: RouteName;
  params: Record<string, string>;
  navigate: (route: RouteName, params?: Record<string, string>) => void;
}

const RouterContext = createContext<RouterState | null>(null);

function parseHash(): { route: RouteName; params: Record<string, string> } {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [route, ...rest] = hash.split("/").filter(Boolean);
  const params: Record<string, string> = {};
  if (rest.length > 0) params.id = rest[0];
  const known: RouteName[] = [
    "login", "home", "queue", "candidates", "kanban", "calendar", "analytics", "templates", "users", "audit", "settings",
  ];
  return { route: known.includes(route as RouteName) ? (route as RouteName) : "login", params };
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState(parseHash);

  useEffect(() => {
    function onHashChange() {
      setState(parseHash());
    }
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const navigate = useCallback((route: RouteName, params?: Record<string, string>) => {
    const suffix = params?.id ? `/${params.id}` : "";
    window.location.hash = `/${route}${suffix}`;
  }, []);

  const value = useMemo(() => ({ ...state, navigate }), [state, navigate]);

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterState {
  const ctx = useContext(RouterContext);
  if (!ctx) throw new Error("useRouter must be used within RouterProvider");
  return ctx;
}
