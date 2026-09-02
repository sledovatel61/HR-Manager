import { useCallback, useEffect, useState } from "react";
import { fetchCurrentUser, login as defaultLogin } from "./api";
import Dashboard from "./components/Dashboard";
import LoginForm from "./components/LoginForm";
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

  return (
    <div className="page">
      <header className="header">
        <h1>HR Manager</h1>
        <p>Идентификация и безопасность — вход, роли и аудит</p>
      </header>

      {state === "loading" && (
        <section className="panel">
          <p className="muted">Проверяем сессию…</p>
        </section>
      )}

      {state === "anonymous" && (
        <LoginForm
          onLoggedIn={(me) => {
            setCurrent(me);
            setState("authenticated");
          }}
          loginFetcher={loginFetcher}
        />
      )}

      {state === "authenticated" && current && (
        <Dashboard
          current={current}
          onLoggedOut={() => {
            setCurrent(null);
            setState("anonymous");
          }}
        />
      )}
    </div>
  );
}
