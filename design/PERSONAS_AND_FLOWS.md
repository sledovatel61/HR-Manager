# Персоны и пользовательские сценарии

**Дата:** 2026-09-02
**Основано на:** PRODUCT_SPEC.md, agents.md, ROADMAP.md + дизайн-решения Signal Desk

Легенда: **[ТЗ]** = из документации, **[D]** = дизайн-предложение.

---

## Персона 1 — Анна, HR-менеджер

| | |
|---|---|
| Роль | HR **[ТЗ]** |
| Опыт | 2–5 лет, 30–80 активных кандидатов |
| Устройства | Ноутбук 1440×900, иногда 1280 |
| Цели | Закрывать вакансии; не терять касания; вести свою очередь |
| Частые действия | Открыть очередь → позвонить/отметить → сменить статус → запланировать интервью → передать при необходимости |
| Боли | Дубли, потеря контекста при переключении, медленный поиск, неочевидный owner |
| Нужная информация | Статус, последний контакт, следующий шаг, телефон, вакансия, источник |
| Плотность | Высокая / compact **[D]** |
| Ошибки, которые UI должен предотвращать | Случайная передача без confirm; смена статуса без фиксации; работа не со своим кандидатом без явного take-in; потеря несохранённого комментария |

### Ключевые сценарии Анны
1. Утренний разбор «Моя очередь»
2. Поиск кандидата по ФИО/телефону
3. Добавление взаимодействия после звонка
4. Планирование собеседования
5. Передача кандидату коллеге при отпуске

---

## Персона 2 — Игорь, руководитель направления подбора

| | |
|---|---|
| Роль | Руководитель **[ТЗ]** |
| Опыт | 7+ лет, управляет 4–8 HR |
| Цели | Контроль воронки, балансировка нагрузки, качество найма |
| Частые действия | Аналитика за период → drill-down по HR → переназначение → календарь команды |
| Боли | «Красивые» дашборды без action; разные определения метрик; нет owner clarity |
| Нужная информация | KPI, конверсии, SLA касаний, перегруженные HR, stuck candidates |
| Плотность | Средняя на analytics, высокая на списках |
| Ошибки UI | Неверная интерпретация KPI без определения; массовая передача без audit trail |

---

## Персона 3 — Марина, администратор системы

| | |
|---|---|
| Роль | Администратор **[ТЗ]** |
| Цели | Пользователи, роли, audit, справочники, безопасность |
| Частые действия | Создать пользователя → выдать роль → проверить audit → разблокировать |
| Боли | Слабые пароли, нет следа «кто изменил роль», неочевидный session revoke |
| Нужная информация | Статус учётки, last login, role, lock state, audit filters |
| Плотность | Формы + таблицы, medium |
| Ошибки UI | Создание user без пароля; повышение прав без confirm; скрытие audit |

---

## User flows (минимум 18)

### F01. Вход в систему **[ТЗ]**
1. Пользователь открывает `/login`.
2. Вводит логин и пароль (password input с show/hide).
3. При ошибке — inline message, focus на поле; при rate-limit — блокировка с таймером.
4. Успех → home по роли (HR → Моя очередь, руководитель → Главная/Аналитика, admin → может Users).
5. Loading skeleton session restore.

### F02. Личная очередь
1. Nav «Моя очередь» или `G` then `Q` **[D]**.
2. Список кандидатов `owner = me`, default sort: next action / updated.
3. Быстрые фильтры статусов chips.
4. Click row → preview drawer или full card.

### F03. Поиск кандидата
1. Focus search top / `⌘K` / `/`.
2. Type ≥2 chars → results: candidates, actions, pages.
3. Enter → open candidate; arrows navigate.

### F04. Фильтрация и saved view **[D]**
1. Open Filters panel.
2. Status multi, source, owner (manager+), date range, vacancy.
3. Apply → URL/query state mock.
4. «Сохранить представление» → name → appears in Saved views dropdown.

### F05. Карточка кандидата
1. From table/kanban/search.
2. Header identity + status + owner + CTA.
3. Tabs: Timeline (default), Данные, События, Документы.
4. Back preserves list scroll/filters.

### F06. Добавление взаимодействия
1. CTA «Добавить взаимодействие» or `A`.
2. Drawer/modal: type (call/email/note/meeting), outcome, comment, datetime.
3. Save → timeline prepend + toast; Escape cancels with confirm if dirty.

### F07. Планирование собеседования
1. From card or calendar «+».
2. Form: datetime, type, participants, location/link, reminder.
3. Creates event + timeline item + optional status bump suggestion.

### F08. Изменение статуса
1. Status chip → menu or command «Статус: …».
2. Select new status; optional reason for terminal (отказ/уволен).
3. Timeline entry + toast. Kanban card moves if board open.

### F09. Передача кандидата **[ТЗ]**
1. Action «Передать».
2. Dialog: select HR, required reason, summary of candidate.
3. Confirm primary button disabled until reason ≥3 chars.
4. On success: owner changes, audit event, toast; previous owner loses edit if policy says so.

### F10. Kanban
1. Toggle Table | Kanban (segmented) — filters shared.
2. Columns = funnel stages; counts; cards with name, vacancy, owner, next event.
3. Move via menu «Переместить в…» (keyboard) or drag (mouse). Drag respects reduced-motion (instant snap).

### F11. Календарь
1. Week default; day/month toggle.
2. Events color by type; click → detail; «Сегодня».
3. Filter my / team (manager).

### F12. Аналитика руководителя
1. Period preset + custom.
2. KPI strip with definitions tooltip.
3. Funnel + conversion; table by HR; source breakdown.
4. Export stub button (mock toast «Подготовка отчёта»).

### F13. Создание пользователя (admin)
1. Users → «Создать».
2. Form: name, username, email example.com, role, temporary password required **[ТЗ]**.
3. Validation; success toast; row appears.

### F14. Изменение роли
1. User row → Edit role.
2. Confirm dialog explaining impact.
3. Audit event mock.

### F15. Audit log
1. Filters: actor, action, entity, date.
2. Table immutable rows; expand detail JSON-like readable.
3. No edit/delete actions.

### F16. Истечение / отзыв сессии **[ТЗ]**
1. Mock trigger «Симулировать истечение».
2. Full-screen blocking state: «Сессия истекла», CTA Войти.
3. Focus trap; no background interaction.

### F17. Недостаточно прав
1. HR opens `/users` → Permission denied state with explanation + go home.
2. Not a blank error.

### F18. Сеть / backend unavailable
1. Banner degraded + retry.
2. Inline error state on page; cached mock still readable in prototype demo mode.

---

## Cross-cutting rules **[D]**

- Escape closes topmost overlay; focus returns to invoker.
- Destructive actions always confirm.
- Toasts via `aria-live="polite"`; errors `assertive` when blocking.
- All flows have mouse and keyboard path.
- Russian microcopy, clear verbs («Передать», не «OK»).
