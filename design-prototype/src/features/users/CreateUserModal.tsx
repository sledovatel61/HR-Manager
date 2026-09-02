import { useState } from "react";
import { Modal } from "../../components/ui/Modal";
import { Button } from "../../components/ui/Button";
import { Field, SelectInput, TextInput } from "../../components/ui/Field";
import { useAppState } from "../../state/AppState";
import type { UserRole } from "../../types";

/**
 * Создание пользователя администратором. Пароль обязателен (см.
 * agents.md → "обязательный пароль при создании пользователя"); прототип
 * не отправляет данные никуда, но воспроизводит серверные требования к
 * паролю как client-side валидацию + понятную подсказку.
 */
export function CreateUserModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { pushToast } = useAppState();
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<UserRole>("hr");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit() {
    if (!fullName.trim() || !username.trim() || !email.trim()) {
      setError("Заполните все обязательные поля.");
      return;
    }
    if (password.length < 12) {
      setError("Пароль должен быть не короче 12 символов (требование безопасности).");
      return;
    }
    setError(null);
    pushToast("success", `Пользователь «${fullName}» создан (мок, без записи на сервер).`);
    setFullName("");
    setUsername("");
    setEmail("");
    setPassword("");
    setRole("hr");
    onClose();
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Создать пользователя"
      description="Пароль обязателен и должен соответствовать требованиям безопасности."
      size="sm"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Отмена</Button>
          <Button variant="primary" onClick={handleSubmit}>Создать</Button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
        <Field label="Полное имя" required error={error && !fullName.trim() ? error : undefined}>
          {(id) => <TextInput id={id} value={fullName} onChange={(e) => setFullName(e.target.value)} required />}
        </Field>
        <Field label="Имя пользователя" required>
          {(id) => <TextInput id={id} value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="username" />}
        </Field>
        <Field label="Email" required>
          {(id) => <TextInput id={id} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="name@example.com" />}
        </Field>
        <Field label="Роль" required>
          {(id) => (
            <SelectInput id={id} value={role} onChange={(e) => setRole(e.target.value as UserRole)}>
              <option value="hr">HR</option>
              <option value="manager">Руководитель</option>
              <option value="admin">Администратор</option>
            </SelectInput>
          )}
        </Field>
        <Field
          label="Временный пароль"
          required
          hint="Минимум 12 символов. Пользователь сменит пароль при первом входе."
          error={error && password.length < 12 ? error : undefined}
        >
          {(id, describedBy) => (
            <TextInput
              id={id}
              aria-describedby={describedBy}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
              invalid={Boolean(error && password.length < 12)}
            />
          )}
        </Field>
      </div>
    </Modal>
  );
}
