# Промпт новому агенту: отдельный PR по parity с Bento Arctic

Ты работаешь в `HR-Manager-merged-main`. Подготовь отдельный PR, который доводит уже существующее React-приложение до уровня удобства, визуальной иерархии и интерактивности mockup на совпадающих экранах. Это не перенос статического HTML и не переписывание продукта с нуля.

## Жёсткие ограничения

1. Не удаляй, не переименовывай и не перестраивай существующие пункты левой панели. Sidebar уже является продуктовой навигацией и не обязан совпадать с mockup.
2. Не меняй role visibility в `frontend/src/app-shell/workspaceSections.ts`. Не добавляй администратору «Моя очередь» без отдельного продуктового решения.
3. Не используй demo data/mockup arrays в production. Все данные и mutations остаются API-backed.
4. Не добавляй библиотеки без необходимости. Используемый стек: React 18, TypeScript strict, Vite, Vitest, Testing Library.
5. Сохрани backend authorization, русский UI, accessibility, keyboard navigation, focus states, responsive layout и `prefers-reduced-motion`.
6. Не ломай существующие pagination, loading/error/empty states, duplicate flow, permissions, audit и API contracts.
7. Делай PR поэтапно и не выполняй большой неразделимый rewrite.

Reference mockup:
`C:\Users\User\Documents\HR\HR Manager Desktop\HR-Manager-merged-main\design\mockups\bento-arctic.html`

## Scope PR

Работай только над совпадающими разделами и общими механизмами:

- «Кандидаты» — table, toolbar, filters, row actions, drawer/modal;
- «Воронка кандидатов» — Kanban, карточки, drag/drop;
- «Шаблоны и материалы» / библиотека — карточки, поиск, категории, detail;
- «Настройки» — appearance controls;
- global theme state и, только если архитектура позволяет, общий поиск/command palette.

Не мигрируй в этом PR «Мою очередь», календарь, аналитику, уведомления и прочие непохожие экраны.

## Сначала изучи существующий код

Прочитай и тесты рядом с этими файлами:

- `frontend/src/app-shell/Workspace.tsx`
- `frontend/src/app-shell/workspaceSections.ts`
- `frontend/src/app-shell/SettingsHub.tsx`
- `frontend/src/app-shell/settingsGroups.ts`
- `frontend/src/app-shell/workspace.css`
- `frontend/src/features/candidates/CandidatesListPage.tsx`
- `frontend/src/features/candidates/CandidateDrawer.tsx`
- `frontend/src/features/candidates/CandidateFormModal.tsx`
- `frontend/src/features/candidates/useCandidatesList.ts`
- `frontend/src/features/candidates/candidates.css`
- `frontend/src/features/candidates/KanbanPage.tsx`
- `frontend/src/features/candidates/kanban.css`
- `frontend/src/features/library/LibraryPage.tsx`
- `frontend/src/features/library/library.css`
- `frontend/src/api.ts`, `frontend/src/types.ts`
- `frontend/src/design-system/tokens.css` и design-system components.

Сверь production с функциями/участками mockup `screenCandidates`, `screenKanban`, `screenSettings`, drawer кандидата, `applyState`, `toggleTheme`, `set-theme`, `data-theme`, `data-density`, command palette и `bindDrag`.

## Целевое поведение

### Кандидаты

- Page header с контекстом, заголовком, вычисляемой из API статистикой и понятными primary/secondary actions.
- Одна toolbar-card: поиск по реальным доступным полям, фильтры вакансии/этапа/источника/ответственного, server-side query и debounce.
- Кликабельная строка с avatar/инициалами, двухстрочным именем, вторичным текстом, status chip и реальными метаданными.
- Клик открывает уже существующий `CandidateDrawer`; row actions имеют `aria-label` и не открывают drawer случайно.
- Сохрани документы, сообщения, редактирование, duplicate confirmation, error/loading/empty states.
- Не показывай mockup-числа вроде «16 кандидатов», если их нельзя вычислить из API.
- Добавь тесты фильтрации, drawer, event propagation и состояний.

### Воронка

- Оставь существующий `updateCandidate(..., { stage })`, серверную проверку и retry/error flow.
- Board/columns/cards визуально приблизить к mockup: header этапа и count, горизонтальный scroll, спокойный тональный фон, readable cards.
- Карточка показывает реальные имя, должность и метаданные; клик открывает тот же drawer.
- Drag/drop показывает drop state и busy state; при ошибке не теряет карточку и предлагает retry.
- Оставь доступную альтернативу drag/drop (select/button), keyboard/focus states и aria description.
- Не вводи новые этапы и не меняй `STAGE_ORDER`/backend vocabulary.

### Библиотека / шаблоны

- Используй существующий `LibraryPage` и `listLibraryMaterials`, а не mockup data.
- Сделай каталог ближе к mockup: header, search, category chips/counts, card grid, metadata и primary action.
- Сохрани detail, recent items, print, download, open in new window, manage mode и permission boundaries.
- Не вставляй произвольный HTML из mockup; существующий server-rendered `body_html` pipeline остаётся источником контента.
- Проверь mobile: одна колонка, toolbar/actions переносятся; добавь тесты search/category/detail/recent/error.

### Настройки и тема

Добавь в существующий `SettingsHub` заметную секцию visual settings, не ломая role-filtered settings groups:

- выбор «Светлая»/«Тёмная» в стиле mockup;
- используй существующие semantic tokens `[data-theme="dark"]` из `frontend/src/design-system/tokens.css`;
- состояние должно быть единым для shell, применяться к `document.documentElement` и сохраняться в namespaced `localStorage` key, например `hrm-theme`;
- валидируй значение storage, обработай отсутствие/битое значение и выбранный fallback `prefers-color-scheme`;
- не добавляй backend persistence без существующего API;
- controls должны быть keyboard-accessible и иметь корректный `aria-pressed`/role;
- если добавляешь «Комфортная/Компактная», используй существующие `data-density` tokens, валидируй и тестируй;
- font switch добавляй только если production CSS реально поддерживает его, не имитируй несуществующую загрузку.

Проверь dark mode для body/app, sidebar, topbar, cards, table, inputs, chips, drawer, modal, Kanban и library. Убирай hardcoded colors в пользу tokens.

Не создавай отдельные theme states в каждом экране. Используй существующий provider/hook pattern или добавь один `AppearanceProvider`/`useAppearance` после проверки providers.

### Поиск

Сначала найди существующий shell search/command palette. Не создавай второй competing search. Если общего поиска нет, не делай псевдо-поиск по demo data: добавляй scoped palette только при понятном API/маршрутизации. Обязательны debounce, loading/no-results/error, keyboard navigation, Escape, focus management и отсутствие PII в localStorage.

## Порядок работ

1. Зафиксируй baseline: `git status`, branch, `npm test`, `npm run typecheck`, `npm run build` из `frontend`.
2. В PR description составь gap matrix: mockup element → production component → gap → code/test.
3. Реализуй appearance state + Settings UI + tests (initial state, toggle, invalid storage, reload persistence).
4. Адаптируй tokens/shell и проверь light/dark.
5. Адаптируй Candidates table/toolbar/rows, затем Kanban, затем Library.
6. Только при наличии API и тестов доведи global search.
7. Запусти validation и вручную проверь роли, responsive layout, drawer, drag/drop, errors и theme reload.

## Acceptance criteria

- [ ] Sidebar set и role visibility не изменены.
- [ ] Нет mockup demo data в production runtime.
- [ ] Candidates используют реальные API, но визуально и по UX ближе к mockup; drawer/actions/errors сохранены.
- [ ] Kanban использует реальные mutations, drag/drop, доступную альтернативу, error/retry и drawer.
- [ ] Library использует реальные материалы и сохраняет detail/download/print/permissions.
- [ ] Settings содержит рабочий light/dark selector.
- [ ] Theme глобален через `data-theme`, переживает reload, сохраняет контраст и focus.
- [ ] Density, если реализована, глобальна через `data-density` и покрыта тестами.
- [ ] Новые интерактивности покрыты Vitest/Testing Library.
- [ ] Нет TypeScript/ESLint ошибок; responsive и reduced-motion проверены.
- [ ] PR содержит screenshots light/dark, список файлов, limitations и результаты команд.

Из `C:\Users\User\Documents\HR\HR Manager Desktop\HR-Manager-merged-main\frontend` выполни:

```text
npm test
npm run typecheck
npm run lint
npm run build
```

Если команда не запускается из-за окружения, укажи точную причину в PR.

## Git/PR дисциплина

- Создай feature branch от актуального base; не коммить в detached HEAD и не переписывай base history.
- Рекомендуемые тематические коммиты: `feat: add persistent appearance settings`, `style: align candidate and kanban surfaces with mockup`, `style: align library and settings visuals`, `test: cover mockup parity interactions`.
- Перед commit проверяй `git diff` и `git status`.
- Не включай backups, `.pr*`, secrets и unrelated generated files.
- В финале укажи base/head commit, изменённые файлы, тесты и что намеренно не менялось.

Итог: production-разделы «Кандидаты», «Воронка кандидатов», «Шаблоны и материалы» и «Настройки» должны ощущаться столь же компактными и презентабельными, как Bento Arctic, но работать на реальных данных и существующих бизнес-правилах.
