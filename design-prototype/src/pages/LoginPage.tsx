import { useState, type FormEvent } from "react";
import { Icon } from "../icons/Icon";
import { Button } from "../components/ui/Button";
import { Field, TextInput } from "../components/ui/Field";
import { useAppState } from "../state/AppState";
import { USERS } from "../data/mockData";
import { ROLE_LABELS } from "../types";
import "./loginPage.css";

/**
 * Экран входа — визуально согласован с существующим `LoginForm.tsx`
 * (username + password, единый submit), но добавляет продуктовое
 * оформление. Пароль не проверяется по-настоящему: любой непустой ввод
 * "входит" под выбранного мокового пользователя (см. README прототипа).
 */
export function LoginPage() {
  const { login, pushToast } = useAppState();
  const [username, setUsername] = useState("a.smirnova");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (!password.trim()) {
      setError("Введите пароль. Это обязательное поле.");
      return;
    }
    setSubmitting(true);
    window.setTimeout(() => {
      setSubmitting(false);
      const known = USERS.some((u) => u.username === username);
      if (!known) {
        setError("Пользователь не найден. Проверьте имя пользователя.");
        return;
      }
      login(username);
      pushToast("success", "Добро пожаловать! Сессия защищена.");
    }, 420);
  }

  return (
    <div className="login-screen">
      <div className="login-visual" aria-hidden="true">
        <div className="login-visual-badge">
          <Icon name="spark" size={22} />
        </div>
        <h2>Единая база кандидатов для всей команды подбора</h2>
        <p>
          Очередь, Kanban, звонки, собеседования и аналитика — в одном месте.
          Права проверяются на сервере, каждое действие — в журнале аудита.
        </p>
        <ul className="login-visual-points">
          <li><Icon name="check-circle" size={14} /> Совместная работа HR без потери контекста</li>
          <li><Icon name="check-circle" size={14} /> Прозрачные передачи кандидатов с подтверждением</li>
          <li><Icon name="check-circle" size={14} /> Аналитика воронки в реальном времени</li>
        </ul>
      </div>

      <div className="login-form-wrap">
        <form className="login-card" onSubmit={handleSubmit} noValidate>
          <div className="login-card-head">
            <span className="brand-mark" aria-hidden="true"><Icon name="spark" size={16} /></span>
            <div>
              <h1 className="login-title">Вход в HR Manager</h1>
              <p className="login-subtitle">Внутренняя система подбора персонала</p>
            </div>
          </div>

          <Field label="Имя пользователя" required>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                name="username"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                required
              />
            )}
          </Field>

          <Field label="Пароль" required error={error ?? undefined}>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="password"
                name="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                invalid={Boolean(error)}
              />
            )}
          </Field>

          <Button type="submit" variant="primary" loading={submitting} style={{ width: "100%", marginTop: 4 }}>
            Войти
          </Button>

          <div className="login-demo-users">
            <p>Демо-пользователи прототипа (пароль — любой текст):</p>
            <div className="login-demo-list">
              {USERS.map((u) => (
                <button
                  type="button"
                  key={u.id}
                  className="login-demo-chip"
                  onClick={() => setUsername(u.username)}
                >
                  {u.fullName} · {ROLE_LABELS[u.role]}
                </button>
              ))}
            </div>
          </div>
        </form>
        <p className="login-footnote">
          Все данные на этом экране синтетические. Прототип не отправляет запросы на сервер.
        </p>
      </div>
    </div>
  );
}
