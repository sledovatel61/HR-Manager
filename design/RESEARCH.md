# UX/UI Research — HR Manager

**Дата исследования:** 2026-09-02
**Продукт:** HR Manager — внутренняя ATS/CRM для командного подбора
**Контекст:** desktop-first, русский язык, роли HR / руководитель / администратор

## 1. Источники документации продукта (факты)

| Источник | Что взято |
|---|---|
| `PRODUCT_SPEC.md` | Роли, карточка кандидата, воронка статусов, аналитика, soft delete, передача с аудитом |
| `ROADMAP.md` | Последовательность этапов: security → candidates → HR UI → calendar → analytics → templates |
| `agents.md` | Правила owner_user_id, серверная проверка прав, audit, desktop UX |
| `docs/ARCHITECTURE.md` | React 18 + TS + Vite, same-origin proxy, security headers |
| `prompts/PHASE_2_PROMPT.md` | Login/logout, roles, audit, session expiry — контракт для UI-состояний безопасности |
| Ветка `arena/01a061ab-hr-manager` (fetch) | LoginForm + Dashboard shell: loading → anonymous → authenticated; роли HR/manager/admin |

**Факт:** production frontend сейчас — статусная страница / минимальный auth shell. Дизайн-прототип не должен подключаться к API.

## 2. Внешние источники

### 2.1 Nielsen Norman Group — Data Tables

- **URL:** https://www.nngroup.com/articles/data-tables/
- **Дата просмотра:** 2026-09-02
- **Паттерны:** 4 задачи таблицы (find / compare / view-edit / act); sticky headers; side panel вместо modal для edit; явные фильтры; freeze primary column
- **Адаптация:** таблица кандидатов с sticky ФИО, filter chips, drawer preview без потери списка; primary actions видимы, secondary в ⋮

### 2.2 Nielsen Norman Group — Progressive disclosure / dashboards

- **URL:** https://thedan.design/insights/dashboard-design-principles-best-practices-to-enhance-your-data-analysis/ (ссылается на NN/G)
- **Дата:** 2026-09-02
- **Паттерны:** progressive disclosure снижает cognitive load; не color-only статусы; WCAG 2.2 AA
- **Адаптация:** KPI руководителя — 4–6 ключевых метрик + drill-down; воронка как одна визуализация, не 12 одинаковых плиток

### 2.3 Eleken — ATS design practices

- **URL:** https://www.eleken.co/blog-posts/applicant-tracking-system-design-how-to-make-recruitment-better-for-everyone
- **Дата:** 2026-09-02
- **Паттерны:** Kanban pipeline как визуализация этапов; candidate profile = single source of truth; list ↔ board toggle
- **Адаптация:** единый Candidate entity view; переключение Table/Kanban сохраняет фильтры

### 2.4 Linear — keyboard-first & command menu

- **URL:** https://www.morgen.so/blog-posts/linear-project-management; https://fastshortcuts.com/shortcuts/linear/
- **Дата:** 2026-09-02
- **Паттерны:** ⌘/Ctrl+K command palette; G+letter navigation; single-key actions; `?` shortcuts help
- **Адаптация:** palette для поиска кандидатов + команд («передать», «сменить статус»); shortcuts discoverable через UI, не только клавиатуру

### 2.5 SaaS dashboard trends (Linear, Attio, Stripe)

- **URL:** https://adminlte.io/blog/saas-dashboard-design-examples/
- **Дата:** 2026-09-02
- **Паттерны:** quiet chrome; tables as primary interface (Stripe); color = meaning sparingly (Vercel); density for power tools
- **Адаптация:** спокойный chrome HR Manager; цвет только для статусов/алертов; dense table mode

### 2.6 ATS comparisons (Ashby, Greenhouse, Lever)

- **URL:** https://clonepartner.com/blog/greenhouse-vs-ashby-2026-the-ctos-technical-ats-comparison/
- **Дата:** 2026-09-02
- **Идеи:** Ashby — modern UX + native analytics; Greenhouse — structured process; Lever — CRM nurture
- **Адаптация:** гибрид — скорость Ashby-like UI + structured ownership/audit как enterprise requirement; без копирования UI

### 2.7 Carbon Design System (IBM)

- **URL:** https://www.carbondesignsystem.com/ (через обзоры tokens)
- **Дата:** 2026-09-02
- **Паттерны:** semantic UI layers (ui-01…ui-05); 4px base spacing; restrained radius; strong focus states
- **Адаптация:** layered surfaces, 4px spacing scale, focus ring 2px brand

### 2.8 Atlassian Design System

- **URL:** https://www.designsystems.one/design-systems/atlassian-design
- **Дата:** 2026-09-02
- **Паттерны:** surface/sunken tokens; brand bold blue; semantic danger/success/warning
- **Адаптация:** token naming `--surface`, `--surface-sunken`, `--text-danger`

### 2.9 WAI-ARIA / WCAG

- **URL:** https://www.w3.org/WAI/ARIA/apg/; https://www.w3.org/WAI/WCAG22/quickref/
- **Дата:** 2026-09-02
- **Паттерны:** dialog focus trap; combobox; grid keyboard; live regions; target size 24×24 (2.5.8)
- **Адаптация:** modal/command palette по APG; aria-live для toast; status не только цветом

### 2.10 Enterprise data table guidelines

- **URL:** https://medium.com/@calee607/data-table-design-guidelines-for-enterprise-applications-40f7ef0e0186
- **Дата:** 2026-09-02
- **Паттерны:** row density 40–48px compact / 48–56 standard; zebra optional; column min widths; bulk actions
- **Адаптация:** density switch (comfortable / compact); row hover highlight вместо агрессивного zebra

### 2.11 Bullhorn Kanban ATS

- **URL:** https://kb.bullhorn.com/bh4sf/Content/BH4SF/Topics/ATSV1CandidateKanbanBoard.htm
- **Дата:** 2026-09-02
- **Паттерны:** columns = stages + counts; card quick actions; multi-select move
- **Адаптация:** колонки = статусы воронки из PRODUCT_SPEC; count badges; keyboard move как fallback к drag

## 3. Выводы для HR Manager

1. **Таблица — primary surface** для ежедневной работы HR; Kanban — operational overview.
2. **Карточка кандидата** открывается как full page или split preview (drawer), timeline — центр.
3. **Command palette** — must-have для power users, с mouse-parity.
4. **Фильтры + saved views** важнее «красивых» dashboard tiles.
5. **Статусы** — chip + text + optional icon (не color-only).
6. **Роли** меняют default home и видимость nav, не «ломают» layout.
7. **Security states** (session expired, 403, locked) — first-class screens.
8. **Density** и quiet motion важнее decorative glass/neon.

## 4. Что не копировать

- Bootstrap/AdminLTE «карточка на каждую метрику»
- Неоновые градиенты и glassmorphism ради эффекта
- Landing-page hero на рабочих экранах
- Hover-only actions без keyboard/focus equivalent
- Drag-only Kanban без альтернативы
- Реальные логотипы Ashby/Linear/Greenhouse
- Случайные аватары с внешних CDN

## 5. Разделение: факт / референс / решение

| Тип | Примеры |
|---|---|
| **Факт (ТЗ)** | Роли, статусы воронки, owner, soft delete, audit, desktop-first RU |
| **Референс** | Cmd+K (Linear), side panel table edit (NN/G), pipeline Kanban (ATS) |
| **Собственное решение** | Концепция «Signal Desk»; indigo-teal accent; density switch; transfer confirm pattern; hybrid IA |

## 6. Ограничения исследования

- Закрытые UI Ashby/Greenhouse изучены по публичным обзорам, не по внутренним design kits.
- Браузерный automated a11y-audit прототипа — см. `ACCESSIBILITY.md`.
- Визуальные moodboards не скачивались; палитры синтезированы.
