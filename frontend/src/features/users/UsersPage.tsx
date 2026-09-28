/**
 * Admin: управление учётными записями (список, создание, редактирование,
 * отключение, сброс пароля, снятие блокировки).
 *
 * Доступ: раздел строго для роли admin. Навигация показывает пункт только
 * администратору, и сама страница повторно проверяет роль — при прямом
 * переходе по #/users не-admin видит штатное состояние «Недостаточно прав».
 * Настоящая граница безопасности — backend (require_roles на каждом
 * /admin/users-эндпоинте): UI-проверка нужна только для понятного UX.
 *
 * Пароли: вводятся только в type="password" полях, никогда не отображаются
 * и не хранятся в состоянии после успешного запроса. Backend в ответах
 * отдаёт User без каких-либо парольных полей (см. app/schemas.py UserOut).
 */

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createUser,
  listUsers,
  unlockUser,
  updateUser,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { ConfirmDialog } from "../../design-system/components/ConfirmDialog";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import {
  ErrorState,
  PermissionDeniedState,
  SkeletonRows,
} from "../../design-system/components/StateViews";
import { useToast } from "../../design-system/components/ToastContext";
import { ROLE_LABELS, type User, type UserRole, type UserUpdateInput } from "../../types";
import "./users.css";

const ROLE_OPTIONS: UserRole[] = ["hr", "manager", "admin"];

const PASSWORD_HINT =
  "Не менее 12 символов, минимум одна буква и одна цифра; не совпадайте с именем пользователя.";

const EMPTY_CREATE = { username: "", full_name: "", role: "hr" as UserRole, password: "" };

interface EditForm {
  full_name: string;
  role: UserRole;
  is_active: boolean;
  password: string;
}

function emptyEdit(user: User): EditForm {
  return { full_name: user.full_name, role: user.role, is_active: user.is_active, password: "" };
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU");
}

/** Блокировка активна, пока locked_until в будущем. */
function lockActive(user: User): boolean {
  return user.locked_until !== null && new Date(user.locked_until).getTime() > Date.now();
}

/**
 * Человекочитаемое сообщение об ошибке на русском. Backend присылает
 * готовые русские detail-сообщения; pydantic-валидация приходит массивом —
 * извлекаем текст первого нарушения («Value error, …» — префикс pydantic).
 */
function describeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    if (error.status === 403) {
      return "Недостаточно прав: управление пользователями доступно только администраторам.";
    }
    const raw = error.rawDetail;
    if (
      Array.isArray(raw) &&
      raw.length > 0 &&
      typeof raw[0] === "object" &&
      raw[0] !== null &&
      "msg" in raw[0]
    ) {
      const message = String((raw[0] as { msg: unknown }).msg)
        .replace(/^Value error,\s*/, "")
        .trim();
      if (message) return message;
    }
    const detail = error.message?.trim();
    if (detail) return detail;
  }
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}

export function UsersPage({ currentUser }: { currentUser: User }) {
  const { pushToast } = useToast();
  const isAdmin = currentUser.role === "admin";

  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(isAdmin);
  const [loadError, setLoadError] = useState(false);
  const [forbidden, setForbidden] = useState(false);

  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState(EMPTY_CREATE);
  const [saving, setSaving] = useState(false);

  const [editTarget, setEditTarget] = useState<User | null>(null);
  const [editForm, setEditForm] = useState<EditForm | null>(null);
  const [confirmDeactivate, setConfirmDeactivate] = useState(false);
  const [confirmPasswordReset, setConfirmPasswordReset] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    setForbidden(false);
    try {
      const page = await listUsers();
      setUsers(page.items);
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) {
        setForbidden(true);
      } else {
        setLoadError(true);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isAdmin) void load();
  }, [isAdmin, load]);

  const createUserAccount = useCallback(async () => {
    const username = createForm.username.trim();
    if (!username || createForm.password.length === 0) {
      pushToast("info", "Укажите имя пользователя и пароль.");
      return;
    }
    setSaving(true);
    try {
      const created = await createUser({
        username,
        full_name: createForm.full_name.trim(),
        role: createForm.role,
        password: createForm.password,
      });
      // Пароль одноразовый: после успешного ответа из состояния удаляем.
      setCreateForm(EMPTY_CREATE);
      setCreateOpen(false);
      pushToast("success", `Пользователь ${created.username} создан.`);
      await load();
    } catch (error) {
      pushToast("danger", describeError(error, "Не удалось создать пользователя."));
    } finally {
      setSaving(false);
    }
  }, [createForm, load, pushToast]);

  const openEdit = useCallback((user: User) => {
    setEditTarget(user);
    setEditForm(emptyEdit(user));
    setConfirmDeactivate(false);
    setConfirmPasswordReset(false);
  }, []);

  const closeEdit = useCallback(() => {
    setEditTarget(null);
    setEditForm(null);
    setConfirmDeactivate(false);
    setConfirmPasswordReset(false);
  }, []);

  const performEditSave = useCallback(async () => {
    if (!editTarget || !editForm) return;
    const input: UserUpdateInput = {};
    if (editForm.full_name.trim() !== editTarget.full_name) {
      input.full_name = editForm.full_name.trim();
    }
    if (editForm.role !== editTarget.role) input.role = editForm.role;
    if (editForm.is_active !== editTarget.is_active) input.is_active = editForm.is_active;
    const password = editForm.password;
    if (password.length > 0) input.password = password;

    if (Object.keys(input).length === 0) {
      closeEdit();
      return;
    }
    setSaving(true);
    try {
      const updated = await updateUser(editTarget.id, input);
      setUsers((current) => current.map((user) => (user.id === updated.id ? updated : user)));
      // Пароль не храним: сбрасываем поле сразу после успешного запроса.
      setEditForm((current) => (current ? { ...current, password: "" } : current));
      closeEdit();
      if (input.password !== undefined) {
        pushToast(
          "success",
          `Пароль для ${updated.username} сброшен. Передайте его пользователю лично — пароль нигде не показывается.`,
        );
      } else {
        pushToast("success", `Изменения для ${updated.username} сохранены.`);
      }
    } catch (error) {
      pushToast("danger", describeError(error, "Не удалось сохранить изменения."));
    } finally {
      setSaving(false);
    }
  }, [closeEdit, editForm, editTarget, pushToast]);

  /** Сабмит редактирования: опасные изменения проводим через подтверждения. */
  const beginEditSave = useCallback(() => {
    if (!editTarget || !editForm) return;
    const deactivating = editTarget.is_active && !editForm.is_active;
    const resetsPassword = editForm.password.length > 0;
    if (deactivating) {
      setConfirmDeactivate(true);
      return;
    }
    if (resetsPassword) {
      setConfirmPasswordReset(true);
      return;
    }
    void performEditSave();
  }, [editForm, editTarget, performEditSave]);

  const confirmDeactivateAndContinue = useCallback(() => {
    setConfirmDeactivate(false);
    if (editForm && editForm.password.length > 0) {
      setConfirmPasswordReset(true);
      return;
    }
    void performEditSave();
  }, [editForm, performEditSave]);

  const confirmPasswordAndContinue = useCallback(() => {
    setConfirmPasswordReset(false);
    void performEditSave();
  }, [performEditSave]);

  const performUnlock = useCallback(
    async (user: User) => {
      try {
        const updated = await unlockUser(user.id);
        setUsers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
        pushToast("success", `Блокировка учётной записи ${updated.username} снята.`);
      } catch (error) {
        pushToast("danger", describeError(error, "Не удалось снять блокировку."));
      }
    },
    [pushToast],
  );

  if (!isAdmin || forbidden) {
    return <PermissionDeniedState />;
  }
  if (loading) {
    return <SkeletonRows rows={5} columns={5} />;
  }
  if (loadError) {
    return <ErrorState onRetry={() => void load()} />;
  }

  const isSelf = editTarget?.id === currentUser.id;
  const deactivatingSelf = editForm !== null && isSelf && !editForm.is_active;

  return (
    <div className="users-page">
      <div className="users-card">
        <div className="users-head">
          <div>
            <h3 className="users-title">Учётные записи</h3>
            <p className="users-subtitle">
              Всего пользователей: {users.length}. Все изменения фиксируются в журнале аудита.
            </p>
          </div>
          <Button variant="primary" icon="user-plus" onClick={() => setCreateOpen(true)}>
            Добавить пользователя
          </Button>
        </div>

        <div className="table-wrap">
          <table className="users-table" aria-label="Список пользователей">
            <thead>
              <tr>
                <th scope="col">Пользователь</th>
                <th scope="col">Роль</th>
                <th scope="col">Статус</th>
                <th scope="col">Создан</th>
                <th scope="col">Последний вход</th>
                <th scope="col">Действия</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const locked = lockActive(user);
                return (
                  <tr key={user.id}>
                    <td>
                      <div className="users-fullname">{user.full_name || "—"}</div>
                      <div className="users-username">{user.username}</div>
                    </td>
                    <td>{ROLE_LABELS[user.role]}</td>
                    <td>
                      <span className={`users-pill ${user.is_active && !locked ? "ok" : locked ? "bad" : "neutral"}`}>
                        {locked
                          ? `Заблокирована до ${formatDateTime(user.locked_until)}`
                          : user.is_active
                            ? "Активна"
                            : "Отключена"}
                      </span>
                    </td>
                    <td>{formatDateTime(user.created_at)}</td>
                    <td>{user.last_login_at ? formatDateTime(user.last_login_at) : "никогда"}</td>
                    <td>
                      <div className="users-actions">
                        <Button variant="secondary" size="sm" icon="edit" onClick={() => openEdit(user)}>
                          Редактировать
                        </Button>
                        {user.locked_until !== null && (
                          <Button
                            variant="ghost"
                            size="sm"
                            icon="undo"
                            onClick={() => void performUnlock(user)}
                          >
                            Разблокировать
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <Modal
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          // Гигиена: не держим введённый пароль в состоянии после закрытия.
          setCreateForm(EMPTY_CREATE);
        }}
        title="Добавить пользователя"
        description="Пароль обязателен и должен соответствовать политике безопасности."
      >
        <form
          className="users-form"
          onSubmit={(event) => {
            event.preventDefault();
            void createUserAccount();
          }}
          aria-label="Форма создания пользователя"
        >
          <Field label="Имя пользователя" required hint="Только латинские буквы, цифры, точка, дефис и подчёркивание.">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                autoComplete="off"
                value={createForm.username}
                onChange={(event) => setCreateForm({ ...createForm, username: event.target.value })}
              />
            )}
          </Field>
          <Field label="ФИО">
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                autoComplete="off"
                value={createForm.full_name}
                onChange={(event) => setCreateForm({ ...createForm, full_name: event.target.value })}
              />
            )}
          </Field>
          <Field label="Роль" required>
            {(id, describedBy) => (
              <SelectInput
                id={id}
                aria-describedby={describedBy}
                value={createForm.role}
                onChange={(event) => setCreateForm({ ...createForm, role: event.target.value as UserRole })}
              >
                {ROLE_OPTIONS.map((role) => (
                  <option key={role} value={role}>
                    {ROLE_LABELS[role]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Пароль" required hint={PASSWORD_HINT}>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                type="password"
                autoComplete="new-password"
                value={createForm.password}
                onChange={(event) => setCreateForm({ ...createForm, password: event.target.value })}
              />
            )}
          </Field>
          <div className="users-form-footer">
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setCreateOpen(false);
                setCreateForm(EMPTY_CREATE);
              }}
            >
              Отмена
            </Button>
            <Button type="submit" variant="primary" loading={saving}>
              Создать пользователя
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={editTarget !== null && editForm !== null}
        onClose={closeEdit}
        title={editTarget ? `Редактирование: ${editTarget.username}` : "Редактирование"}
        description="Оставьте поле пароля пустым, чтобы не менять пароль."
      >
        {editTarget && editForm && (
          <form
            className="users-form"
            onSubmit={(event) => {
              event.preventDefault();
              beginEditSave();
            }}
            aria-label="Форма редактирования пользователя"
          >
            <Field label="ФИО">
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  autoComplete="off"
                  value={editForm.full_name}
                  onChange={(event) => setEditForm({ ...editForm, full_name: event.target.value })}
                />
              )}
            </Field>
            <Field
              label="Роль"
              required
              hint={
                isSelf
                  ? "Нельзя изменить собственную роль."
                  : undefined
              }
            >
              {(id, describedBy) => (
                <SelectInput
                  id={id}
                  aria-describedby={describedBy}
                  value={editForm.role}
                  disabled={isSelf}
                  onChange={(event) => setEditForm({ ...editForm, role: event.target.value as UserRole })}
                >
                  {ROLE_OPTIONS.map((role) => (
                    <option key={role} value={role}>
                      {ROLE_LABELS[role]}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
            <Field
              label="Учётная запись активна"
              hint={
                isSelf
                  ? "Нельзя отключить собственную учётную запись."
                  : "Отключённый пользователь не сможет войти в систему."
              }
            >
              {(id, describedBy) => (
                <input
                  id={id}
                  aria-describedby={describedBy}
                  type="checkbox"
                  className="users-checkbox"
                  checked={editForm.is_active}
                  disabled={isSelf}
                  onChange={(event) => setEditForm({ ...editForm, is_active: event.target.checked })}
                />
              )}
            </Field>
            <Field label="Новый пароль (сброс пароля)" hint={PASSWORD_HINT}>
              {(id, describedBy) => (
                <TextInput
                  id={id}
                  aria-describedby={describedBy}
                  type="password"
                  autoComplete="new-password"
                  value={editForm.password}
                  onChange={(event) => setEditForm({ ...editForm, password: event.target.value })}
                />
              )}
            </Field>
            {deactivatingSelf && (
              <p className="users-warning" role="note">
                Отключение собственной учётной записи запрещено.
              </p>
            )}
            <div className="users-form-footer">
              <Button type="button" variant="secondary" onClick={closeEdit}>
                Отмена
              </Button>
              <Button type="submit" variant="primary" loading={saving}>
                Сохранить
              </Button>
            </div>
          </form>
        )}
      </Modal>

      <ConfirmDialog
        open={confirmDeactivate}
        onCancel={() => setConfirmDeactivate(false)}
        onConfirm={confirmDeactivateAndContinue}
        danger
        title="Отключить учётную запись?"
        description={`Пользователь ${editTarget?.username ?? ""} не сможет войти в систему, пока вы снова не включите учётную запись. Подтвердите отключение.`}
        confirmLabel="Отключить"
      />
      <ConfirmDialog
        open={confirmPasswordReset}
        onCancel={() => setConfirmPasswordReset(false)}
        onConfirm={confirmPasswordAndContinue}
        title="Сбросить пароль?"
        description={`Текущий пароль пользователя ${editTarget?.username ?? ""} перестанет действовать. Новый пароль нигде не показывается — передайте его пользователю по защищённому каналу.`}
        confirmLabel="Сбросить пароль"
      />
    </div>
  );
}
