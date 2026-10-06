# Bento Arctic — продолжение PR #50, 2026-10-06

## Область отчёта и состояние веток

- Проверенная база `main`: `a62db2344967d67b210577ca1b7bb0d6f4a0526b`.
- Исходный head PR #50: `8d8e1c0aef3554d6da17efbb18ddc69729ca2901`,
  ветка `arena/44cb1fd1-hr-manager`. Его существующая работа сохранена fast-forward.
- **Эта сессия закреплена за `arena/9d09afe4-hr-manager`.** Все исправления продолжения
  находятся здесь. Ветка PR #50 не изменялась, новый PR не создавался.
  Локальные результаты ниже **не означают зелёный CI PR #50**: исправления ещё нужно
  перенести в его ветку и дождаться CI на новом SHA.
- Прочитан промпт продолжения из `origin/arena/01a0d78f-hr-manager`:
  `prompts/BENTO_ARCTIC_PR50_CONTINUE_NEW_AGENT_PROMPT.md`.
- Промпт оркестратора отсутствует в текущем дереве; прочитан через
  `git show 14fa0609ed9b4b8aed6675709e9e4263026ca531:docs/MOCKUP_PARITY_AGENT_PROMPT_RU.md`.
- Reference: `design/mockups/bento-arctic.html`. Просмотрены все 9 изображений
  `screenshots/real` и 10 `screenshots/mokap`, включая кандидатов, Kanban и библиотеку.
  Это **предоставленные исходные изображения**, не доказательство работы нового кода.
  Существующие каталоги и изображения не изменены.

## P0 — выполнено в ветке продолжения

### Контрастные доказательства (`db04766`)

На исходном `8d8e1c0` собственным запуском воспроизведены 21 устаревшая ссылка
и exit 1 у `python3 scripts/measure-contrast.py --check-refs`.
В `scripts/measure-contrast.py` изменены **только номера строк и диапазоны**:
не менялись алгоритм гейта, пары, исключения, тесты или CSS ради старых ссылок.

Перепроверка по фактическому CSS уточнила таблицу промпта:
`row-avatar` — 156/157, но проза о `filter-chip` — **196/197**, диапазон 189–200.
Для некавыченного упоминания `.filter-chip-remove:hover` диапазон 207–225 включает
и базовый селектор, и hover. Все три прогона теперь дают ноль, включая prose refs.

### Администратор = надмножество HR (`67b9914`)

- В `workspaceSections.ts` `queue` добавлен первым в admin-список. Все прежние
  административные разделы сохранены; каждый раздел HR доступен admin.
  Это включение множеств, **не равенство**: у admin остаются дополнительные разделы.
- Администратор без section hash после входа попадает на «Мою очередь» (вариант A).
  Стартовый экран определяется `useWorkspaceSection(sections[0])`, а не строкой 80
  `Workspace.tsx`: та строка — callback перехода к кандидату из календаря/уведомлений.
  Он намеренно сохранён: администратор открывает такие карточки в общем списке.
  Явные deep links продолжают открывать запрошенный доступный раздел.
- 5 новых shell-тестов: первый раздел, включение HR-набора и точный admin-only остаток,
  manager без queue, открытие реальной MyQueuePage без hash и по `#/queue`, клик меню.
  Актуализирован существующий `App.auth.test.tsx` для новой страницы после входа.
- Новый backend regression в `test_queue_dashboard.py` проверяет **оба endpoint**:
  без `owner_id` агрегаты, этапы, источники, выборки кандидатов и события принадлежат
  только admin; три кандидата другого HR не попадают в результат. Явный `owner_id`
  переключает dashboard на HR, следующий запрос без него снова личный.
- У summary **нет параметра `owner_id`**, он всегда личный. Переключение владельца
  поддерживает dashboard. Backend production-код и авторизация не менялись.
- **Открытый продуктовый вопрос: manager по-прежнему не видит `queue`.**
  Решение заказчика было только про admin; самостоятельно доступ не расширялся.

P1 начат после успешных локальных P0-прогонов: 407 frontend tests и 1181 backend tests,
три контрастных проверки — ноль. Ограничение версии Python описано ниже.

## P1 — визуальная иерархия на реальных данных (`af6ca9b`)

### Кандидаты

- `CandidatesListPage.tsx`: «Показано N–M из K» с `role="status"`, где K — API total,
  N/M — offset и **действительно полученные items**, а не запрошенный размер страницы.
- Пустой ответ показывает `0–0`, а не `1–0`. При опустевшей последней странице
  доступна кнопка «Назад», неверное `2 / 1` заменяется на «Список изменился».
- В page header загрузка/ошибка больше не выдаются за пустую базу или свежие числа.
- Тесты покрывают первую/последнюю, короткую и опустевшую страницу; прежние проверки
  фильтров, drawer, удаления, propagation и прав проходят.

### Kanban

- `KanbanPage.tsx` / `kanban.css`: инициалы, источник (`SOURCE_LABELS`), реальная дата
  обновления, разделённый footer, радиус/отступы карточек на существующих токенах.
- Съёмные чипы фильтров должности и ответственного сохраняют другой фильтр и
  перезапрашивают все колонки с корректными параметрами.
- Видимая цель drag/drop, `aria-busy` на время мутации, остановка автоскролла при
  завершении drag. Сохранены select-перенос с клавиатуры, rollback, retry и drawer.
- Общий count не показывается как полный, пока хотя бы одна колонка грузится/ошиблась.
- Новые тесты проверяют метаданные, отсутствие выдуманного скоринга/зарплаты,
  drag/drop + busy и независимое снятие обоих фильтров.
- Этапы и `CANDIDATE_STAGE_ORDER` не менялись. Размер страницы колонки остаётся 20.

### Библиотека

- `LibraryPage.tsx` / `library.css`: count каталога, category tiles с иконками и
  настоящими counts, адаптивная сетка от 240px (одна колонка до 720px), верх карточки
  с категорией, footer с **версией и датой публикации** из API. Нет выдуманных размеров файлов.
- Активная категория использует контрастную пару accent-subtle / accent-on-subtle;
  убрана полупрозрачность счётчика. Search/actions переносятся на узком экране.
- Заголовок библиотеки — h2, единственный экранный h1 остаётся в shell.
- Сохранены поиск, категории, recent ids, detail, печать, скачивание, новое окно,
  управление по `can_manage` и серверный `body_html` pipeline.
- 2 новых теста проверяют реальные counts/версию/дату и честное отсутствие даты.

## Актуальная gap matrix

| Макет | Production | Статус / граница |
| --- | --- | --- |
| Глобальные theme/density | `appearance.tsx`, `AppearanceControls`, `index.html` | Сохранена реализация предшественника; тесты проходят |
| Page head кандидатов | `CandidatesListPage` | Контекст и API total, отдельные loading/error состояния |
| Table, filters, drawer, actions | `CandidatesListPage` / `CandidateDrawer` | API-backed, существующие сценарии сохранены |
| «Показано N–M из K» | `CandidatesListPage` | Выполнено, включая пустую/короткую страницу |
| Колонка «СКОРИНГ» | `Candidate`, `CandidateOut`, `useCandidatesList` | **Требует API**: поля score/scoring нет ни в ответе, ни в TS-контракте |
| Kanban metadata и filter chips | `KanbanPage` | Выполнено для имени, должности, источника, владельца, даты и реальных фильтров |
| Зарплата, вилка и progress/score bar | `CandidateOut` | **Требует API и семантики**; salary/progress нет. Номер этапа не выдаётся за скоринг |
| Library catalog | `LibraryPage` | Доработаны категории/сетка/метаданные; primary action «Открыть», управление отдельно |
| Глобальный поиск / command palette | Shell | **Отдельная задача**: shell-search отсутствует; есть только scoped API поиска кандидатов/материалов, не общий контракт |
| Моя очередь admin | `workspaceSections` | Выполнено; стартовая страница admin; личный backend scope закреплён тестом |
| Моя очередь manager | `workspaceSections` | Ждёт продуктового решения; не добавлялась |
| Календарь, аналитика, уведомления | Существующие страницы | Визуальный перенос вне scope; не изменены |
| Font switch | Системный стек production | Не имитируется загрузка отсутствующих шрифтов |
| Light/dark screenshots нового кода | Ручная проверка ниже | **Не выполнено**; исходные screenshots не выдаются за новые |

## Валидация конечного кода

Среда: Node `22.22.3`, npm `10.9.8`, Python **3.11.2** в `.venv`, зависимости из
`backend/requirements-dev.txt` без изменений lock/requirements. В CI требуется Python 3.12.
Попытки получить 3.12 через uv/официальный GitHub asset не прошли из-за сетевого
TLS EOF; системные dev-пакеты для сборки также недоступны. Результаты Python 3.11
**не заменяют** обязательный CI на 3.12. Новые зависимости в проект не добавлялись.

| Команда | Результат |
| --- | --- |
| `cd frontend && npm ci` | exit 0; 263 packages, audit 0 vulnerabilities |
| `npm run lint` | exit 0, 0 errors / 0 warnings |
| `npm run typecheck` | exit 0 |
| `npm test` | exit 0, **415 passed**, 36 files |
| `npm run build` | exit 0, 131 modules; предупреждение Vite о chunk >500 kB |
| `python3 scripts/measure-contrast.py` | exit 0, **ИТОГО проблем: 0** |
| `python3 scripts/measure-contrast.py --audit` | exit 0, **Не в гейте: 0** |
| `python3 scripts/measure-contrast.py --check-refs` | exit 0, **ИТОГО устаревших ссылок: 0** |
| `cd backend && ruff check .` | exit 0, All checks passed |
| `ruff format --check .` | exit 0, 182 files already formatted |
| `mypy app tests` | exit 0, 159 source files, no issues |
| `pytest -m "not integration" -v` | exit 0, **1181 passed**, 146 deselected, 47 warnings (344.14s) |
| `git diff --check` | exit 0 |

Предупреждения окружения: npm сообщает deprecation ESLint, Vitest/jsdom — неподдерживаемую
полную document navigation при тесте скачивания, backend — deprecation Starlette/httpx/422.
Ничего из этого не подавлялось ради зелёного отчёта.
PostgreSQL integration, Compose, Windows/installer/release jobs **локально не запускались**.
Их код не изменён; старые результаты GitHub не считаются проверкой этого head.

## GitHub и состав изменений

`gh pr checks 50` после локальных прогонов: Backend checks — **fail**, Frontend checks —
**fail**, Compose — skipped; остальные четыре job — pass. `gh pr view 50`:
head `8d8e1c0`, `UNSTABLE` / `MERGEABLE`. Это старый запуск `37444247110`,
а не проверка исправлений. Workflow запускается на PR или push в main, поэтому push
ветки продолжения сам по себе CI #50 не перезапустит. CI/workflow не модифицировались.

Изменения этого раунда относительно `8d8e1c0` — 14 файлов:

- `scripts/measure-contrast.py`
- `backend/tests/test_queue_dashboard.py`
- `frontend/src/app-shell/workspaceSections.ts`
- `frontend/src/app-shell/Workspace.test.tsx`
- `frontend/src/App.auth.test.tsx`
- `frontend/src/features/candidates/CandidatesListPage.tsx`
- `frontend/src/features/candidates/CandidatesListPage.test.tsx`
- `frontend/src/features/candidates/KanbanPage.tsx`
- `frontend/src/features/candidates/KanbanPage.test.tsx`
- `frontend/src/features/candidates/kanban.css`
- `frontend/src/features/library/LibraryPage.tsx`
- `frontend/src/features/library/Library.test.tsx`
- `frontend/src/features/library/library.css`
- `docs/MOCKUP_PARITY_PROGRESS.md`

Общий diff против `origin/main` включает 46 файлов (в том числе исходные screenshots
предшественника). Diff проверен; новых бинарников, секретов, lockfile-изменений,
бэкапов или сгенерированного мусора в продолжении нет. Коммиты кода для переноса
сопровождающим PR #50: `db04766`, `67b9914`, `af6ca9b`, затем коммит этого отчёта.
История не переписывалась; main и ветка #50 не изменялись.

## Ручная процедура скриншотов и визуальной приёмки (осталось)

Браузерные executable (`chromium`, `chromium-browser`, `google-chrome`, `firefox`) и
Playwright/Puppeteer в этой среде не найдены. Новые screenshots не снимались.

1. Развернуть **ветку продолжения или перенесённые коммиты** на тестовом стенде обычным
   проектным способом. Записать SHA, браузер/версию, роль, размеры viewport и масштаб.
   Использовать тестовые обезличенные данные, не production-ПДн; не брать числа из макета.
2. Войти как admin без hash в URL: должна открыться «Моя очередь». Убедиться, что
   «Кандидаты» и прежние административные разделы остались. HR-набор должен быть доступен
   admin, у manager пункта «Моя очередь» пока нет.
3. Установить viewport **1440×900**, масштаб 100%. В «Настройки → Внешний вид» выбрать
   **Светлая / Комфортная** (кнопки «Тема оформления» и «Плотность интерфейса»).
4. После окончания загрузки снять `#/candidates`, `#/kanban`, `#/templates`, `#/settings`.
   Для Chrome/Edge: F12 → Ctrl+Shift+M → Responsive 1440×900 → Ctrl+Shift+P →
   `Capture screenshot`. Закрыть overlays DevTools, если используется обычный снимок окна.
   Для Kanban оставить scroll в начале, отдельно снять правые колонки при необходимости.
5. В настройках выбрать **Тёмная / Комфортная** и повторить те же четыре экрана с теми же
   фильтрами и данными. Проверить таблицу, inputs, выбранные chips, карточки и focus.
6. Дополнительно в обеих темах открыть drawer кандидата и модалку добавления (без сохранения),
   detail материала; проверить читаемость и закрытие Escape/кнопкой, Tab/Enter/Space.
   В библиотеке проверить поиск, категории, recent, печать preview, download и «В новом окне».
7. Выбрать **Компактная** и повторить кандидатов и Kanban в обеих темах. Перезагрузить
   страницу: тема и плотность должны сохраниться. Для нового выбора системной темы
   использовать отдельный чистый профиль, не стирать настройки рабочего пользователя.
8. Viewport **390×844**: библиотека — одна колонка, поиск и действия не выходят за экран;
   таблица/доска прокручиваются в своём контейнере. Проверить Windows reduced motion
   (или DevTools → Rendering → prefers-reduced-motion: reduce).
9. На тестовом кандидате проверить drag/drop и select-перенос, busy, возврат карточки
   при ошибке сети; затем вернуть исходный этап. Для пагинации проверить первую/последнюю
   страницу и пустой фильтр. Для библиотеки — HR без manage и разрешённый управляющий.
10. Сохранить обезличенные снимки в **новом** `screenshots/real/<short-sha>/`, например
    `candidates-light-comfortable-1440x900.png`, `kanban-dark-compact-1440x900.png`,
    `library-dark-comfortable-390x844.png`. Не перезаписывать исходные изображения.
    Приложить к PR SHA/окружение и результаты шагов 2–9; только после этого закрывать
    acceptance по визуальной проверке. Снимки `screenshots/mokap` — лишь reference.

## Acceptance / решение о готовности

- [x] Только согласованное расширение role visibility: admin + queue; sidebar не перестроен.
- [x] API-контракты, backend authorization, словарь этапов, duplicate/audit flows не изменены.
- [x] Нет mockup demo-чисел, зарплат или искусственного скоринга в production.
- [x] Candidates / Kanban / Library используют реальные API; интерактивности покрыты тестами.
- [x] Appearance механизмы предшественника сохранены, провайдер общий, persistence проверяется.
- [x] Контрастный гейт, audit и проверка ссылок выполняются без ослабления.
- [x] CSS использует semantic tokens, перенос действий, mobile grid и reduced-motion правила.
- [ ] Визуальная проверка runtime light/dark, mobile и новые screenshots выполнены вручную.
- [ ] Исправления перенесены в head PR #50, его полный CI зелёный на новом SHA.

**Итог: локальная реализация подготовлена; приёмка PR #50 и его merge пока не завершены.**


## Follow-up после baseline 1fdcc46 — 2026-10-06

Предыдущие пункты выше сохранены как исторический отчёт, а не статус нового head.
Новый проход по 20 приёмочным screenshots владельца описан в
[PR50_FOLLOWUP_ACCEPTANCE.md](PR50_FOLLOWUP_ACCEPTANCE.md).

- Контракт анкет подтверждён владельцем: хранение DOCX/PDF, без распознавания.
- Исправлены скролл/геометрия Kanban, доступность файлов и их маркер в списке,
  read-all и поповер уведомлений, legacy/native dark theme, ячейки ФИО графика.
- На конечном коде локально: **434 frontend / 1185 backend unit** passed;
  lint, typecheck, build, Ruff, mypy и contrast refs/audit/gate прошли.
- Выполнен реальный Chromium/API smoke: 3 роли × 2 темы × 2 viewport,
  [99 новых снимков](../screenshots/pr50-follow-up/README.md), геометрия,
  загрузка/скачивание, read-all, mouse DnD/select/edge-scroll. Данные явно
  тестовые, in-memory SQLite; production/PostgreSQL/Windows не объявлены проверенными.
- PR #50 по read-only GitHub проверке всё ещё открыт на
  `arena/44cb1fd1-hr-manager`, head `1fdcc464f727abcf1a82b8fe4e89a087fed81991`.
  Владелец отдельно разрешил follow-up из `arena/9d09afe4-hr-manager` в его ветку.
  Merge, изменение main и force push не выполняются.
