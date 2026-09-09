import { useCallback, useEffect, useState } from "react";
import {
  fetchCurrentUser,
  login as defaultLogin,
  redeemOwnerSetup as defaultRedeemOwnerSetup,
} from "./api";
import LoginForm from "./components/LoginForm";
import FirstRunSetup from "./components/FirstRunSetup";
import Workspace from "./app-shell/Workspace";
import { ToastProvider } from "./design-system/components/Toast";
import type { CurrentUser, SetupPreview } from "./types";

type AuthState = "loading" | "anonymous" | "authenticated";

interface AppProps {
  /** Injectable for tests; defaults to the real API call. */
  currentUserFetcher?: () => Promise<CurrentUser | null>;
  /** Injectable for tests; defaults to the real login call. */
  loginFetcher?: (username: string, password: string) => Promise<CurrentUser>;
  /** Injectable for tests; first-run preview call. */
  setupPreviewFetcher?: (ticket: string) => Promise<SetupPreview>;
  /** Injectable for tests; first-run redeem call. */
  setupRedeemFetcher?: typeof defaultRedeemOwnerSetup;
}

/** Phase 12: the installer hands the one-time first-run ticket to the
 * browser via the URL fragment (`#setup=<ticket>`). The ticket never goes
 * into a query parameter, referrer or log. */
function readSetupTicket(): string | null {
  const match = window.location.hash.match(/^#setup=(.+)$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

/** Application shell: restores the session on load and gates on auth. */
export default function App({
  currentUserFetcher,
  loginFetcher = defaultLogin,
  setupPreviewFetcher,
  setupRedeemFetcher,
}: AppProps) {
  const [state, setState] = useState<AuthState>("loading");
  const [current, setCurrent] = useState<CurrentUser | null>(null);
  const [setupTicket, setSetupTicket] = useState<string | null>(() => readSetupTicket());

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
    if (setupTicket) {
      // Fresh install: the installer handed over the one-time ticket.
      return (
        <FirstRunSetup
          ticket={setupTicket}
          onComplete={(me) => {
            setCurrent(me);
            setSetupTicket(null);
            setState("authenticated");
          }}
          previewFetcher={setupPreviewFetcher}
          redeemFetcher={setupRedeemFetcher}
        />
      );
    }
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
