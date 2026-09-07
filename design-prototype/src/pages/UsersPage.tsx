import { useState } from "react";
import { PageHeader } from "../components/ui/PageHeader";
import { Button, IconButton } from "../components/ui/Button";
import { Avatar } from "../components/ui/Avatar";
import { Badge } from "../components/ui/StatusChip";
import { PermissionDeniedState } from "../components/ui/StateViews";
import { CreateUserModal } from "../features/users/CreateUserModal";
import { useAppState } from "../state/AppState";
import { USERS, userById } from "../data/mockData";
import { ROLE_LABELS } from "../types";

export function UsersPage() {
  const { currentUserId } = useAppState();
  const currentUser = userById(currentUserId)!;
  const [createOpen, setCreateOpen] = useState(false);

  if (currentUser.role !== "admin") {
    return (
      <div>
        <PageHeader title="Пользователи" description="Управление учётными записями и ролями." />
        <PermissionDeniedState />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Пользователи"
        description="Учётные записи HR, руководителей и администраторов. Изменения ролей фиксируются в журнале аудита."
        actions={<Button variant="primary" icon="user-plus" onClick={() => setCreateOpen(true)}>Создать пользователя</Button>}
      />

      <div className="candidate-table-wrap">
        <table className="candidate-table">
          <caption className="sr-only">Список пользователей системы</caption>
          <thead>
            <tr>
              <th scope="col">Пользователь</th>
              <th scope="col">Логин</th>
              <th scope="col">Роль</th>
              <th scope="col">Статус</th>
              <th scope="col"><span className="sr-only">Действия</span></th>
            </tr>
          </thead>
          <tbody>
            {USERS.map((u) => (
              <tr key={u.id}>
                <td>
                  <div className="candidate-cell">
                    <Avatar initials={u.initials} color={u.avatarColor} size="sm" />
                    <div className="candidate-cell-text">
                      <span className="candidate-name">{u.fullName}</span>
                      <span className="candidate-meta">{u.email}</span>
                    </div>
                  </div>
                </td>
                <td className="muted-cell">{u.username}</td>
                <td><Badge tone={u.role === "admin" ? "violet" : u.role === "manager" ? "indigo" : "teal"}>{ROLE_LABELS[u.role]}</Badge></td>
                <td><Badge tone={u.isActive ? "success" : "neutral"}>{u.isActive ? "Активен" : "Отключён"}</Badge></td>
                <td>
                  <div className="row-actions">
                    <IconButton icon="edit" label={`Изменить роль ${u.fullName}`} size="sm" />
                    <IconButton icon="lock" label={`Заблокировать ${u.fullName}`} size="sm" />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <CreateUserModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}
