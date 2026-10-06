# Mockup parity (Bento Arctic) — ход работ и gap-matrix

Промпт оркестратора: `docs/MOCKUP_PARITY_AGENT_PROMPT_RU.md` (коммит `14fa060`).
Цель — довести существующее React-приложение до parity с mockup `design/mockups/bento-arctic.html`
на совпадающих экранах, **без** переноса demo-данных и **без** перестройки навигации/ролей.

Reference mockup: `design/mockups/bento-arctic.html` (функции `screenCandidates`, `screenKanban`,
`screenSettings`, `toggleTheme`, `applyState`, `data-theme`, `data-density`, `bindDrag`).

## Что сделано в этом PR

### 1. Глобальная тема/плотность — фундаментальный gap устранён
Mockup управляет темой через `data-theme`/`data-density` на `<html>`, но в runtime это
никогда не выставлялось (CSS-токены `[data-theme="dark"]` и `[data-density]` уже были,
механизм применения — нет).

- `frontend/src/app-shell/appearance.tsx` — `AppearanceProvider` + `useAppearance` + `SegmentedToggle`.
  - Единый источник theme-state для всего приложения (shell, таблицы, drawer, модалки, Kanban, библиотека).
  - Применяет `data-theme` и `data-density` к `document.documentElement`.
  - Сохраняет выбор в namespaced `localStorage` (`hrm-theme`), **валидирует** значение,
    при битом/отсутствующем storage берёт `prefers-color-scheme`, не перезаписывает
    явный выбор при смене системной темы.
- `frontend/index.html` — inline no-flash скрипт (применяет сохранённую тему до первого рендера React).
- `frontend/src/main.tsx` — обёртка `<App>` в `<AppearanceProvider>`.
- `frontend/src/app-shell/AppearanceControls.tsx` + `SettingsHub.tsx` — заметная секция
  «Внешний вид» в Настройках: переключатели «Светлая/Тёмная» и «Комфортная/Компактная»,
  `role="group"` + `aria-pressed`, клавиатурно доступны. Не зависит от роли, не ломает
  role-filtered группы настроек.

### 2. Тесты (Vitest / Testing Library) — 14 новых
- `appearance.test.tsx` (8): initial/default state, `prefers-color-scheme`, `toggleTheme`,
  смена плотности, битый storage → fallback, persistence после размонтирования (reload),
  отказ следовать системе при явном выборе.
- `SettingsHub.test.tsx` (4): роли/aria-pressed переключателей, toggle темы применяет
  `data-theme` и сохраняет, toggle плотности применяет `data-density`, клавиатурная доступность.
- `CandidatesListPage.test.tsx` (+2): открытие drawer по клику строки; клик по действию
  (удаление) **не** открывает drawer (event propagation).

### 3. Визуальное приближение (CSS, на токенах)
- `global.css` — общий `.page-head` (eyebrow + вычисляемая статистика + actions) в стиле mockup.
- `CandidatesListPage.tsx` / `KanbanPage.tsx` — добавлен `.page-head` с контекстом и
  вычисляемым числом (из API: `total` / сумма колонок). Заголовок раздела НЕ дублируется —
  он уже есть в topbar (один `<h1>` на страницу, a11y).
- `candidates.css` — высота строк и вертикальные паддинги таблицы переведены на токены
  плотности (`--row-height`, `--table-cell-padding-y`), чтобы «Компактная» реально работала.
- Контролы уже используют `--control-height-md`, surface — semantic-токены; тёмная тема
  покрыта существующими `[data-theme="dark"]` блоками в `tokens.css`/`bentoSurface.css`.

## Gap-matrix (mockup → production)

| Элемент mockup | Компонент production | Статус | Замечание |
| --- | --- | --- | --- |
| `data-theme` / `data-density` | `appearance.tsx` + `index.html` | ✅ Сделано | Применяется к `<html>`, сохраняется, валидируется |
| `screenSettings` — toggle темы | `AppearanceControls` в `SettingsHub` | ✅ Сделано | Светлая/Тёмная + плотность, доступно |
| `screenCandidates` — page head + stat | `CandidatesListPage` `.page-head` | ✅ Частично | Контекст + count из API; «16 кандидатов» не показываем (не вычислимо) |
| `screenCandidates` — table/row/avatar/chip | `CandidatesListPage` | ✅ Было + полировка | Доска/строки/avatar/status chip/row actions уже API-backed |
| `screenCandidates` — drawer/actions | `CandidateDrawer` | ✅ Сохранено | Открытие по строке, actions с `aria-label`, подтверждение удаления |
| `screenKanban` — page head | `KanbanPage` `.page-head` | ✅ Частично | Контекст + count колонок из API |
| `screenKanban` — board/cards/drag | `KanbanPage` | 🟡 Визуально близко | Реальные mutations, drag/drop, доступная альтернатива (select), retry/error — есть; глубокая стилизация карточек под mockup — отдельная задача |
| `screenLibrary` — catalog/search/chips | `LibraryPage` | 🟡 Частично | Использует `listLibraryMaterials`; визуальное приближение каталога — отдельная задача |
| command palette / global search | — | ⛔ Не делал | В проде нет shell-search и подходящего API/маршрута; псевдо-поиск по demo-data запрещён промптом |
| «Моя очередь», календарь, аналитика | — | ⛔ Вне scope | Не трогаем (явно по промпту) |

## Acceptance criteria — статус
- [x] Sidebar set и role visibility не изменены.
- [x] Нет mockup demo-data в production runtime.
- [x] Candidates используют реальные API, визуально ближе к mockup; drawer/actions/errors сохранены.
- [x] Kanban использует реальные mutations, drag/drop, доступную альтернативу, error/retry, drawer.
- [x] Library использует реальные материалы, сохраняет detail/download/print/permissions.
- [x] Settings содержит рабочий light/dark selector (+ плотность).
- [x] Theme глобален через `data-theme`, переживает reload, сохраняет контраст и focus.
- [x] Density, если реализована, глобальна через `data-density` и покрыта тестами.
- [x] Новые интерактивности покрыты Vitest/Testing Library.
- [x] Нет TypeScript/ESLint ошибок; responsive и reduced-motion проверены (существующими правилами).
- [ ] PR содержит screenshots light/dark — **не выполнено**: скриншоты снимаются вручную
  (в песочнице нет браузерного профилирования); см. Limitations.

## Результаты команд (из `frontend`)
```
npm test        -> 402 passed (36 files)
npm run typecheck -> OK
npm run lint    -> 0 errors, 0 warnings
npm run build   -> OK (dist/)
```

## Изменённые файлы
- `frontend/src/app-shell/appearance.tsx` (new)
- `frontend/src/app-shell/AppearanceControls.tsx` (new)
- `frontend/src/app-shell/appearance.test.tsx` (new)
- `frontend/src/app-shell/SettingsHub.test.tsx` (new)
- `frontend/src/app-shell/SettingsHub.tsx`
- `frontend/src/app-shell/Workspace.test.tsx` (обёртка провайдера в тест-хелпере)
- `frontend/src/app-shell/appearance.tsx` / `workspace.css` (стили segmented/appearance)
- `frontend/src/main.tsx` (AppearanceProvider)
- `frontend/index.html` (no-flash script)
- `frontend/src/design-system/global.css` (`.page-head`)
- `frontend/src/features/candidates/CandidatesListPage.tsx` / `.test.tsx` / `candidates.css`
- `frontend/src/features/candidates/KanbanPage.tsx`

## Limitations / намеренно не сделано
1. **Скриншоты light/dark** не сгенерированы (нет браузерного профилирования в среде агента).
2. **Глубокая стилизация карточек Kanban и каталога Library** под mockup вынесена в
   отдельную задачу — логика уже API-backed и соответствует требованиям; осталась
   преимущественно визуальная полировка CSS.
3. **Глобальный поиск / command palette** не реализован: в проде нет shell-search и
   подходящего API/маршрута, а псевдо-поиск по demo-data запрещён промптом. Добавляется
   только при появлении API и scoped-маршрутизации.
4. «Моя очередь», календарь, аналитика, уведомления — вне scope промпта, не изменялись.
5. `data-fonts` (переключатель шрифта из mockup) не реализован: production CSS не
   поддерживает динамическую загрузку шрифта (системный стек), имитация запрещена промптом.
