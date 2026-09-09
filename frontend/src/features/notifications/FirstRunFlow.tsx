/** First run of the local Windows pilot (phase 12).

 * The installer arms a one-shot exchange token in the backend environment and
 * opens `/#first-run?t=<token>`. This component claims the token, shows the
 * owner their surname and working mode, and requires them to set their own
 * password before entering the workspace. The token is single-use and
 * loopback-bound; the surname/working mode never travel through the URL
 * (they are read server-side from the installer-injected environment).
 */

import { useCallback, useEffect, useState } from "react";
import { claimFirstRun, setOwnPassword } from "../../api";
import { ROLE_LABELS, type CurrentUser, type FirstRunStatus, type User } from "../../types";
import { Button } from "../../design-system/components/Button";
import { Field, TextInput } from "../../design-system/components/Field";
import { tokenFromHash } from "./firstRunToken";
import "./notifications.css";

interface FirstRunFlowProps {
  status: FirstRunStatus;
  onDone: (current: CurrentUser) => void;
  claimFetcher?: (token: string) => Promise<CurrentUser>;
  passwordFetcher?: (password: string) => Promise<User>;
  /** Location hash to read the exchange token from (injectable for tests). */
  hash?: string;
}

type Step = "claiming" | "welcome" | "error" | "waiting";

export function FirstRunFlow({
  status,
  onDone,
  claimFetcher = claimFirstRun,
  passwordFetcher = setOwnPassword,
  hash,
}: FirstRunFlowProps) {
  const [step, setStep] = useState<Step>("claiming");
  const [current, setCurrent] = useState<CurrentUser | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const claim = useCallback(async () => {
    const token = tokenFromHash(hash);
    if (!token) {
      setStep("waiting");
      return;
    }
    setStep("claiming");
    setError(null);
    try {
      const claimed = await claimFetcher(token);
      setCurrent(claimed);
      setStep("welcome");
      // The one-shot token is consumed server-side; drop it from the address
      // bar so it cannot linger in a screenshot or be replayed visually.
      if (window.location.hash.includes("t=")) {
        window.history.replaceState(null, "", "#first-run");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось завершить первый запуск.");
      setStep("error");
    }
  }, [claimFetcher, hash]);

  useEffect(() => {
    void claim();
  }, [claim]);

  const savePassword = useCallback(async () => {
    if (password.length < 12) {
      setError("Пароль должен содержать не менее 12 символов.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Пароли не совпадают.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const updated = await passwordFetcher(password);
      if (current) {
        const next = { ...current, user: updated };
        setCurrent(next);
        onDone(next);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось сохранить пароль.");
    } finally {
      setSaving(false);
    }
  }, [confirmPassword, current, onDone, password, passwordFetcher]);

  if (step === "waiting") {
    const hint = status.needs_password
      ? "Осталось задать пароль владельца. Откройте HR Manager из ярлыка «HR Manager», чтобы продолжить."
      : status.pending
        ? "Установка ещё не завершена. Откройте HR Manager из ярлыка «HR Manager» или запустите установку ещё раз."
        : "Продолжите настройку из ярлыка «HR Manager».";
    return (
      <section className="panel auth-panel" data-testid="first-run-waiting">
        <h2 className="panel-title">Первый запуск HR Manager</h2>
        <p className="panel-subtitle">{hint}</p>
        <Button variant="secondary" onClick={() => void claim()}>
          Проверить снова
        </Button>
      </section>
    );
  }

  if (step === "error") {
    return (
      <section className="panel auth-panel" role="alert">
        <h2 className="panel-title">Не удалось завершить первый запуск</h2>
        <p className="form-error">{error}</p>
        <p className="panel-subtitle">
          Закройте это окно и откройте приложение из ярлыка «HR Manager» — установка продолжится с
          того же шага.
        </p>
        <Button variant="secondary" onClick={() => void claim()}>
          Повторить
        </Button>
      </section>
    );
  }

  if (step === "claiming") {
    return (
      <section className="panel auth-panel">
        <h2 className="panel-title">Первый запуск HR Manager</h2>
        <p className="panel-subtitle">Подготавливаем вашу учётную запись…</p>
      </section>
    );
  }

  const modeLabel = current ? ROLE_LABELS[current.user.role] ?? "Администратор" : "";
  const workingModeLabel =
    current && current.user.working_mode ? ROLE_LABELS[current.user.working_mode] : "";

  return (
    <section className="panel auth-panel">
      <h2 className="panel-title">Добро пожаловать, {current?.user.full_name || "владелец"}!</h2>
      <p className="panel-subtitle">
        Этот компьютер стал локальным сервером HR Manager. Вы — владелец установки с полным
        доступом.
      </p>
      <ul className="first-run-summary">
        <li>
          Ваш рабочий режим: <strong>{workingModeLabel || modeLabel}</strong>
        </li>
        <li>
          Технический логин: <code>{current?.user.username}</code> (вам не нужно его запоминать)
        </li>
      </ul>
      <p className="notif-card-body">
        Рабочий режим определяет, с какого экрана вы начинаете работу. Полный доступ остаётся у вас
        независимо от выбранного режима; обычным сотрудникам роли назначаются в разделе
        «Администрирование».
      </p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void savePassword();
        }}
        className="auth-form"
      >
        <h3 className="panel-subtitle">Задайте пароль для входа</h3>
        <Field label="Пароль" hint="Не менее 12 символов, с буквами и цифрами.">
          {(id) => (
            <TextInput
              id={id}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>
        <Field label="Повторите пароль">
          {(id) => (
            <TextInput
              id={id}
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
            />
          )}
        </Field>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <Button type="submit" variant="primary" loading={saving}>
          Сохранить и продолжить
        </Button>
        <p className="notif-card-body">
          Часовая зона, рабочие дни и тихие часы настраиваются на следующем шаге. Email и Telegram
          необязательны — их можно пропустить.
        </p>
      </form>
    </section>
  );
}
