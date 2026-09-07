/**
 * Глобальное состояние дизайн-прототипа.
 *
 * Всё состояние — в памяти вкладки (React context), НИКАКИХ сетевых
 * запросов и НИКАКОЙ персистентности за пределами sessionStorage для
 * пары некритичных UI-предпочтений (тема/плотность), явно разрешённых
 * промптом ("переключать состояния интерфейса"). Кандидаты, статусы,
 * события — все изменения мокапа живут только в памяти и сбрасываются
 * при перезагрузке страницы.
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import type { Candidate, CandidateStage, CalendarEvent, Interaction } from "../types";
import { CANDIDATES, CURRENT_USER, EVENTS, INTERACTIONS, USERS } from "../data/mockData";

export type ThemeMode = "light" | "dark";
export type DensityMode = "comfortable" | "compact";
export type SimStatus = "online" | "degraded" | "offline";

export interface Toast {
  id: string;
  tone: "success" | "info" | "danger";
  message: string;
}

interface AppStateShape {
  currentUserId: string;
  isAuthenticated: boolean;
  sessionExpired: boolean;
  theme: ThemeMode;
  density: DensityMode;
  simStatus: SimStatus;
  candidates: Candidate[];
  interactions: Interaction[];
  events: CalendarEvent[];
  toasts: Toast[];
}

interface AppStateApi extends AppStateShape {
  login: (username: string) => void;
  logout: () => void;
  expireSession: () => void;
  restoreSession: () => void;
  toggleTheme: () => void;
  setDensity: (d: DensityMode) => void;
  setSimStatus: (s: SimStatus) => void;
  updateCandidateStage: (id: string, stage: CandidateStage) => void;
  transferCandidate: (id: string, newOwnerId: string, reason: string) => void;
  addInteraction: (i: Omit<Interaction, "id" | "createdAt">) => void;
  addEvent: (e: Omit<CalendarEvent, "id">) => void;
  pushToast: (tone: Toast["tone"], message: string) => void;
  dismissToast: (id: string) => void;
}

const AppStateContext = createContext<AppStateApi | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const [currentUserId, setCurrentUserId] = useState(CURRENT_USER.id);
  const [theme, setTheme] = useState<ThemeMode>(() => {
    if (typeof window === "undefined") return "light";
    return (sessionStorage.getItem("hrm_proto_theme") as ThemeMode | null) ?? "light";
  });
  const [density, setDensityState] = useState<DensityMode>(() => {
    if (typeof window === "undefined") return "comfortable";
    return (sessionStorage.getItem("hrm_proto_density") as DensityMode | null) ?? "comfortable";
  });
  const [simStatus, setSimStatus] = useState<SimStatus>("online");
  const [candidates, setCandidates] = useState<Candidate[]>(CANDIDATES);
  const [interactions, setInteractions] = useState<Interaction[]>(INTERACTIONS);
  const [events, setEvents] = useState<CalendarEvent[]>(EVENTS);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastCounter = useRef(0);

  const pushToast = useCallback((tone: Toast["tone"], message: string) => {
    toastCounter.current += 1;
    const id = `toast-${toastCounter.current}`;
    setToasts((prev) => [...prev, { id, tone, message }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4200);
  }, []);

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const login = useCallback((username: string) => {
    const found = USERS.find((u) => u.username === username);
    setCurrentUserId(found?.id ?? CURRENT_USER.id);
    setIsAuthenticated(true);
    setSessionExpired(false);
  }, []);

  const logout = useCallback(() => {
    setIsAuthenticated(false);
    setSessionExpired(false);
  }, []);

  const expireSession = useCallback(() => {
    setSessionExpired(true);
    setIsAuthenticated(false);
  }, []);

  const restoreSession = useCallback(() => {
    setSessionExpired(false);
    setIsAuthenticated(true);
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((prev) => {
      const next = prev === "light" ? "dark" : "light";
      sessionStorage.setItem("hrm_proto_theme", next);
      return next;
    });
  }, []);

  const setDensity = useCallback((d: DensityMode) => {
    setDensityState(d);
    sessionStorage.setItem("hrm_proto_density", d);
  }, []);

  const updateCandidateStage = useCallback(
    (id: string, stage: CandidateStage) => {
      setCandidates((prev) => prev.map((c) => (c.id === id ? { ...c, stage, lastActivityAt: new Date().toISOString() } : c)));
    },
    [],
  );

  const transferCandidate = useCallback((id: string, newOwnerId: string, _reason: string) => {
    setCandidates((prev) => prev.map((c) => (c.id === id ? { ...c, ownerId: newOwnerId, lastActivityAt: new Date().toISOString() } : c)));
  }, []);

  const addInteraction = useCallback((i: Omit<Interaction, "id" | "createdAt">) => {
    setInteractions((prev) => [
      { ...i, id: `i-mock-${prev.length + 1}`, createdAt: new Date().toISOString() },
      ...prev,
    ]);
  }, []);

  const addEvent = useCallback((e: Omit<CalendarEvent, "id">) => {
    setEvents((prev) => [...prev, { ...e, id: `e-mock-${prev.length + 1}` }]);
  }, []);

  const value = useMemo<AppStateApi>(
    () => ({
      currentUserId,
      isAuthenticated,
      sessionExpired,
      theme,
      density,
      simStatus,
      candidates,
      interactions,
      events,
      toasts,
      login,
      logout,
      expireSession,
      restoreSession,
      toggleTheme,
      setDensity,
      setSimStatus,
      updateCandidateStage,
      transferCandidate,
      addInteraction,
      addEvent,
      pushToast,
      dismissToast,
    }),
    [
      currentUserId,
      isAuthenticated,
      sessionExpired,
      theme,
      density,
      simStatus,
      candidates,
      interactions,
      events,
      toasts,
      login,
      logout,
      expireSession,
      restoreSession,
      toggleTheme,
      setDensity,
      updateCandidateStage,
      transferCandidate,
      addInteraction,
      addEvent,
      pushToast,
      dismissToast,
    ],
  );

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppStateApi {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
