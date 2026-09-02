# HR Manager — Design Prototype (Signal Desk)

Изолированный интерактивный UX/UI-прототип. **Не** связан с `frontend/` и backend.

- Мок-данные только
- Без внешних runtime-запросов
- Без реальных персональных данных
- Email только `@example.com` / `@example.org` / `@example.net`

## Запуск

Требуется Node.js 20+ (в CI проекта — 22).

```bash
cd design-prototype
npm install
npm run dev
```

Откройте http://localhost:5174

Production-сборка:

```bash
npm run build
npm run preview
```

Проверки:

```bash
npm run typecheck
npm run lint
```

## Демо-учётки

| Логин | Пароль | Роль |
|---|---|---|
| `a.krylova` | `demo-hr` | HR |
| `i.saveliev` | `demo-mgr` | Руководитель |
| `m.orlova` | `demo-adm` | Администратор |

## Клавиши

- `Ctrl/⌘ K` — command palette
- `/` — поиск
- `G` затем `H/Q/C/K/A/U` — навигация
- `V` — table/kanban
- `?` — справка
- `Esc` — закрыть оверлей

## Экраны

Login, Главная, Очередь, Кандидаты (table/kanban), Карточка + timeline, Календарь, Аналитика, Пользователи, Создание пользователя, Audit, Command palette, Empty/Loading/Error/Forbidden/Session expired.

Демо-состояния: **Настройки → Демо-состояния**.

## Документация дизайна

См. каталог [`../design/`](../design/).
