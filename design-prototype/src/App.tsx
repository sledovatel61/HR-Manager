import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  DEMO_ACCOUNTS,
  ROLE_LABEL,
  STATUS_META,
  STATUS_ORDER,
  SOURCES,
  auditEvents as seedAudit,
  candidates as seedCandidates,
  events as seedEvents,
  fullName,
  interactions as seedInteractions,
  kpi,
  savedViews,
  shortName,
  userById,
  users as seedUsers,
  vacancyById,
  type AuditEvent,
  type CalendarEvent,
  type Candidate,
  type CandidateStatus,
  type Interaction,
  type Role,
  type User,
} from "./data/mock";
import {
  Avatar,
  Button,
  EmptyState,
  Field,
  Input,
  Modal,
  PasswordInput,
  Segmented,
  Select,
  SkeletonPage,
  StatusChip,
  Textarea,
} from "./components/ui";

type Route =
  | "home"
  | "queue"
  | "candidates"
  | "candidate"
  | "calendar"
  | "analytics"
  | "templates"
  | "users"
  | "audit"
  | "settings"
  | "forbidden"
  | "empty-demo"
  | "loading-demo"
  | "error-demo";

type ViewMode = "table" | "kanban";
type Density = "comfortable" | "compact";
type UiState = "app" | "login" | "session-expired" | "booting";

interface ToastItem {
  id: string;
  message: string;
  tone?: "default" | "success" | "error";
}

interface NavItem {
  id: Route;
  label: string;
  icon: string;
  roles?: Role[];
  badge?: number;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
}

let toastSeq = 0;

export default function App() {
  const [uiState, setUiState] = useState<UiState>("booting");
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [route, setRoute] = useState<Route>("home");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [density, setDensity] = useState<Density>("comfortable");
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [railOpenMobile, setRailOpenMobile] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [statusFilter, setStatusFilter] = useState<CandidateStatus[]>([]);
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [ownerFilter, setOwnerFilter] = useState<string>("");
  const [savedViewId, setSavedViewId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>(seedCandidates);
  const [interactions, setInteractions] = useState<Interaction[]>(seedInteractions);
  const [events, setEvents] = useState<CalendarEvent[]>(seedEvents);
  const [users, setUsers] = useState<User[]>(seedUsers);
  const [audit, setAudit] = useState<AuditEvent[]>(seedAudit);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [networkDegraded, setNetworkDegraded] = useState(false);
  const [transferOpen, setTransferOpen] = useState(false);
  const [interactionOpen, setInteractionOpen] = useState(false);
  const [eventOpen, setEventOpen] = useState(false);
  const [createUserOpen, setCreateUserOpen] = useState(false);
  const [statusMenuFor, setStatusMenuFor] = useState<string | null>(null);
  const [candidateTab, setCandidateTab] = useState<"timeline" | "data" | "events">("timeline");
  const [confirm, setConfirm] = useState<{ title: string; body: string; onConfirm: () => void; danger?: boolean } | null>(null);
  const goChord = useRef<string | null>(null);

  const toast = useCallback((message: string, tone: ToastItem["tone"] = "default") => {
    const id = `t-${++toastSeq}`;
    setToasts((t) => [...t, { id, message, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);

  useEffect(() => {
    const t = window.setTimeout(() => setUiState("login"), 600);
    return () => window.clearTimeout(t);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.density = density;
  }, [density]);

  const loginAs = (userId: string) => {
    const u = users.find((x) => x.id === userId) ?? seedUsers.find((x) => x.id === userId);
    if (!u) return;
    setCurrentUser(u);
    setUiState("app");
    setRoute(u.role === "hr" ? "queue" : "home");
    toast(`Добро пожаловать, ${u.name.split(" ")[0]}`, "success");
  };

  const logout = () => {
    setCurrentUser(null);
    setUiState("login");
    toast("Вы вышли из системы");
  };

  const navigate = useCallback(
    (r: Route, opts?: { candidateId?: string }) => {
      if (!currentUser) return;
      const adminOnly: Route[] = ["users", "audit"];
      if (adminOnly.includes(r) && currentUser.role !== "admin") {
        setRoute("forbidden");
        setRailOpenMobile(false);
        return;
      }
      if (r === "analytics" && currentUser.role === "hr") {
        // personal analytics allowed
      }
      setRoute(r);
      if (opts?.candidateId) setSelectedId(opts.candidateId);
      if (r !== "candidate") setCandidateTab("timeline");
      setRailOpenMobile(false);
    },
    [currentUser],
  );

  const openCandidate = useCallback(
    (id: string) => {
      setSelectedId(id);
      setRoute("candidate");
      setRailOpenMobile(false);
    },
    [],
  );

  const filteredCandidates = useMemo(() => {
    let list = candidates.filter((c) => !c.deleted);
    if (route === "queue" && currentUser) {
      list = list.filter((c) => c.ownerId === currentUser.id);
    }
    if (statusFilter.length) list = list.filter((c) => statusFilter.includes(c.status));
    if (sourceFilter) list = list.filter((c) => c.source === sourceFilter);
    if (ownerFilter) list = list.filter((c) => c.ownerId === ownerFilter);
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      list = list.filter(
        (c) =>
          fullName(c).toLowerCase().includes(q) ||
          c.phone.includes(q) ||
          c.email.toLowerCase().includes(q) ||
          (vacancyById(c.vacancyId)?.title ?? "").toLowerCase().includes(q),
      );
    }
    return list;
  }, [candidates, route, currentUser, statusFilter, sourceFilter, ownerFilter, query]);

  const selected = candidates.find((c) => c.id === selectedId) ?? null;

  const changeStatus = (id: string, status: CandidateStatus) => {
    setCandidates((list) =>
      list.map((c) => (c.id === id ? { ...c, status, updatedAt: new Date().toISOString() } : c)),
    );
    const actor = currentUser?.id ?? "u-anna";
    setInteractions((items) => [
      {
        id: `i-status-${Date.now()}`,
        candidateId: id,
        type: "status",
        title: "Статус изменён",
        body: `Новый статус: ${STATUS_META[status].label}`,
        actorId: actor,
        at: new Date().toISOString(),
      },
      ...items,
    ]);
    setAudit((a) => [
      {
        id: `a-${Date.now()}`,
        at: new Date().toISOString(),
        actorId: actor,
        action: "candidate.status",
        entity: "candidate",
        entityId: id,
        detail: `Статус → ${status}`,
      },
      ...a,
    ]);
    setStatusMenuFor(null);
    toast(`Статус: ${STATUS_META[status].label}`, "success");
  };

  const applySavedView = (id: string) => {
    setSavedViewId(id);
    const v = savedViews.find((x) => x.id === id);
    if (!v) return;
    setStatusFilter([...v.statuses]);
    if (v.ownerOnly && currentUser) setOwnerFilter(currentUser.id);
    else setOwnerFilter("");
    toast(`Представление «${v.name}»`);
  };

  // Global keyboard
  useEffect(() => {
    if (uiState !== "app") return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);

      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (e.key === "?" && !typing) {
        e.preventDefault();
        setHelpOpen(true);
        return;
      }
      if (e.key === "/" && !typing) {
        e.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (e.key === "Escape") {
        if (paletteOpen) setPaletteOpen(false);
        else if (helpOpen) setHelpOpen(false);
        else if (confirm) setConfirm(null);
        else if (transferOpen) setTransferOpen(false);
        else if (interactionOpen) setInteractionOpen(false);
        else if (eventOpen) setEventOpen(false);
        else if (createUserOpen) setCreateUserOpen(false);
        else if (statusMenuFor) setStatusMenuFor(null);
        return;
      }
      if (typing) return;

      if (e.key.toLowerCase() === "g") {
        goChord.current = "g";
        window.setTimeout(() => {
          goChord.current = null;
        }, 800);
        return;
      }
      if (goChord.current === "g") {
        const map: Record<string, Route> = {
          h: "home",
          q: "queue",
          c: "candidates",
          k: "calendar",
          a: "analytics",
          u: "users",
          l: "audit",
          s: "settings",
        };
        const r = map[e.key.toLowerCase()];
        if (r) {
          e.preventDefault();
          navigate(r);
          goChord.current = null;
        }
      }
      if (e.key.toLowerCase() === "v" && (route === "candidates" || route === "queue")) {
        setViewMode((m) => (m === "table" ? "kanban" : "table"));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [
    uiState,
    navigate,
    paletteOpen,
    helpOpen,
    confirm,
    transferOpen,
    interactionOpen,
    eventOpen,
    createUserOpen,
    statusMenuFor,
    route,
  ]);

  if (uiState === "booting") {
    return (
      <div className="login-page" aria-busy="true">
        <div className="card login-card">
          <SkeletonPage />
        </div>
      </div>
    );
  }

  if (uiState === "session-expired") {
    return (
      <div className="login-page" role="alertdialog" aria-labelledby="sess-title" aria-modal="true">
        <div className="card login-card state-block" style={{ margin: 0 }}>
          <div className="state-icon is-warn" aria-hidden="true">
            ⏱
          </div>
          <h1 id="sess-title">Сессия истекла</h1>
          <p>Из соображений безопасности вход нужно выполнить снова. Несохранённые черновики в прототипе не хранятся на сервере.</p>
          <Button variant="primary" onClick={() => setUiState("login")}>
            Войти снова
          </Button>
        </div>
      </div>
    );
  }

  if (uiState === "login" || !currentUser) {
    return <LoginScreen onLogin={loginAs} toast={toast} />;
  }

  const queueCount = candidates.filter((c) => c.ownerId === currentUser.id && !["rejected", "left", "hired"].includes(c.status)).length;

  const nav: NavItem[] = [
    { id: "home", label: "Главная", icon: "⌂" },
    { id: "queue", label: "Моя очередь", icon: "☰", roles: ["hr", "manager"], badge: queueCount },
    { id: "candidates", label: "Кандидаты", icon: "◎" },
    { id: "calendar", label: "Календарь", icon: "▦" },
    { id: "analytics", label: "Аналитика", icon: " mixed" },
    { id: "templates", label: "Шаблоны", icon: "📄" },
    { id: "users", label: "Пользователи", icon: "☺", roles: ["admin"] },
    { id: "audit", label: "Журнал аудита", icon: "⌁", roles: ["admin"] },
    { id: "settings", label: "Настройки", icon: "⚙" },
  ];

  const visibleNav = nav.filter((n) => !n.roles || n.roles.includes(currentUser.role) || currentUser.role === "admin");

  return (
    <div className={`app-shell${railCollapsed ? " is-collapsed" : ""}`}>
      <a className="skip-link" href="#main">
        Перейти к содержимому
      </a>

      <aside className={`rail${railOpenMobile ? " is-open" : ""}`} aria-label="Основная навигация">
        <div className="rail-brand">
          <div className="rail-logo" aria-hidden="true">
            HR
          </div>
          <div className="rail-brand-text">
            <strong>HR Manager</strong>
            <span>Signal Desk · demo</span>
          </div>
        </div>
        <nav className="rail-nav">
          <div className="rail-section">Работа</div>
          {visibleNav
            .filter((n) => !["users", "audit", "settings"].includes(n.id))
            .map((n) => (
              <button
                key={n.id}
                type="button"
                className={`nav-item${route === n.id || (n.id === "candidates" && route === "candidate") ? " is-active" : ""}`}
                onClick={() => navigate(n.id)}
                aria-current={route === n.id ? "page" : undefined}
                title={n.label}
              >
                <span className="nav-icon" aria-hidden="true">
                  {n.icon}
                </span>
                <span className="nav-label">{n.label}</span>
                {n.badge ? <span className="nav-badge">{n.badge}</span> : null}
              </button>
            ))}
          <div className="rail-section">Система</div>
          {visibleNav
            .filter((n) => ["users", "audit", "settings"].includes(n.id))
            .map((n) => (
              <button
                key={n.id}
                type="button"
                className={`nav-item${route === n.id ? " is-active" : ""}`}
                onClick={() => navigate(n.id)}
                aria-current={route === n.id ? "page" : undefined}
                title={n.label}
              >
                <span className="nav-icon" aria-hidden="true">
                  {n.icon}
                </span>
                <span className="nav-label">{n.label}</span>
              </button>
            ))}
        </nav>
        <Button variant="ghost" size="sm" onClick={() => setRailCollapsed((v) => !v)} aria-pressed={railCollapsed}>
          {railCollapsed ? "»" : "« Свернуть"}
        </Button>
      </aside>

      <header className="topbar">
        <Button
          variant="ghost"
          icon
          className="only-narrow"
          aria-label="Меню"
          onClick={() => setRailOpenMobile(true)}
          style={{ display: undefined }}
        >
          ☰
        </Button>
        <button type="button" className="search-trigger" onClick={() => setPaletteOpen(true)}>
          <span aria-hidden="true">⌕</span>
          <span>Поиск кандидатов и команд…</span>
          <kbd>Ctrl K</kbd>
        </button>
        <div className="topbar-actions">
          <Segmented
            label="Плотность интерфейса"
            value={density}
            onChange={(v) => setDensity(v as Density)}
            options={[
              { value: "comfortable", label: "Комфорт" },
              { value: "compact", label: "Компакт" },
            ]}
          />
          <Button variant="ghost" size="sm" onClick={() => setHelpOpen(true)} aria-label="Справка по клавишам">
            ?
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setUiState("session-expired");
              setCurrentUser(null);
            }}
            title="Симулировать истечение сессии"
          >
            Сессия
          </Button>
          <button type="button" className="user-pill" onClick={() => navigate("settings")} aria-label="Профиль">
            <Avatar name={currentUser.name} />
            <span className="user-pill-meta">
              <strong>{currentUser.name}</strong>
              <span>{ROLE_LABEL[currentUser.role]}</span>
            </span>
          </button>
        </div>
      </header>

      <main className="main" id="main">
        {networkDegraded && (
          <div className="banner is-error" role="status">
            <strong>Связь с backend недоступна</strong>
            <span className="muted"> (мок). Показаны локальные данные.</span>
            <Button size="sm" variant="secondary" onClick={() => setNetworkDegraded(false)}>
              Повторить
            </Button>
          </div>
        )}

        {route === "home" && (
          <HomePage
            user={currentUser}
            queueCount={queueCount}
            events={events}
            onOpenQueue={() => navigate("queue")}
            onOpenAnalytics={() => navigate("analytics")}
            onOpenCandidate={openCandidate}
            onOpenCalendar={() => navigate("calendar")}
          />
        )}
        {route === "queue" && (
          <CandidatesPage
            title="Моя очередь"
            subtitle="Кандидаты, за которые вы отвечаете"
            list={filteredCandidates}
            viewMode={viewMode}
            setViewMode={setViewMode}
            onOpen={openCandidate}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            filtersOpen={filtersOpen}
            setFiltersOpen={setFiltersOpen}
            sourceFilter={sourceFilter}
            setSourceFilter={setSourceFilter}
            ownerFilter={ownerFilter}
            setOwnerFilter={setOwnerFilter}
            showOwnerFilter={false}
            savedViewId={savedViewId}
            onSavedView={applySavedView}
            query={query}
            setQuery={setQuery}
            onStatus={changeStatus}
            statusMenuFor={statusMenuFor}
            setStatusMenuFor={setStatusMenuFor}
            onTransfer={(id) => {
              setSelectedId(id);
              setTransferOpen(true);
            }}
          />
        )}
        {route === "candidates" && (
          <CandidatesPage
            title="Кандидаты"
            subtitle="Единая база · фильтры и представления"
            list={filteredCandidates}
            viewMode={viewMode}
            setViewMode={setViewMode}
            onOpen={openCandidate}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            filtersOpen={filtersOpen}
            setFiltersOpen={setFiltersOpen}
            sourceFilter={sourceFilter}
            setSourceFilter={setSourceFilter}
            ownerFilter={ownerFilter}
            setOwnerFilter={setOwnerFilter}
            showOwnerFilter={currentUser.role !== "hr"}
            savedViewId={savedViewId}
            onSavedView={applySavedView}
            query={query}
            setQuery={setQuery}
            onStatus={changeStatus}
            statusMenuFor={statusMenuFor}
            setStatusMenuFor={setStatusMenuFor}
            onTransfer={(id) => {
              setSelectedId(id);
              setTransferOpen(true);
            }}
            onEmptyDemo={() => navigate("empty-demo")}
          />
        )}
        {route === "candidate" && selected && (
          <CandidatePage
            candidate={selected}
            tab={candidateTab}
            setTab={setCandidateTab}
            interactions={interactions.filter((i) => i.candidateId === selected.id).sort((a, b) => (a.at < b.at ? 1 : -1))}
            events={events.filter((e) => e.candidateId === selected.id)}
            onBack={() => navigate("candidates")}
            onStatus={() => setStatusMenuFor(selected.id)}
            onTransfer={() => setTransferOpen(true)}
            onAddInteraction={() => setInteractionOpen(true)}
            onAddEvent={() => setEventOpen(true)}
            statusMenuFor={statusMenuFor}
            setStatusMenuFor={setStatusMenuFor}
            onChangeStatus={changeStatus}
          />
        )}
        {route === "candidate" && !selected && (
          <EmptyState title="Кандидат не найден" description="Запись отсутствует в мок-данных." action={<Button onClick={() => navigate("candidates")}>К списку</Button>} />
        )}
        {route === "calendar" && (
          <CalendarPage
            events={events}
            onOpenCandidate={(id) => openCandidate(id)}
            onCreate={() => setEventOpen(true)}
            onToggleDone={(id) => {
              setEvents((list) => list.map((e) => (e.id === id ? { ...e, done: !e.done } : e)));
              toast("Событие обновлено");
            }}
          />
        )}
        {route === "analytics" && <AnalyticsPage role={currentUser.role} />}
        {route === "templates" && (
          <EmptyState
            title="Шаблоны и контент"
            description="Раздел появится на этапе 6: формы, анкеты, скрипты и версия документов. В прототипе — заглушка навигации."
            icon="📄"
            action={<Button variant="primary" disabled>Загрузить версию (скоро)</Button>}
          />
        )}
        {route === "users" && (
          <UsersPage
            users={users}
            onCreate={() => setCreateUserOpen(true)}
            onToggleLock={(id) => {
              setUsers((list) => list.map((u) => (u.id === id ? { ...u, locked: !u.locked, active: u.locked } : u)));
              toast("Статус блокировки обновлён");
            }}
            onChangeRole={(id, role) => {
              setConfirm({
                title: "Изменить роль?",
                body: `Пользователю будет назначена роль «${ROLE_LABEL[role]}». Действие попадёт в журнал аудита.`,
                onConfirm: () => {
                  setUsers((list) => list.map((u) => (u.id === id ? { ...u, role } : u)));
                  setAudit((a) => [
                    {
                      id: `a-role-${Date.now()}`,
                      at: new Date().toISOString(),
                      actorId: currentUser.id,
                      action: "user.role_change",
                      entity: "user",
                      entityId: id,
                      detail: `Роль → ${role}`,
                    },
                    ...a,
                  ]);
                  setConfirm(null);
                  toast("Роль изменена", "success");
                },
              });
            }}
          />
        )}
        {route === "audit" && <AuditPage items={audit} />}
        {route === "settings" && (
          <SettingsPage
            user={currentUser}
            density={density}
            setDensity={setDensity}
            onLogout={logout}
            onExpire={() => {
              setUiState("session-expired");
              setCurrentUser(null);
            }}
            onDegrade={() => {
              setNetworkDegraded(true);
              navigate("error-demo");
            }}
            onLoading={() => navigate("loading-demo")}
            onEmpty={() => navigate("empty-demo")}
            onForbidden={() => navigate("forbidden")}
          />
        )}
        {route === "forbidden" && (
          <div className="state-block card card-pad">
            <div className="state-icon is-danger" aria-hidden="true">
              ⛔
            </div>
            <h2>Недостаточно прав</h2>
            <p>
              Раздел доступен роли «Администратор». Ваша роль: <strong>{ROLE_LABEL[currentUser.role]}</strong>. Если доступ нужен —
              обратитесь к администратору системы.
            </p>
            <Button variant="primary" onClick={() => navigate("home")}>
              На главную
            </Button>
          </div>
        )}
        {route === "empty-demo" && (
          <EmptyState
            title="Ничего не найдено"
            description="По текущим фильтрам кандидатов нет. Сбросьте фильтры или создайте кандидата (в production — форма создания)."
            action={
              <Button
                variant="primary"
                onClick={() => {
                  setStatusFilter([]);
                  setSourceFilter("");
                  setOwnerFilter("");
                  setQuery("");
                  navigate("candidates");
                }}
              >
                Сбросить фильтры
              </Button>
            }
          />
        )}
        {route === "loading-demo" && <SkeletonPage />}
        {route === "error-demo" && (
          <div className="state-block card card-pad">
            <div className="state-icon is-danger" aria-hidden="true">
              ⚠
            </div>
            <h2>Не удалось загрузить данные</h2>
            <p>Backend недоступен или вернул ошибку. В prototype используются только локальные моки — это демонстрация degraded state.</p>
            <div className="inline" style={{ justifyContent: "center" }}>
              <Button
                variant="primary"
                onClick={() => {
                  setNetworkDegraded(false);
                  navigate("home");
                }}
              >
                Повторить
              </Button>
              <Button variant="secondary" onClick={() => navigate("home")}>
                На главную
              </Button>
            </div>
          </div>
        )}
      </main>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        candidates={candidates}
        onNavigate={(r) => {
          setPaletteOpen(false);
          navigate(r);
        }}
        onOpenCandidate={(id) => {
          setPaletteOpen(false);
          openCandidate(id);
        }}
        onAction={(action) => {
          setPaletteOpen(false);
          if (action === "transfer" && selectedId) setTransferOpen(true);
          if (action === "interaction" && selectedId) setInteractionOpen(true);
          if (action === "event") setEventOpen(true);
          if (action === "user" && currentUser.role === "admin") setCreateUserOpen(true);
          if (action === "density") setDensity((d) => (d === "compact" ? "comfortable" : "compact"));
        }}
        role={currentUser.role}
      />

      <Modal open={helpOpen} title="Клавиатурные сокращения" onClose={() => setHelpOpen(false)}>
        <div className="help-grid">
          <kbd>Ctrl/⌘ K</kbd>
          <span>Command palette</span>
          <kbd>/</kbd>
          <span>Поиск</span>
          <kbd>G H/Q/C/K/A</kbd>
          <span>Навигация по разделам</span>
          <kbd>V</kbd>
          <span>Table ↔ Kanban</span>
          <kbd>?</kbd>
          <span>Эта справка</span>
          <kbd>Esc</kbd>
          <span>Закрыть оверлей</span>
        </div>
      </Modal>

      <TransferDialog
        open={transferOpen}
        candidate={selected}
        users={users.filter((u) => u.role === "hr" && u.active && !u.locked)}
        onClose={() => setTransferOpen(false)}
        onSubmit={(toId, reason) => {
          if (!selected || !currentUser) return;
          const from = selected.ownerId;
          setCandidates((list) =>
            list.map((c) => (c.id === selected.id ? { ...c, ownerId: toId, updatedAt: new Date().toISOString() } : c)),
          );
          setInteractions((items) => [
            {
              id: `i-tr-${Date.now()}`,
              candidateId: selected.id,
              type: "transfer",
              title: "Передача ответственности",
              body: `${userById(from)?.name ?? from} → ${userById(toId)?.name ?? toId}. Причина: ${reason}`,
              actorId: currentUser.id,
              at: new Date().toISOString(),
            },
            ...items,
          ]);
          setAudit((a) => [
            {
              id: `a-tr-${Date.now()}`,
              at: new Date().toISOString(),
              actorId: currentUser.id,
              action: "candidate.transfer",
              entity: "candidate",
              entityId: selected.id,
              detail: `Передача → ${toId}. ${reason}`,
            },
            ...a,
          ]);
          setTransferOpen(false);
          toast("Кандидат передан", "success");
        }}
      />

      <InteractionDialog
        open={interactionOpen}
        onClose={() => setInteractionOpen(false)}
        onSubmit={(payload) => {
          if (!selected || !currentUser) return;
          setInteractions((items) => [
            {
              id: `i-new-${Date.now()}`,
              candidateId: selected.id,
              type: payload.type,
              title: payload.title,
              body: payload.body,
              actorId: currentUser.id,
              at: payload.at,
            },
            ...items,
          ]);
          setCandidates((list) =>
            list.map((c) => (c.id === selected.id ? { ...c, updatedAt: new Date().toISOString() } : c)),
          );
          setInteractionOpen(false);
          toast("Взаимодействие добавлено", "success");
        }}
      />

      <EventDialog
        open={eventOpen}
        candidateId={selectedId}
        onClose={() => setEventOpen(false)}
        onSubmit={(ev) => {
          if (!currentUser) return;
          setEvents((list) => [{ ...ev, id: `e-${Date.now()}`, ownerId: currentUser.id, done: false }, ...list]);
          setEventOpen(false);
          toast("Событие запланировано", "success");
        }}
      />

      <CreateUserDialog
        open={createUserOpen}
        onClose={() => setCreateUserOpen(false)}
        onSubmit={(u) => {
          setUsers((list) => [u, ...list]);
          setAudit((a) => [
            {
              id: `a-cu-${Date.now()}`,
              at: new Date().toISOString(),
              actorId: currentUser.id,
              action: "user.create",
              entity: "user",
              entityId: u.id,
              detail: `Создан ${u.username}`,
            },
            ...a,
          ]);
          setCreateUserOpen(false);
          toast("Пользователь создан", "success");
        }}
      />

      <Modal
        open={!!confirm}
        title={confirm?.title ?? ""}
        onClose={() => setConfirm(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(null)}>
              Отмена
            </Button>
            <Button variant={confirm?.danger ? "danger" : "primary"} onClick={() => confirm?.onConfirm()}>
              Подтвердить
            </Button>
          </>
        }
      >
        <p className="muted" style={{ margin: 0 }}>
          {confirm?.body}
        </p>
      </Modal>

      <div className="toast-region" aria-live="polite" aria-relevant="additions">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.tone === "success" ? " is-success" : ""}${t.tone === "error" ? " is-error" : ""}`} role="status">
            <span>{t.message}</span>
            <button type="button" aria-label="Закрыть уведомление" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              ✕
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ——— Screens ——— */

function LoginScreen({ onLogin, toast }: { onLogin: (userId: string) => void; toast: (m: string, t?: ToastItem["tone"]) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!username || !password) {
      setError("Введите логин и пароль");
      return;
    }
    setLoading(true);
    window.setTimeout(() => {
      const acc = DEMO_ACCOUNTS.find((a) => a.username === username && a.password === password);
      setLoading(false);
      if (!acc) {
        setError("Неверный логин или пароль (мок-проверка)");
        toast("Ошибка входа", "error");
        return;
      }
      onLogin(acc.userId);
    }, 400);
  };

  return (
    <div className="login-page">
      <div className="card login-card">
        <div className="inline" style={{ marginBottom: 16 }}>
          <div className="rail-logo" aria-hidden="true">
            HR
          </div>
          <div>
            <strong>HR Manager</strong>
            <div className="muted" style={{ fontSize: 12 }}>
              Design prototype · без backend
            </div>
          </div>
        </div>
        <h1>Вход</h1>
        <p className="lead">Рабочий инструмент командного подбора. Используйте демо-учётные записи ниже.</p>
        <form className="stack" onSubmit={submit} noValidate>
          <Field label="Логин" error={error && !username ? error : undefined}>
            <Input
              id="login-user"
              name="username"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              error={!!error}
              aria-invalid={!!error}
              aria-describedby={error ? "login-err" : undefined}
            />
          </Field>
          <Field label="Пароль">
            <PasswordInput
              id="login-pass"
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              error={!!error}
            />
          </Field>
          {error ? (
            <div className="field-error" id="login-err" role="alert">
              {error}
            </div>
          ) : null}
          <Button variant="primary" type="submit" disabled={loading} aria-busy={loading}>
            {loading ? "Входим…" : "Войти"}
          </Button>
        </form>
        <div className="demo-accounts">
          <h2>Демо-доступ</h2>
          {DEMO_ACCOUNTS.map((a) => (
            <Button
              key={a.username}
              variant="secondary"
              size="sm"
              onClick={() => {
                setUsername(a.username);
                setPassword(a.password);
              }}
            >
              <span>
                {a.username} · {ROLE_LABEL[a.role]}
              </span>
              <span className="muted">{a.password}</span>
            </Button>
          ))}
        </div>
      </div>
    </div>
  );
}

function HomePage({
  user,
  queueCount,
  events,
  onOpenQueue,
  onOpenAnalytics,
  onOpenCandidate,
  onOpenCalendar,
}: {
  user: User;
  queueCount: number;
  events: CalendarEvent[];
  onOpenQueue: () => void;
  onOpenAnalytics: () => void;
  onOpenCandidate: (id: string) => void;
  onOpenCalendar: () => void;
}) {
  const mine = events.filter((e) => e.ownerId === user.id || user.role !== "hr").slice(0, 5);
  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Рабочий стол</h1>
          <p>
            {user.role === "hr"
              ? "Фокус на очереди и ближайших касаниях"
              : user.role === "manager"
                ? "Обзор операций и контрольные точки воронки"
                : "Администрирование и контроль безопасности"}
          </p>
        </div>
        <div className="page-actions">
          {user.role !== "admin" && (
            <Button variant="primary" onClick={onOpenQueue}>
              Моя очередь · {queueCount}
            </Button>
          )}
          {(user.role === "manager" || user.role === "admin") && (
            <Button variant="secondary" onClick={onOpenAnalytics}>
              Аналитика
            </Button>
          )}
        </div>
      </div>
      <div className="grid-kpi">
        <div className="card kpi">
          <div className="label">В очереди</div>
          <div className="value">{queueCount}</div>
          <div className="delta">активные кандидаты</div>
        </div>
        <div className="card kpi">
          <div className="label">События сегодня</div>
          <div className="value">{events.filter((e) => e.start.startsWith("2026-09-02")).length}</div>
          <div className="delta">мок-дата 2 сен</div>
        </div>
        <div className="card kpi">
          <div className="label">Офферы</div>
          <div className="value">{kpi.offers}</div>
          <div className="delta">за {kpi.periodLabel}</div>
        </div>
        <div className="card kpi">
          <div className="label">Выходы</div>
          <div className="value">{kpi.hired}</div>
          <div className="delta">конверсия оффер→выход {(kpi.conversionOfferToHire * 100).toFixed(0)}%</div>
        </div>
      </div>
      <div className="split-2">
        <section className="card card-pad">
          <div className="inline" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <h2 style={{ margin: 0, font: "var(--text-subtitle)" }}>Ближайшие события</h2>
            <Button size="sm" variant="ghost" onClick={onOpenCalendar}>
              Календарь
            </Button>
          </div>
          <ul className="timeline">
            {mine.map((e) => (
              <li key={e.id} className="timeline-item">
                <div className="timeline-mark" aria-hidden="true">
                  {e.type === "interview" ? "С" : e.type === "call" ? "З" : "•"}
                </div>
                <div className="timeline-body">
                  <header>
                    <strong>{e.title}</strong>
                    <time dateTime={e.start}>{formatDate(e.start)}</time>
                  </header>
                  <p>
                    {e.candidateId ? (
                      <button type="button" className="btn btn-ghost btn-sm" onClick={() => onOpenCandidate(e.candidateId!)}>
                        Открыть кандидата
                      </button>
                    ) : (
                      "Командное событие"
                    )}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
        <section className="card card-pad">
          <h2 style={{ margin: "0 0 12px", font: "var(--text-subtitle)" }}>Быстрые действия</h2>
          <div className="stack">
            <Button variant="secondary" onClick={onOpenQueue}>
              Разобрать очередь
            </Button>
            <Button variant="secondary" onClick={onOpenCalendar}>
              Открыть календарь
            </Button>
            <p className="muted" style={{ fontSize: 13 }}>
              Нажмите <kbd>Ctrl</kbd>+<kbd>K</kbd> для command palette — поиск и команды без мыши.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}

function CandidatesPage(props: {
  title: string;
  subtitle: string;
  list: Candidate[];
  viewMode: ViewMode;
  setViewMode: (v: ViewMode) => void;
  onOpen: (id: string) => void;
  statusFilter: CandidateStatus[];
  setStatusFilter: (s: CandidateStatus[]) => void;
  filtersOpen: boolean;
  setFiltersOpen: (v: boolean) => void;
  sourceFilter: string;
  setSourceFilter: (s: string) => void;
  ownerFilter: string;
  setOwnerFilter: (s: string) => void;
  showOwnerFilter: boolean;
  savedViewId: string;
  onSavedView: (id: string) => void;
  query: string;
  setQuery: (q: string) => void;
  onStatus: (id: string, s: CandidateStatus) => void;
  statusMenuFor: string | null;
  setStatusMenuFor: (id: string | null) => void;
  onTransfer: (id: string) => void;
  onEmptyDemo?: () => void;
}) {
  const toggleStatus = (s: CandidateStatus) => {
    props.setStatusFilter(
      props.statusFilter.includes(s) ? props.statusFilter.filter((x) => x !== s) : [...props.statusFilter, s],
    );
  };

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>{props.title}</h1>
          <p>
            {props.subtitle} · {props.list.length} записей
          </p>
        </div>
        <div className="page-actions">
          <Segmented
            label="Режим списка"
            value={props.viewMode}
            onChange={(v) => props.setViewMode(v as ViewMode)}
            options={[
              { value: "table", label: "Таблица" },
              { value: "kanban", label: "Kanban" },
            ]}
          />
          <Button variant="secondary" aria-expanded={props.filtersOpen} onClick={() => props.setFiltersOpen(!props.filtersOpen)}>
            Фильтры
          </Button>
        </div>
      </div>

      <div className="toolbar">
        <Input
          aria-label="Быстрый фильтр списка"
          placeholder="ФИО, телефон, вакансия…"
          value={props.query}
          onChange={(e) => props.setQuery(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <Select aria-label="Сохранённые представления" value={props.savedViewId} onChange={(e) => props.onSavedView(e.target.value)}>
          <option value="">Представления…</option>
          {savedViews.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </Select>
        {props.onEmptyDemo && (
          <Button size="sm" variant="ghost" onClick={props.onEmptyDemo}>
            Демо empty
          </Button>
        )}
      </div>

      {props.filtersOpen && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <div className="stack">
            <div>
              <div className="muted" style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
                Статусы
              </div>
              <div className="filter-chips">
                {STATUS_ORDER.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="filter-chip"
                    aria-pressed={props.statusFilter.includes(s)}
                    onClick={() => toggleStatus(s)}
                  >
                    {STATUS_META[s].label}
                  </button>
                ))}
              </div>
            </div>
            <div className="inline">
              <Field label="Источник">
                <Select value={props.sourceFilter} onChange={(e) => props.setSourceFilter(e.target.value)} aria-label="Источник">
                  <option value="">Все источники</option>
                  {SOURCES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </Select>
              </Field>
              {props.showOwnerFilter && (
                <Field label="Ответственный HR">
                  <Select value={props.ownerFilter} onChange={(e) => props.setOwnerFilter(e.target.value)} aria-label="HR">
                    <option value="">Все</option>
                    {seedUsers
                      .filter((u) => u.role === "hr")
                      .map((u) => (
                        <option key={u.id} value={u.id}>
                          {u.name}
                        </option>
                      ))}
                  </Select>
                </Field>
              )}
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  props.setStatusFilter([]);
                  props.setSourceFilter("");
                  props.setOwnerFilter("");
                }}
              >
                Сбросить
              </Button>
            </div>
          </div>
        </div>
      )}

      {props.list.length === 0 ? (
        <EmptyState title="Пусто" description="Нет кандидатов по текущим условиям." />
      ) : props.viewMode === "table" ? (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Кандидат</th>
                <th scope="col">Вакансия</th>
                <th scope="col">Статус</th>
                <th scope="col">Ответственный</th>
                <th scope="col">Источник</th>
                <th scope="col">Обновлён</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {props.list.map((c) => {
                const owner = userById(c.ownerId);
                const vac = vacancyById(c.vacancyId);
                return (
                  <tr
                    key={c.id}
                    tabIndex={0}
                    onClick={() => props.onOpen(c.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") props.onOpen(c.id);
                    }}
                  >
                    <td>
                      <div className="cell-main">
                        <Avatar name={fullName(c)} />
                        <div className="meta">
                          <strong>{fullName(c)}</strong>
                          <span>{c.phone}</span>
                        </div>
                      </div>
                    </td>
                    <td>{vac?.title}</td>
                    <td>
                      <StatusChip status={c.status} />
                    </td>
                    <td>
                      <div className="inline">
                        {owner ? <Avatar name={owner.name} /> : null}
                        <span>{owner?.name ?? "—"}</span>
                      </div>
                    </td>
                    <td>{c.source}</td>
                    <td>{formatDay(c.updatedAt)}</td>
                    <td>
                      <div className="row-actions" onClick={(e) => e.stopPropagation()}>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Статус ${shortName(c)}`}
                          onClick={() => props.setStatusMenuFor(props.statusMenuFor === c.id ? null : c.id)}
                        >
                          Статус
                        </Button>
                        <Button size="sm" variant="ghost" aria-label={`Передать ${shortName(c)}`} onClick={() => props.onTransfer(c.id)}>
                          Передать
                        </Button>
                      </div>
                      {props.statusMenuFor === c.id && (
                        <StatusMenu onPick={(s) => props.onStatus(c.id, s)} onClose={() => props.setStatusMenuFor(null)} />
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="kanban" role="list">
          {STATUS_ORDER.filter((s) => !["left"].includes(s)).map((status) => {
            const col = props.list.filter((c) => c.status === status);
            const tone = STATUS_META[status].tone;
            return (
              <div className="kanban-col" key={status} role="list">
                <div className="kanban-col-h" data-tone={tone}>
                  <strong>{STATUS_META[status].label}</strong>
                  <span className="badge">{col.length}</span>
                </div>
                <div className="kanban-body">
                  {col.map((c) => (
                    <button key={c.id} type="button" className="k-card" onClick={() => props.onOpen(c.id)} role="listitem">
                      <h3>{shortName(c)}</h3>
                      <p>{vacancyById(c.vacancyId)?.title}</p>
                      <div className="k-card-foot">
                        <Avatar name={userById(c.ownerId)?.name ?? "?"} />
                        <span className="muted" style={{ fontSize: 11 }}>
                          {c.nextActionAt ? formatDay(c.nextActionAt) : "—"}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StatusMenu({ onPick, onClose }: { onPick: (s: CandidateStatus) => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [onClose]);
  return (
    <div
      ref={ref}
      role="menu"
      className="card"
      style={{ position: "absolute", zIndex: 20, padding: 6, marginTop: 4, minWidth: 220, right: 16 }}
    >
      {STATUS_ORDER.map((s) => (
        <button
          key={s}
          type="button"
          role="menuitem"
          className="palette-item"
          onClick={() => onPick(s)}
        >
          <StatusChip status={s} />
        </button>
      ))}
    </div>
  );
}

function CandidatePage(props: {
  candidate: Candidate;
  tab: "timeline" | "data" | "events";
  setTab: (t: "timeline" | "data" | "events") => void;
  interactions: Interaction[];
  events: CalendarEvent[];
  onBack: () => void;
  onStatus: () => void;
  onTransfer: () => void;
  onAddInteraction: () => void;
  onAddEvent: () => void;
  statusMenuFor: string | null;
  setStatusMenuFor: (id: string | null) => void;
  onChangeStatus: (id: string, s: CandidateStatus) => void;
}) {
  const c = props.candidate;
  const owner = userById(c.ownerId);
  const vac = vacancyById(c.vacancyId);
  return (
    <div>
      <div className="inline" style={{ marginBottom: 12 }}>
        <Button variant="ghost" size="sm" onClick={props.onBack}>
          ← Кандидаты
        </Button>
        <span className="muted" style={{ fontSize: 13 }}>
          Кандидаты / {shortName(c)}
        </span>
      </div>
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="candidate-hero">
          <Avatar name={fullName(c)} size="lg" />
          <div style={{ flex: 1, minWidth: 200 }}>
            <h1>{fullName(c)}</h1>
            <div className="sub">
              {vac?.title} · {c.city} · {c.source}
            </div>
            <div className="inline" style={{ marginTop: 10 }}>
              <StatusChip status={c.status} />
              <span className="inline">
                <Avatar name={owner?.name ?? "?"} />
                <span style={{ fontSize: 13 }}>{owner?.name}</span>
              </span>
            </div>
          </div>
          <div className="page-actions" style={{ position: "relative" }}>
            <Button variant="secondary" onClick={props.onAddInteraction}>
              + Взаимодействие
            </Button>
            <Button variant="secondary" onClick={props.onAddEvent}>
              Запланировать
            </Button>
            <Button
              variant="secondary"
              onClick={() =>
                props.setStatusMenuFor(props.statusMenuFor === c.id ? null : c.id)
              }
            >
              Статус
            </Button>
            <Button variant="primary" onClick={props.onTransfer}>
              Передать
            </Button>
            {props.statusMenuFor === c.id && (
              <StatusMenu onPick={(s) => props.onChangeStatus(c.id, s)} onClose={() => props.setStatusMenuFor(null)} />
            )}
          </div>
        </div>
      </div>

      <div className="candidate-layout">
        <section>
          <div className="tabs" role="tablist" aria-label="Разделы карточки">
            {(
              [
                ["timeline", "Timeline"],
                ["data", "Данные"],
                ["events", "События"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={props.tab === id}
                onClick={() => props.setTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {props.tab === "timeline" && (
            <ul className="timeline">
              {props.interactions.map((i) => (
                <li key={i.id} className="timeline-item">
                  <div className="timeline-mark" aria-hidden="true">
                    {i.type[0]!.toUpperCase()}
                  </div>
                  <div className="timeline-body">
                    <header>
                      <strong>{i.title}</strong>
                      <time dateTime={i.at}>{formatDate(i.at)}</time>
                    </header>
                    <p>{i.body}</p>
                    <p style={{ marginTop: 6 }}>
                      <span className="muted">{userById(i.actorId)?.name}</span>
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {props.tab === "data" && (
            <div className="card card-pad props-panel">
              <div className="prop">
                <span>Телефон</span>
                <strong>{c.phone}</strong>
              </div>
              <div className="prop">
                <span>Email</span>
                <strong>{c.email}</strong>
              </div>
              <div className="prop">
                <span>Город</span>
                <strong>{c.city}</strong>
              </div>
              <div className="prop">
                <span>Источник</span>
                <strong>{c.source}</strong>
              </div>
              <div className="prop">
                <span>Создан</span>
                <strong>{formatDate(c.createdAt)}</strong>
              </div>
            </div>
          )}
          {props.tab === "events" && (
            <div className="stack">
              {props.events.length === 0 ? (
                <EmptyState title="Нет событий" description="Запланируйте собеседование или звонок." action={<Button onClick={props.onAddEvent}>Запланировать</Button>} />
              ) : (
                props.events.map((e) => (
                  <div key={e.id} className="card card-pad inline" style={{ justifyContent: "space-between" }}>
                    <div>
                      <strong>{e.title}</strong>
                      <div className="muted" style={{ fontSize: 13 }}>
                        {formatDate(e.start)} · {e.type}
                      </div>
                    </div>
                    <StatusChip status={c.status} />
                  </div>
                ))
              )}
            </div>
          )}
        </section>
        <aside className="card card-pad props-panel" aria-label="Свойства">
          <h2 style={{ margin: "0 0 8px", font: "var(--text-subtitle)" }}>Свойства</h2>
          <div className="prop">
            <span>ID</span>
            <strong style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{c.id}</strong>
          </div>
          <div className="prop">
            <span>Вакансия</span>
            <strong>{vac?.title}</strong>
          </div>
          <div className="prop">
            <span>Подразделение</span>
            <strong>{vac?.department}</strong>
          </div>
          <div className="prop">
            <span>Следующий шаг</span>
            <strong>{c.nextActionAt ? formatDate(c.nextActionAt) : "Не задан"}</strong>
          </div>
        </aside>
      </div>
    </div>
  );
}

function CalendarPage({
  events,
  onOpenCandidate,
  onCreate,
  onToggleDone,
}: {
  events: CalendarEvent[];
  onOpenCandidate: (id: string) => void;
  onCreate: () => void;
  onToggleDone: (id: string) => void;
}) {
  // Fixed week around demo date 2026-09-01 (Mon) .. 09-07
  const days = [1, 2, 3, 4, 5, 6, 7].map((d) => `2026-09-0${d}`);
  const labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Календарь</h1>
          <p>Неделя 1–7 сентября 2026 (мок)</p>
        </div>
        <div className="page-actions">
          <Button variant="primary" onClick={onCreate}>
            + Событие
          </Button>
        </div>
      </div>
      <div className="cal-grid" role="grid" aria-label="Календарь на неделю">
        {labels.map((l) => (
          <div key={l} className="cal-head" role="columnheader">
            {l}
          </div>
        ))}
        {days.map((day) => {
          const dayEvents = events.filter((e) => e.start.startsWith(day));
          const isToday = day === "2026-09-02";
          return (
            <div key={day} className={`cal-cell${isToday ? " is-today" : ""}`} role="gridcell" aria-label={day}>
              <div className="cal-daynum">{Number(day.slice(-2))}</div>
              {dayEvents.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className="cal-event"
                  data-type={e.type}
                  style={{ opacity: e.done ? 0.5 : 1 }}
                  onClick={() => (e.candidateId ? onOpenCandidate(e.candidateId) : onToggleDone(e.id))}
                  title={e.title}
                >
                  {e.start.slice(11, 16)} {e.title}
                </button>
              ))}
            </div>
          );
        })}
      </div>
      <div className="card card-pad" style={{ marginTop: 16 }}>
        <h2 style={{ margin: "0 0 12px", font: "var(--text-subtitle)" }}>Список событий</h2>
        <div className="stack">
          {events.map((e) => (
            <div key={e.id} className="inline" style={{ justifyContent: "space-between" }}>
              <div>
                <strong>{e.title}</strong>
                <div className="muted" style={{ fontSize: 12 }}>
                  {formatDate(e.start)} · {userById(e.ownerId)?.name}
                </div>
              </div>
              <Button size="sm" variant="secondary" onClick={() => onToggleDone(e.id)}>
                {e.done ? "Вернуть" : "Выполнено"}
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function AnalyticsPage({ role }: { role: Role }) {
  const max = Math.max(...kpi.funnel.map((f) => f.count));
  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Аналитика</h1>
          <p>
            {kpi.periodLabel}
            {role === "hr" ? " · персональный срез (мок)" : " · команда подбора"}
          </p>
        </div>
        <div className="page-actions">
          <Select aria-label="Период" defaultValue="aug">
            <option value="aug">Август 2026</option>
            <option value="jul">Июль 2026</option>
            <option value="q">Квартал</option>
          </Select>
          <Button
            variant="secondary"
            onClick={() => {
              /* mock */
            }}
          >
            Экспорт
          </Button>
        </div>
      </div>
      <div className="grid-kpi">
        {[
          ["Создано", kpi.created, "+8% к июлю"],
          ["Дозвоны", kpi.reached, `${(kpi.conversionContactToInterview * 100).toFixed(0)}% → интервью`],
          ["Собеседования", kpi.interviewsDone, `${kpi.interviewsScheduled} назначено`],
          ["Выходы", kpi.hired, `${(kpi.conversionOfferToHire * 100).toFixed(0)}% с оффера`],
        ].map(([label, value, delta]) => (
          <div className="card kpi" key={String(label)}>
            <div className="label">{label}</div>
            <div className="value">{value}</div>
            <div className="delta">{delta}</div>
          </div>
        ))}
      </div>
      <div className="split-2">
        <section className="card card-pad">
          <h2 style={{ margin: "0 0 8px", font: "var(--text-subtitle)" }}>Воронка</h2>
          <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>
            Одна визуализация вместо сетки одинаковых плиток. Подписи и числа дублируют цвет.
          </p>
          <div className="funnel">
            {kpi.funnel.map((f) => (
              <div className="funnel-row" key={f.status}>
                <span style={{ fontSize: 13 }}>{STATUS_META[f.status].label}</span>
                <div className="funnel-bar-track">
                  <div className="funnel-bar" style={{ width: `${Math.round((f.count / max) * 100)}%` }}>
                    {f.count > 8 ? f.count : ""}
                  </div>
                </div>
                <strong style={{ fontVariantNumeric: "tabular-nums" }}>{f.count}</strong>
              </div>
            ))}
          </div>
        </section>
        <section className="card card-pad">
          <h2 style={{ margin: "0 0 12px", font: "var(--text-subtitle)" }}>По HR</h2>
          <div className="table-wrap" style={{ border: "none" }}>
            <table className="data" style={{ minWidth: 0 }}>
              <thead>
                <tr>
                  <th scope="col">HR</th>
                  <th scope="col">Обработано</th>
                  <th scope="col">Интервью</th>
                  <th scope="col">Выходы</th>
                </tr>
              </thead>
              <tbody>
                {kpi.byHr.map((row) => (
                  <tr key={row.ownerId} style={{ cursor: "default" }}>
                    <td>
                      <div className="inline">
                        <Avatar name={userById(row.ownerId)?.name ?? ""} />
                        {userById(row.ownerId)?.name}
                      </div>
                    </td>
                    <td>{row.processed}</td>
                    <td>{row.interviews}</td>
                    <td>{row.hired}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function UsersPage({
  users,
  onCreate,
  onToggleLock,
  onChangeRole,
}: {
  users: User[];
  onCreate: () => void;
  onToggleLock: (id: string) => void;
  onChangeRole: (id: string, role: Role) => void;
}) {
  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Пользователи</h1>
          <p>Учётные записи, роли и блокировки</p>
        </div>
        <div className="page-actions">
          <Button variant="primary" onClick={onCreate}>
            Создать пользователя
          </Button>
        </div>
      </div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Пользователь</th>
              <th scope="col">Логин</th>
              <th scope="col">Роль</th>
              <th scope="col">Статус</th>
              <th scope="col">Последний вход</th>
              <th scope="col">Действия</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} style={{ cursor: "default" }}>
                <td>
                  <div className="cell-main">
                    <Avatar name={u.name} />
                    <div className="meta">
                      <strong>{u.name}</strong>
                      <span>{u.email}</span>
                    </div>
                  </div>
                </td>
                <td>
                  <code style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{u.username}</code>
                </td>
                <td>
                  <Select
                    aria-label={`Роль ${u.name}`}
                    value={u.role}
                    onChange={(e) => onChangeRole(u.id, e.target.value as Role)}
                  >
                    <option value="hr">HR</option>
                    <option value="manager">Руководитель</option>
                    <option value="admin">Администратор</option>
                  </Select>
                </td>
                <td>
                  {u.locked ? <span className="chip chip-rose"><span className="chip-dot" />Заблокирован</span> : u.active ? <span className="chip chip-teal"><span className="chip-dot" />Активен</span> : <span className="chip chip-gray"><span className="chip-dot" />Неактивен</span>}
                </td>
                <td>{formatDate(u.lastLogin)}</td>
                <td>
                  <Button size="sm" variant="secondary" onClick={() => onToggleLock(u.id)}>
                    {u.locked ? "Разблокировать" : "Заблокировать"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AuditPage({ items }: { items: AuditEvent[] }) {
  const [action, setAction] = useState("");
  const filtered = items.filter((i) => !action || i.action.includes(action));
  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Журнал аудита</h1>
          <p>Неизменяемая история security- и business-событий (мок)</p>
        </div>
      </div>
      <div className="toolbar">
        <Input
          placeholder="Фильтр по action…"
          value={action}
          onChange={(e) => setAction(e.target.value)}
          aria-label="Фильтр action"
        />
      </div>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Время</th>
              <th scope="col">Актор</th>
              <th scope="col">Action</th>
              <th scope="col">Сущность</th>
              <th scope="col">Детали</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((a) => (
              <tr key={a.id} style={{ cursor: "default" }}>
                <td>{formatDate(a.at)}</td>
                <td>{userById(a.actorId)?.name ?? a.actorId}</td>
                <td>
                  <code style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{a.action}</code>
                </td>
                <td>
                  {a.entity}:{a.entityId}
                </td>
                <td>{a.detail}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SettingsPage({
  user,
  density,
  setDensity,
  onLogout,
  onExpire,
  onDegrade,
  onLoading,
  onEmpty,
  onForbidden,
}: {
  user: User;
  density: Density;
  setDensity: (d: Density) => void;
  onLogout: () => void;
  onExpire: () => void;
  onDegrade: () => void;
  onLoading: () => void;
  onEmpty: () => void;
  onForbidden: () => void;
}) {
  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Настройки</h1>
          <p>Профиль, плотность и демо-состояния интерфейса</p>
        </div>
      </div>
      <div className="split-2">
        <section className="card card-pad stack">
          <h2 style={{ margin: 0, font: "var(--text-subtitle)" }}>Профиль</h2>
          <div className="inline">
            <Avatar name={user.name} size="lg" />
            <div>
              <strong>{user.name}</strong>
              <div className="muted">
                {user.email} · {ROLE_LABEL[user.role]}
              </div>
            </div>
          </div>
          <Field label="Плотность">
            <Segmented
              label="Плотность"
              value={density}
              onChange={(v) => setDensity(v as Density)}
              options={[
                { value: "comfortable", label: "Комфорт" },
                { value: "compact", label: "Компакт" },
              ]}
            />
          </Field>
          <Button variant="danger" onClick={onLogout}>
            Выйти
          </Button>
        </section>
        <section className="card card-pad stack">
          <h2 style={{ margin: 0, font: "var(--text-subtitle)" }}>Демо-состояния</h2>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>
            Для ревью UX: session expired, permission denied, loading, empty, network error.
          </p>
          <Button variant="secondary" onClick={onExpire}>
            Session expired
          </Button>
          <Button variant="secondary" onClick={onForbidden}>
            Permission denied
          </Button>
          <Button variant="secondary" onClick={onLoading}>
            Loading skeleton
          </Button>
          <Button variant="secondary" onClick={onEmpty}>
            Empty state
          </Button>
          <Button variant="secondary" onClick={onDegrade}>
            Network / backend error
          </Button>
        </section>
      </div>
    </div>
  );
}

/* ——— Dialogs & palette ——— */

function CommandPalette({
  open,
  onClose,
  candidates,
  onNavigate,
  onOpenCandidate,
  onAction,
  role,
}: {
  open: boolean;
  onClose: () => void;
  candidates: Candidate[];
  onNavigate: (r: Route) => void;
  onOpenCandidate: (id: string) => void;
  onAction: (a: string) => void;
  role: Role;
}) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  type Item = { id: string; group: string; label: string; hint?: string; run: () => void };
  const items: Item[] = useMemo(() => {
    const navItems: Item[] = [
      { id: "n-home", group: "Разделы", label: "Главная", hint: "G H", run: () => onNavigate("home") },
      { id: "n-queue", group: "Разделы", label: "Моя очередь", hint: "G Q", run: () => onNavigate("queue") },
      { id: "n-cand", group: "Разделы", label: "Кандидаты", hint: "G C", run: () => onNavigate("candidates") },
      { id: "n-cal", group: "Разделы", label: "Календарь", hint: "G K", run: () => onNavigate("calendar") },
      { id: "n-an", group: "Разделы", label: "Аналитика", hint: "G A", run: () => onNavigate("analytics") },
    ];
    if (role === "admin") {
      navItems.push(
        { id: "n-u", group: "Разделы", label: "Пользователи", run: () => onNavigate("users") },
        { id: "n-a", group: "Разделы", label: "Журнал аудита", run: () => onNavigate("audit") },
      );
    }
    const commands: Item[] = [
      { id: "c-tr", group: "Команды", label: "Передать текущего кандидата", run: () => onAction("transfer") },
      { id: "c-in", group: "Команды", label: "Добавить взаимодействие", run: () => onAction("interaction") },
      { id: "c-ev", group: "Команды", label: "Запланировать событие", run: () => onAction("event") },
      { id: "c-den", group: "Команды", label: "Переключить плотность", run: () => onAction("density") },
    ];
    if (role === "admin") commands.push({ id: "c-user", group: "Команды", label: "Создать пользователя", run: () => onAction("user") });

    const ql = q.trim().toLowerCase();
    const candItems: Item[] = candidates
      .filter((c) => !ql || fullName(c).toLowerCase().includes(ql) || c.phone.includes(ql))
      .slice(0, 8)
      .map((c) => ({
        id: c.id,
        group: "Кандидаты",
        label: fullName(c),
        hint: STATUS_META[c.status].label,
        run: () => onOpenCandidate(c.id),
      }));

    const all = [...candItems, ...commands, ...navItems];
    if (!ql) return all;
    return all.filter((i) => i.label.toLowerCase().includes(ql) || i.group.toLowerCase().includes(ql));
  }, [q, candidates, onNavigate, onOpenCandidate, onAction, role]);

  useEffect(() => {
    if (open) {
      setQ("");
      setActive(0);
      window.setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [q]);

  if (!open) return null;

  const groups = items.reduce<Record<string, Item[]>>((acc, item) => {
    (acc[item.group] ??= []).push(item);
    return acc;
  }, {});

  const flat = items;

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, flat.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      flat[active]?.run();
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  let idx = -1;
  return (
    <div className="overlay overlay-palette" role="presentation" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Командная палитра" onKeyDown={onKeyDown}>
        <input
          ref={inputRef}
          className="palette-input"
          placeholder="Кандидат, команда или раздел…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-autocomplete="list"
          aria-controls="palette-list"
        />
        <div className="palette-list" id="palette-list" role="listbox">
          {flat.length === 0 && <div className="palette-group">Ничего не найдено</div>}
          {Object.entries(groups).map(([group, list]) => (
            <div key={group}>
              <div className="palette-group">{group}</div>
              {list.map((item) => {
                idx += 1;
                const my = idx;
                return (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    className="palette-item"
                    aria-selected={my === active}
                    onMouseEnter={() => setActive(my)}
                    onClick={() => item.run()}
                  >
                    {item.label}
                    {item.hint ? <span>{item.hint}</span> : null}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function TransferDialog({
  open,
  candidate,
  users,
  onClose,
  onSubmit,
}: {
  open: boolean;
  candidate: Candidate | null;
  users: User[];
  onClose: () => void;
  onSubmit: (toId: string, reason: string) => void;
}) {
  const [toId, setToId] = useState(users[0]?.id ?? "");
  const [reason, setReason] = useState("");
  useEffect(() => {
    if (open) {
      setReason("");
      setToId(users.find((u) => u.id !== candidate?.ownerId)?.id ?? users[0]?.id ?? "");
    }
  }, [open, users, candidate]);
  const can = reason.trim().length >= 3 && !!toId;
  return (
    <Modal
      open={open}
      title="Передача кандидата"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" disabled={!can} onClick={() => can && onSubmit(toId, reason.trim())}>
            Подтвердить передачу
          </Button>
        </>
      }
    >
      {candidate ? (
        <p style={{ margin: 0 }}>
          <strong>{fullName(candidate)}</strong> — текущий ответственный: {userById(candidate.ownerId)?.name}
        </p>
      ) : (
        <p className="muted">Сначала откройте кандидата.</p>
      )}
      <Field label="Новый ответственный HR">
        <Select value={toId} onChange={(e) => setToId(e.target.value)} aria-label="Новый HR">
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Причина передачи" hint="Минимум 3 символа — обязательное поле по ТЗ" error={reason.length > 0 && reason.trim().length < 3 ? "Слишком коротко" : undefined}>
        <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} required aria-required="true" />
      </Field>
    </Modal>
  );
}

function InteractionDialog({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (p: { type: Interaction["type"]; title: string; body: string; at: string }) => void;
}) {
  const [type, setType] = useState<Interaction["type"]>("call");
  const [body, setBody] = useState("");
  useEffect(() => {
    if (open) setBody("");
  }, [open]);
  const titles: Record<string, string> = {
    call: "Звонок",
    email: "Письмо",
    note: "Заметка",
    meeting: "Встреча",
  };
  return (
    <Modal
      open={open}
      title="Новое взаимодействие"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Отмена
          </Button>
          <Button
            variant="primary"
            disabled={body.trim().length < 2}
            onClick={() =>
              onSubmit({
                type,
                title: titles[type] ?? "Событие",
                body: body.trim(),
                at: new Date().toISOString(),
              })
            }
          >
            Сохранить
          </Button>
        </>
      }
    >
      <Field label="Тип">
        <Select value={type} onChange={(e) => setType(e.target.value as Interaction["type"])}>
          <option value="call">Звонок</option>
          <option value="email">Email</option>
          <option value="note">Заметка</option>
          <option value="meeting">Встреча</option>
        </Select>
      </Field>
      <Field label="Комментарий">
        <Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4} />
      </Field>
    </Modal>
  );
}

function EventDialog({
  open,
  candidateId,
  onClose,
  onSubmit,
}: {
  open: boolean;
  candidateId: string | null;
  onClose: () => void;
  onSubmit: (e: Omit<CalendarEvent, "id" | "ownerId" | "done">) => void;
}) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<CalendarEvent["type"]>("interview");
  const [start, setStart] = useState("2026-09-03T11:00");
  useEffect(() => {
    if (open) {
      setTitle("");
      setType("interview");
    }
  }, [open]);
  return (
    <Modal
      open={open}
      title="Планирование события"
      onClose={onClose}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Отмена
          </Button>
          <Button
            variant="primary"
            disabled={title.trim().length < 2}
            onClick={() =>
              onSubmit({
                title: title.trim(),
                type,
                start: `${start}:00`,
                end: `${start}:00`,
                candidateId: candidateId ?? undefined,
              })
            }
          >
            Создать
          </Button>
        </>
      }
    >
      <Field label="Название">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Тип">
        <Select value={type} onChange={(e) => setType(e.target.value as CalendarEvent["type"])}>
          <option value="interview">Собеседование</option>
          <option value="call">Звонок</option>
          <option value="reminder">Напоминание</option>
          <option value="other">Другое</option>
        </Select>
      </Field>
      <Field label="Начало">
        <Input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
      </Field>
    </Modal>
  );
}

function CreateUserDialog({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (u: User) => void;
}) {
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("hr");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    if (open) {
      setName("");
      setUsername("");
      setEmail("");
      setPassword("");
      setRole("hr");
      setErr("");
    }
  }, [open]);
  const submit = () => {
    if (!name.trim() || !username.trim() || !email.trim() || password.length < 8) {
      setErr("Заполните поля. Пароль обязателен, минимум 8 символов (мок-правило).");
      return;
    }
    if (!email.endsWith("@example.com") && !email.endsWith("@example.org") && !email.endsWith("@example.net")) {
      setErr("В prototype email только на example.com / .org / .net");
      return;
    }
    onSubmit({
      id: `u-${Date.now()}`,
      name: name.trim(),
      username: username.trim(),
      email: email.trim(),
      role,
      active: true,
      locked: false,
      lastLogin: new Date().toISOString(),
    });
  };
  return (
    <Modal
      open={open}
      title="Создание пользователя"
      onClose={onClose}
      large
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="primary" onClick={submit}>
            Создать
          </Button>
        </>
      }
    >
      <div className="split-2">
        <Field label="ФИО">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Логин">
          <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
        </Field>
        <Field label="Email">
          <Input value={email} onChange={(e) => setEmail(e.target.value)} type="email" placeholder="name@example.com" />
        </Field>
        <Field label="Роль">
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="hr">HR</option>
            <option value="manager">Руководитель</option>
            <option value="admin">Администратор</option>
          </Select>
        </Field>
        <Field label="Временный пароль" hint="Обязателен по требованиям безопасности">
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        </Field>
      </div>
      {err ? (
        <div className="field-error" role="alert">
          {err}
        </div>
      ) : null}
    </Modal>
  );
}
