import { useEffect } from "react";
import { AppStateProvider, useAppState } from "./state/AppState";
import { RouterProvider, useRouter } from "./router";
import { AppShell } from "./components/shell/AppShell";
import { ToastViewport } from "./components/ui/Toast";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { QueuePage } from "./pages/QueuePage";
import { CandidatesPage } from "./pages/CandidatesPage";
import { KanbanPage } from "./pages/KanbanPage";
import { CalendarPage } from "./pages/CalendarPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { TemplatesPage } from "./pages/TemplatesPage";
import { UsersPage } from "./pages/UsersPage";
import { AuditPage } from "./pages/AuditPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SessionExpiredState, PermissionDeniedState } from "./components/ui/StateViews";
import { userById } from "./data/mockData";

function ThemeSync() {
  const { theme, density } = useAppState();
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  useEffect(() => {
    document.documentElement.setAttribute("data-density", density);
  }, [density]);
  return null;
}

function AuthenticatedApp() {
  const { route } = useRouter();
  const { currentUserId } = useAppState();
  const currentUser = userById(currentUserId)!;

  function renderRoute() {
    switch (route) {
      case "home":
        return <HomePage />;
      case "queue":
        return currentUser.role === "hr" ? <QueuePage /> : <PermissionDeniedState />;
      case "candidates":
        return <CandidatesPage />;
      case "kanban":
        return <KanbanPage />;
      case "calendar":
        return <CalendarPage />;
      case "analytics":
        return currentUser.role === "hr" ? <PermissionDeniedState /> : <AnalyticsPage />;
      case "templates":
        return <TemplatesPage />;
      case "users":
        return <UsersPage />;
      case "audit":
        return <AuditPage />;
      case "settings":
        return <SettingsPage />;
      default:
        return <HomePage />;
    }
  }

  return <AppShell>{renderRoute()}</AppShell>;
}

function Root() {
  const { isAuthenticated, sessionExpired, restoreSession } = useAppState();
  const { navigate } = useRouter();

  if (sessionExpired) {
    return (
      <div className="session-expired-screen">
        <SessionExpiredState
          onRestore={() => {
            restoreSession();
            navigate("home");
          }}
        />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return <AuthenticatedApp />;
}

export default function App() {
  return (
    <AppStateProvider>
      <RouterProvider>
        <ThemeSync />
        <Root />
        <ToastViewport />
      </RouterProvider>
    </AppStateProvider>
  );
}
