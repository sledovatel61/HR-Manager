import { useCallback, useEffect, useState } from "react";
import { fetchCurrentUser, login as defaultLogin } from "./api";
import LoginForm from "./components/LoginForm";
import Workspace from "./app-shell/Workspace";
import { ToastProvider } from "./design-system/components/Toast";
import type { CurrentUser } from "./types";

type AuthState = "loading" | "anonymous" | "authenticated";

interface AppProps {
  /** Injectable for tests; defaults to the real API call. */
  currentUserFetcher?: () => Promise<CurrentUser | null>;
  /** Injectable for tests; defaults to the real login call. */
  loginFetcher?: (username: string, password: string) => Promise<CurrentUser>;
}

/** Application shell: restores the session on load and gates on auth. */
export default function App({ currentUserFetcher, loginFetcher = defaultLogin }: AppProps) {
  const [state, setState] = useState<AuthState>("loading");
  const [current, setCurrent] = useState<CurrentUser | null>(null);

  const restore = useCallback(async () => {
    const fetcher = currentUserFetcher ?? fetchCurrentUser;
    try {
      const me = await fetcher();
      setCurrent(me);
      setState("authenticated");
    } catch {
      setCurrent(null);
      setState("anonymous");
    }
  }, [currentUserFetcher]);

  useEffect(() => {
    void restore();
  }, [restore]);

  if (state === "loading") {
    return (
      <div className="auth-screen">
        <section className="panel">
          <p className="muted">Проверяем сессию…</p>
        </section>
      </div>
    );
  }

  if (state === "anonymous" || !current) {
    return (
      <div className="page">
        <header className="header">
          <h1>HR Manager</h1>
          <p>Вход в рабочее пространство рекрутинга</p>
        </header>
        <LoginForm
          onLoggedIn={(me) => {
            setCurrent(me);
            setState("authenticated");
          }}
          loginFetcher={loginFetcher}
        />
      </div>
    );
  }

  return (
    <ToastProvider>
      <Workspace
        current={current}
        onLoggedOut={() => {
          setCurrent(null);
          setState("anonymous");
        }}
      />
    </ToastProvider>
  );
}
