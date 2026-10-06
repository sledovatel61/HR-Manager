# PR #50 — раунд 10: CI красный при зелёном отчёте + «Моя очередь» администратору

**Файл сохранён здесь:** `prompts/BENTO_ARCTIC_PR50_CI_RED_PROMPT.md` в ветке
`arena/01a0d78f-hr-manager` (коммит будет запушен сразу после сохранения). Тебе не нужно
его искать — открывай по этому пути.

**Ветка PR:** `arena/44cb1fd1-hr-manager`, голова на момент ревью — `8d8e1c0`
(с момента прошлого промпта не изменилась — значит раунд 9 ещё не выполнен, делай оба).
**База:** `origin/main` = `a62db23` (PR #49 смержен), merge-base совпадает с ней.
`MERGEABLE`, но `mergeStateStatus` = **UNSTABLE**.

---

# ЧАСТЬ 1. CI красный, а в отчёте зелёно (это блокирует мерж)

## Что сделано хорошо, и это не трогать

Работа по сути своей хорошая, и перепроверено запуском, а не на слово:

| Заявление агента | Проверка | Итог |
| --- | --- | --- |
| vitest 402 | `npm test` в `frontend/` | **36 файлов / 402 тестов, exit 0** — точно |
| typecheck / lint / build | запуск | exit 0 / exit 0 / exit 0 |
| 14 новых тестов | подсчёт | `appearance.test.tsx` 8 + `SettingsHub.test.tsx` 4 + `CandidatesListPage.test.tsx` +2 = **14** — точно |
| Sidebar / role visibility не тронуты | `git diff origin/main..HEAD -- frontend/src/app-shell/workspaceSections.ts` | **пусто** |
| Нет demo-data в runtime | grep по `frontend/src` | только SVG-path и комментарий, который объясняет, что числа **не** используются |
| Нет хардкода цветов в новом CSS | grep по diff | **пусто**, только токены |
| `prefers-reduced-motion` | `global.css:98-104` | глобальное правило накрывает новый `transition` |
| `ruff` / `ruff format` | запуск | All checks passed / 182 files already formatted |

`appearance.tsx` — сильный модуль: валидация storage, `prefers-color-scheme` как fallback,
отказ перезаписывать явный выбор при смене системной темы, `try/catch` вокруг localStorage,
namespaced-ключ, `role="group"` + `aria-pressed`. No-flash скрипт синхронизирован с
провайдером, и это сказано комментарием. `docs/MOCKUP_PARITY_PROGRESS.md` — честный
документ с gap-matrix.

## Воспроизведение

```bash
git checkout 8d8e1c0aef3554d6da17efbb18ddc69729ca2901
cd backend && APP_ENV=test python -m pytest -q -m "not integration"
cd .. && python3 scripts/measure-contrast.py --check-refs
```

**Фактически:**

```
FAILED tests/test_measure_contrast.py::test_notes_and_refs_have_no_stale_line_numbers
FAILED tests/test_measure_contrast.py::test_ref_check_reports_a_prose_line_number_that_no_longer_matches
2 failed, 1178 passed, 146 deselected
```

```
ИТОГО устаревших ссылок: 21
EXIT=1
```

**CI (run 37444247110):** `Frontend checks` — **fail**, `Backend checks` — **fail**.

**Заявлено** в теле PR и в `docs/MOCKUP_PARITY_PROGRESS.md`: четыре зелёные команды
(`npm test` / `typecheck` / `lint` / `build`) и **ни одного слова про контраст**.

## Почему

`.github/workflows/ci.yml` в job `Frontend checks` после production-сборки выполняет ещё
три прогона:

```yaml
python3 scripts/measure-contrast.py
python3 scripts/measure-contrast.py --audit
python3 scripts/measure-contrast.py --check-refs
```

Вы поменяли CSS — `candidates.css` (+7 / −2) и `workspace.css` (+115 / −0) — и сдвинули
номера строк в `scripts/measure-contrast.py`. Сам контраст при этом **не сломан**: гейт даёт
0 проблем, аудит даёт «Не в гейте: 0». Сломались только ссылки в доказательствах.

Это ровно тот класс дефекта, который мы чинили в раундах 7 и 8, и ровно та проверка,
которую добавили по промпту раунда 7. **Проверка работает — вы её просто не запустили.**

## Что чинить

Все 21 устаревшая ссылка — в `frontend/src/features/candidates/candidates.css`. Фактические
строки после вашей правки:

| Ожидалось в доказательстве | Факт |
| --- | --- |
| `candidates.css:151` — `var(--accent-subtle)` | **156** (`background: var(--accent-subtle);`) |
| `candidates.css:152` — `var(--accent-on-subtle)` | **157** (`color: var(--accent-on-subtle);`) |
| `candidates.css:141` — `var(--text-link)` | **146** |
| `candidates.css:121` — `var(--surface-hover)` | **126** (`.candidates-table tbody tr:hover` — **125**) |
| `candidates.css:125` — `var(--surface-selected)` | **130** (`tr:focus-within` — **129**) |
| `candidates.css:218` — `var(--surface-pressed)` | **223** (`.filter-chip-remove:hover` — **222**) |
| `candidates.css:219` — `var(--text-primary)` | **224** |

Воспроизведение факта:

```bash
grep -n 'accent-subtle\|accent-on-subtle\|row-name:hover\|tr:hover\|tr:focus-within\|filter-chip-remove' \
  frontend/src/features/candidates/candidates.css
```

Правило: правьте не только машинные `refs`, но и **прозу заметок** — `--check-refs` проверяет
и то и другое. Доказательство, которое нельзя проверить, перестаёт быть доказательством.

---

# ЧАСТЬ 2. Новая задача от заказчика: «Моя очередь» для администратора

## Что требуется

Заказчик смотрит приложение под администратором и не видит экрана «Моя очередь» — главного
экрана мокапа. Причина найдена и проверена: раздел выдаётся только роли `hr`.

`frontend/src/app-shell/workspaceSections.ts:42` — `sectionsForRole(role)`:

* HR (`:62`): `return ["queue", "calendar", "kanban", "schedule", "deleted", ...personal, "settings"];`
* admin (`:64-82`): `return ["candidates", "calendar", "kanban", "schedule", "deleted", "analytics", ...personal, "updates", "license", "admin", "users", "settings"];`

**Проверено set-арифметикой: единственный раздел, который есть у HR и отсутствует у
администратора, — это `queue`.** Всё остальное у администратора уже есть, и даже с запасом
(`analytics`, `candidates`, `users`, `license`, `updates`, `admin`). То есть добавление одного
раздела действительно даёт администратору **весь** функционал HR — ровно то, что просит
заказчик.

Первая в пилотном проекте — Перепечай Мария Павловна, администратор. У неё должен быть
полный функционал.

## Почему это безопасно (проверено, не на слово)

1. **Backend не запрещает.** `queue_summary` (`backend/app/routers/candidates.py:470`) и
   `queue_dashboard` (`:620`) не имеют `Depends(require_role(...))` — только
   `Depends(get_db)` и `Depends(get_current_user)`. Проверено: `grep -n 'Depends(require'
   backend/app/routers/candidates.py` → пусто.
2. **Область видимости личная для каждой роли.** Docstring `queue_summary` говорит прямо:
   *«the scope is ``owner_user_id == caller`` for **every** role — an HR, a manager, an
   administrator and the pilot account all get their own queue»*. Администратор увидит
   **свою** очередь, не чужую.
3. **Экран уже умеет администратора.** `MyQueuePage.tsx:167`:
   `const canSwitchOwner = user?.role === "manager" || user?.role === "admin";` —
   фильтр «Ответственный» администратору уже положен.
4. **Рендер без ролевой guarding.** `Workspace.tsx:164`:
   `{activeSection === "queue" && <MyQueuePage user={user} ... />}` — роль не проверяется.
5. **Группы настроек не затронуты.** `queue` отсутствует и в `SETTINGS_SECTIONS`
   (`settingsGroups.ts:35-44`), и в маппинге групп. Проверено grep'ом. Добавление раздела
   в навигацию не меняет «Настройки».
6. **Тест настроек не сломается.** `Workspace.test.tsx:298` вызывает
   `sectionsForRole("hr")`, а не `"admin"`.

## Что сделать

1. В `frontend/src/app-shell/workspaceSections.ts` добавить `"queue"` в admin-список.
   **Поставьте его первым** — до `"candidates"`: так порядок совпадёт и со списком HR, и с
   мокапом, где «Моя очередь» — первый раздел.
2. Обновить комментарий над admin-списком: объяснить, почему раздел там есть (пилотный
   владелец — администратор с полным функционалом; область видимости личная для каждой
   роли, поэтому чужая очередь не утекает). Комментарий, который объясняет решение,
   ценнее комментария, который его повторяет.
3. **Добавить тесты:**
   * `sectionsForRole("admin")` содержит `"queue"`;
   * `sectionsForRole("admin")` при этом **не** потерял ни один из прежних разделов
     (`candidates`, `analytics`, `users`, `license`, `admin`, `updates`, `settings`, …) —
     регрессия «админ лишился админских прав» должна падать;
   * `sectionsForRole("hr")` и `sectionsForRole("manager")` **не изменились** — HR по-прежнему
     первый и тот же набор, manager по-прежнему без `queue` (если вы решили не давать его
     manager — см. ниже);
   * в `Workspace.test.tsx` — администратор видит пункт «Моя очередь» в боковом меню и
     может открыть экран (маршрут `#/queue` рендерит `MyQueuePage`).
4. **Backend-тест на неутёчку** (в `backend/tests/`): администратор вызывает
   `GET /candidates/queue/summary` и `/candidates/queue/dashboard` и получает **свою**
   очередь; кандидаты другого HR в его агрегаты не попадают; `owner_id` чужого пользователя
   администратор передать может (это уже разрешено), но без `owner_id` — строго личное.
   Если такие тесты уже есть — сошлись на них, не дублируйте.

## Отдельно: страница после входа

`Workspace.tsx:80`: `navigate(user.role === "hr" ? "queue" : "candidates");`

Сейчас после входа администратор попадает на «Кандидаты». Решение за вами, но **не меняйте
молча** — напишите в PR, что выбрали и почему:

* вариант А (рекомендую): администратор после входа тоже попадает на «Моя очередь» — это
  главный экран мокапа и пилотный сценарий;
* вариант Б: оставить «Кандидаты» — администрирование важнее личной очереди.

## Отдельно: manager

У роли `manager` `queue` тоже отсутствует (проверено тем же set-сравнением). Заказчик просил
про администратора — **manager'у не добавляйте**. Но упомяните в PR одним абзацем, что для
manager раздел тоже не выдан, чтобы решение было явным, а не случайным.

---

# Что сделать (итого)

1. **Часть 1** — синхронизировать ссылки в `scripts/measure-contrast.py` (и `refs`, и прозу
   заметок) с фактическими строками `candidates.css`; прогнать все проверки.
2. **Часть 2** — добавить `"queue"` администратору первым разделом + тесты + backend-тест
   на неутёчку + абзац про страницу после входа и про manager.
3. Обновить `docs/MOCKUP_PARITY_PROGRESS.md`: новая строка в gap-matrix («Моя очередь» для
   админа → ✅ Сделано) и честные результаты **всех** команд.
4. Тело PR обновить через
   `gh api -X PATCH repos/sledovatel61/HR-Manager/pulls/50 -F body=@файл.md`
   (не `gh pr edit --body-file` — в этой среде он молча падает).

## Приёмка

Ветка берётся, когда:

* все jobs CI зелёные (`Frontend checks`, `Backend checks` включительно);
* `--check-refs` → 0 устаревших ссылок, и ни одна ссылка — ни машинная, ни в прозе — не
  указывает мимо;
* `pytest -m "not integration"` даёт **0 failed**;
* администратор видит «Моя очередь» в боковом меню и открывает её; экран показывает его
  **личную** очередь;
* у администратора не пропал ни один из прежних разделов;
* HR и manager не изменились;
* в отчёте перечислены **все** прогнанные команды проекта, а не четыре из промпта;
* ни одно число в отчёте не противоречит выводу собственных команд.

Функциональную часть — провайдер, секцию «Внешний вид», page-head'ы, плотность, 14 тестов —
не трогать: она проверена и права.
