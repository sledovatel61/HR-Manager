import { useState, type FormEvent } from "react";
import { login } from "../api";
import type { CurrentUser } from "../types";

interface LoginFormProps {
  /** Called after a successful login. */
  onLoggedIn: (current: CurrentUser) => void;
  /** Injectable for tests; defaults to the real API call. */
  loginFetcher?: (username: string, password: string) => Promise<CurrentUser>;
}

/** Username/password login form. Credentials are posted over the session API. */
export default function LoginForm({ onLoggedIn, loginFetcher = login }: LoginFormProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const current = await loginFetcher(username.trim(), password);
      onLoggedIn(current);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось войти.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel auth-panel">
      <h2 className="panel-title">Вход в HR Manager</h2>
      <p className="panel-subtitle">Введите имя пользователя и пароль.</p>

      <form onSubmit={(event) => void handleSubmit(event)} className="auth-form">
        <label className="form-field">
          <span>Имя пользователя</span>
          <input
            type="text"
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            autoFocus
          />
        </label>

        <label className="form-field">
          <span>Пароль</span>
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="primary-button" disabled={submitting}>
          {submitting ? "Входим…" : "Войти"}
        </button>
      </form>
    </section>
  );
}
