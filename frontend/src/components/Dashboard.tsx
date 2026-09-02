import { useCallback, useEffect, useState } from "react";
import { listUsers, logout, ApiError } from "../api";
import { ROLE_LABELS, type CurrentUser, type User } from "../types";

interface DashboardProps {
  current: CurrentUser;
  /** Called after a successful logout. */
  onLoggedOut: () => void;
}

/** Post-login view: identity, role and an admin-only user list. */
export default function Dashboard({ current, onLoggedOut }: DashboardProps) {
  const { user } = current;
  const isAdmin = user.role === "admin";
  const [users, setUsers] = useState<User[] | null>(null);
  const [usersError, setUsersError] = useState<string | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);

  const loadUsers = useCallback(async () => {
    try {
      const page = await listUsers();
      setUsers(page.items);
    } catch (caught) {
      setUsersError(caught instanceof ApiError ? caught.message : "Не удалось загрузить список.");
    }
  }, []);

  useEffect(() => {
    if (isAdmin) {
      void loadUsers();
    }
  }, [isAdmin, loadUsers]);

  const handleLogout = async () => {
    setLoggingOut(true);
    try {
      await logout();
    } finally {
      setLoggingOut(false);
      onLoggedOut();
    }
  };

  return (
    <section className="panel">
      <div className="dashboard-head">
        <div>
          <h2 className="panel-title">Вы вошли</h2>
          <p className="panel-subtitle">Сессия активна и защищена.</p>
        </div>
        <button
          type="button"
          className="secondary-button"
          onClick={() => void handleLogout()}
          disabled={loggingOut}
        >
          {loggingOut ? "Выходим…" : "Выйти"}
        </button>
      </div>

      <dl className="detail-list">
        <div className="detail-row">
          <dt>Пользователь</dt>
          <dd>{user.username}</dd>
        </div>
        <div className="detail-row">
          <dt>ФИО</dt>
          <dd>{user.full_name || "—"}</dd>
        </div>
        <div className="detail-row">
          <dt>Роль</dt>
          <dd>
            <span className="badge">{ROLE_LABELS[user.role]}</span>
          </dd>
        </div>
      </dl>

      {isAdmin && (
        <div className="admin-section">
          <h3 className="section-title">Пользователи системы</h3>
          {usersError && (
            <p className="form-error" role="alert">
              {usersError}
            </p>
          )}
          {users === null && !usersError && <p className="muted">Загрузка…</p>}
          {users !== null && (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Имя пользователя</th>
                  <th>ФИО</th>
                  <th>Роль</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {users.map((item) => (
                  <tr key={item.id}>
                    <td>{item.username}</td>
                    <td>{item.full_name || "—"}</td>
                    <td>{ROLE_LABELS[item.role]}</td>
                    <td>{item.is_active ? "активен" : "отключён"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {!isAdmin && (
        <p className="muted">
          Раздел управления пользователями доступен только администратору. Бизнес-функции
          (кандидаты, очередь) появятся на следующих этапах.
        </p>
      )}
    </section>
  );
}
