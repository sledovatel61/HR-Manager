import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { claimFirstRun, fetchFirstRunState, fetchOpsReadiness, setFirstRunPassword } from "../api";
import type { FirstRunClaimResult, FirstRunState, OpsReadinessStatus } from "../types";

type Step = "checking" | "code" | "password" | "ready" | "closed";

import { WORK_ROLE_LABELS } from "./firstRunLabels";

interface FirstRunProps {
  /** Инъекции для тестов; по умолчанию — реальные вызовы локального API. */
  stateFetcher?: () => Promise<FirstRunState>;
  claimFetcher?: (code: string) => Promise<FirstRunClaimResult>;
  passwordFetcher?: (password: string) => Promise<void>;
  statusFetcher?: () => Promise<OpsReadinessStatus>;
  onFinished?: () => void;
}

/**
 * Первый вход локального пилота. Одностраничный поток без входа в рабочее
 * пространство: код配对 (выдаёт установщик, одноразовый, 15 минут) → учётная
 * запись уже создана сервером (имя придумано детерминированно) → пользователь
 * задаёт ПАРОЛЬ здесь, в браузере (он никогда не проходит через аргументы
 * установщика/CLI) → подтверждение готовности сервисов.
 */
export default function FirstRun({
  stateFetcher = fetchFirstRunState,
  claimFetcher = claimFirstRun,
  passwordFetcher = setFirstRunPassword,
  statusFetcher = fetchOpsReadiness,
  onFinished,
}: FirstRunProps) {
  const [step, setStep] = useState<Step>("checking");
  const [state, setState] = useState<FirstRunState | null>(null);
  const [claim, setClaim] = useState<FirstRunClaimResult | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [readiness, setReadiness] = useState<OpsReadinessStatus | null>(null);
  const [readinessError, setReadinessError] = useState<string | null>(null);

  // Как открылись — спрашиваем публичное состояние (booleans, без PII).
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const s = await stateFetcher();
        if (!alive) return;
        setState(s);
        if (s.pilot_owner_exists) setStep("closed");
        else setStep("code");
      } catch {
        if (alive) {
          setState(null);
          setStep("code");
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [stateFetcher]);

  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const pollReadiness = useCallback(async () => {
    while (mountedRef.current) {
      try {
        const s = await statusFetcher();
        if (!mountedRef.current) return;
        setReadiness(s);
        setReadinessError(null);
        if (s.status === "ok") return;
      } catch {
        if (!mountedRef.current) return;
        setReadinessError("Приложение ещё отвечает не полностью — ждём…");
      }
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }, [statusFetcher]);

  const handleClaim = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    const clean = code.trim().toUpperCase().replace(/\s+/g, "");
    if (!/^[A-Z0-9]{6,8}$/.test(clean)) {
      setError("Код состоит из 6–8 символов (латиница и цифры) — проверьте ввод.");
      return;
    }
    setBusy(true);
    try {
      const result = await claimFetcher(clean);
      setClaim(result);
      setStep("password");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось подтвердить код.");
    } finally {
      setBusy(false);
    }
  };

  const handlePassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (password.length < 12 || password.length > 128) {
      setError("Пароль должен содержать от 12 до 128 символов.");
      return;
    }
    if (!/[а-яa-z]/i.test(password) || !/\d/.test(password)) {
      setError("Пароль должен содержать хотя бы одну букву и одну цифру.");
      return;
    }
    if (claim && password.trim().toLowerCase() === claim.user.username.toLowerCase()) {
      setError("Пароль не должен совпадать с именем пользователя.");
      return;
    }
    if (password !== password2) {
      setError("Пароли не совпадают.");
      return;
    }
    setBusy(true);
    try {
      await passwordFetcher(password);
      setStep("ready");
      void pollReadiness();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Не удалось сохранить пароль.");
    } finally {
      setBusy(false);
    }
  };

  const finish = () => {
    if (onFinished) onFinished();
    else window.location.assign("/");
  };

  return (
    <div className="page">
      <header className="header">
        <h1>HR Manager — первый запуск</h1>
        <p>Локальный пилот на этом компьютере</p>
      </header>

      {step === "checking" && (
        <section className="panel auth-panel">
          <p className="muted">Проверяем состояние установки…</p>
        </section>
      )}

      {step === "closed" && (
        <section className="panel auth-panel">
          <h2 className="panel-title">Пилот уже настроен</h2>
          <p>
            Первый вход выполнен ранее — повторная выдача невозможна (это защита:
            второй «главный» аккаунт не появится). Войдите в приложение под своим
            пользователем.
          </p>
          <button type="button" className="primary-button" onClick={finish}>
            Ко входу
          </button>
        </section>
      )}

      {step === "code" && (
        <section className="panel auth-panel">
          <h2 className="panel-title">Код первого входа</h2>
          <p className="panel-subtitle">
            Код показал установщик в конце установки. Он одноразовый и живой 15
            минут. Учётную запись создаст сервер: имя придумано автоматически по
            вашей фамилии, пароль вы зададите на следующем шаге.
          </p>
          {state?.pending && state.pending_expires_in_seconds != null && (
            <p className="muted">
              Для этого компьютера уже выдан код (ждём ввода ~
              {Math.ceil(state.pending_expires_in_seconds / 60)} мин).
              Режим работы: {WORK_ROLE_LABELS[state.pending_work_role ?? "hr"]}.
            </p>
          )}
          <form onSubmit={(event) => void handleClaim(event)} className="auth-form">
            <label className="form-field">
              <span>Код подтверждения</span>
              <input
                type="text"
                name="code"
                value={code}
                autoComplete="one-time-code"
                spellCheck={false}
                onChange={(event) => setCode(event.target.value.toUpperCase())}
                autoFocus
              />
            </label>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="primary-button" disabled={busy}>
              {busy ? "Проверяем…" : "Подтвердить код"}
            </button>
          </form>
        </section>
      )}

      {step === "password" && claim && (
        <section className="panel auth-panel">
          <h2 className="panel-title">Учётная запись создана</h2>
          <p>
            Имя пользователя: <strong>{claim.user.username}</strong> · Режим:{" "}
            <strong>{WORK_ROLE_LABELS[claim.user.work_role ?? "hr"]}</strong>.
            Сгенерированный сервером пароль вы не видите и не вводите — задайте
            свой постоянный пароль прямо сейчас.
          </p>
          <form onSubmit={(event) => void handlePassword(event)} className="auth-form">
            <label className="form-field">
              <span>Новый пароль</span>
              <input
                type="password"
                name="password"
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoFocus
              />
            </label>
            <label className="form-field">
              <span>Повторите пароль</span>
              <input
                type="password"
                name="password2"
                autoComplete="new-password"
                value={password2}
                onChange={(event) => setPassword2(event.target.value)}
              />
            </label>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" className="primary-button" disabled={busy}>
              {busy ? "Сохраняем…" : "Сохранить пароль"}
            </button>
          </form>
        </section>
      )}

      {step === "ready" && (
        <section className="panel auth-panel">
          <h2 className="panel-title">Готовность локального контура</h2>
          <p className="panel-subtitle">
            Подтверждаем, что приложение, база, фоновые задачи и резервные копии
            работают, прежде чем открыть рабочее место.
          </p>
          <ul style={{ lineHeight: 1.9 }}>
            <li>{readiness?.status === "ok" ? "✅" : "⏳"} Приложение (HTTP-интерфейс)</li>
            <li>{readiness?.database?.status === "ok" ? "✅" : "⏳"} База данных (локальная)</li>
            <li>{readiness?.migrations?.ok ? "✅" : "⏳"} Структура базы: актуальная</li>
            <li>{readiness?.notifications?.worker_alive ? "✅" : "⏳"} Фоновые задачи (worker)</li>
            <li>{readiness?.backup?.ok === true ? "✅" : "⏳"} Резервные копии</li>
          </ul>
          {readinessError && <p className="muted">{readinessError}</p>}
          <button
            type="button"
            className="primary-button"
            onClick={finish}
            disabled={readiness?.status !== "ok"}
          >
            {readiness?.status === "ok" ? "Открыть HR Manager" : "Ждём готовности…"}
          </button>
          <p className="muted">
            Дальше: часы работы, тихие часы и интеграции (Telegram/email) настраиваются
            в разделе «Настройки» — пропустить их можно без потери данных.
          </p>
        </section>
      )}
    </div>
  );
}
