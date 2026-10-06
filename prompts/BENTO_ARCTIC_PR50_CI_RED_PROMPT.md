# PR #50 — раунд 9: CI красный, а в отчёте зелёно

**Файл сохранён здесь:** `prompts/BENTO_ARCTIC_PR50_CI_RED_PROMPT.md` в ветке
`arena/01a0d78f-hr-manager` (коммит будет запушен сразу после сохранения). Тебе не нужно
его искать — открывай по этому пути.

**Ветка PR:** `arena/44cb1fd1-hr-manager`, голова на момент ревью — `8d8e1c0`.
**База:** `origin/main` = `a62db23` (PR #49 смержен), merge-base совпадает с ней.
`MERGEABLE`, но `mergeStateStatus` = **UNSTABLE**.

---

## Сначала — что сделано хорошо, и это не трогать

Работа по сути своей хорошая, и перепроверено запуском, а не на слово:

| Заявление | Проверка | Итог |
| --- | --- | --- |
| vitest 402 | `npm test` в `frontend/` | **36 файлов / 402 тестов, exit 0** — точно |
| typecheck | `npx tsc -b` | exit 0 |
| lint | `npx eslint .` | exit 0, без предупреждений |
| build | `npm run build` | exit 0 |
| 14 новых тестов | подсчёт | `appearance.test.tsx` 8 + `SettingsHub.test.tsx` 4 + `CandidatesListPage.test.tsx` +2 = **14** — точно |
| Sidebar / role visibility не тронуты | `git diff origin/main..HEAD -- frontend/src/app-shell/workspaceSections.ts` | **пусто** — жёсткое ограничение №1 соблюдено |
| Нет demo-data в runtime | grep по `frontend/src` | совпадения только в SVG-path и в комментарии, который объясняет, что числа **не** используются |
| Нет хардкода цветов в новом CSS | grep по diff | **пусто**, только токены |
| `prefers-reduced-motion` | `global.css:98-104` | глобальное правило `transition-duration: 0.001ms !important` накрывает новый `transition` |
| Плотность реально работает | `candidates.css` | `--row-height` и `--table-cell-padding-y` подключены к таблице; токены `[data-density]` были на main, механизм — нет |
| `ruff` / `ruff format` | запуск | All checks passed / 182 files already formatted |

`appearance.tsx` — сильный модуль: валидация storage, `prefers-color-scheme` как fallback,
отказ перезаписывать явный выбор при смене системной темы, `try/catch` вокруг localStorage,
namespaced-ключ, `role="group"` + `aria-pressed`. No-flash скрипт в `index.html` синхронизирован
с провайдером и это явно написано комментарием. `docs/MOCKUP_PARITY_PROGRESS.md` — честный
документ с gap-matrix и с одним сознательно непомеченным критерием приёмки.

**14 минут на этот объём — правдоподобно.** Работа настоящая, качество приличное, переделывать
её не нужно. Но есть одна процессная ошибка, которая краснит весь PR.

---

# P1. CI красный. В отчёте — зелёно.

## Воспроизведение

```bash
git checkout 8d8e1c0aef3554d6da17efbb18ddc69729ca2901
cd backend && APP_ENV=test python -m pytest -q -m "not integration"
python3 scripts/measure-contrast.py --check-refs
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

## Заявлено

В теле PR и в `docs/MOCKUP_PARITY_PROGRESS.md`:

```
npm test        -> 402 passed (36 files)
npm run typecheck -> OK
npm run lint    -> 0 errors, 0 warnings
npm run build   -> OK (dist/)
```

Четыре команды, четыре зелёных галочки, и **ни одного слова про контраст**.

## Почему

`.github/workflows/ci.yml` в job `Frontend checks` после production-сборки выполняет ещё три
прогона:

```yaml
python3 scripts/measure-contrast.py
python3 scripts/measure-contrast.py --audit
python3 scripts/measure-contrast.py --check-refs
```

Вы поменяли CSS — `candidates.css` (+7 / −2) и `workspace.css` (+115 / −0) — и сдвинули
номера строк в `scripts/measure-contrast.py`. Сам контраст при этом **не сломан**: гейт даёт
0 проблем, аудит даёт «Не в гейте: 0». Сломались только ссылки в доказательствах.

Это ровно тот класс дефекта, который мы чинили в раундах 7 и 8, и ровно та проверка, которую
добавили по моему раунду-7 промпту. **Проверка работает — вы её просто не запустили.**

## Ожидалось

1. `python3 scripts/measure-contrast.py --check-refs` → «ИТОГО устаревших ссылок: 0», exit 0.
2. Гейт и аудит по-прежнему 0 / 0.
3. `pytest -m "not integration"` → **0 failed** (сейчас 1178 passed + 2 failed).
4. Все четыре команды CI зелёные.

---

# P1-подробно. Что именно чинить

Все 21 устаревшая ссылка — в `frontend/src/features/candidates/candidates.css`. Фактические
строки после вашей правки:

| Ожидалось в доказательстве | Факт |
| --- | --- |
| `candidates.css:151` — `var(--accent-subtle)` | **156** (`background: var(--accent-subtle);`) |
| `candidates.css:152` — `var(--accent-on-subtle)` | **157** (`color: var(--accent-on-subtle);`) |
| `candidates.css:141` — `var(--text-link)` | **146** |
| `candidates.css:121` — `var(--surface-hover)` | **126** (`.candidates-table tbody tr:hover` — **125**) |
| `candidates.css:125` — `var(--surface-selected)` | **130** (`.candidates-table tbody tr:focus-within` — **129**) |
| `candidates.css:218` — `var(--surface-pressed)` | **223** (`.filter-chip-remove:hover` — **222**) |
| `candidates.css:219` — `var(--text-primary)` | **224** |

Воспроизведение факта:

```bash
grep -n 'accent-subtle\|accent-on-subtle\|row-name:hover\|tr:hover\|tr:focus-within\|filter-chip-remove' \
  frontend/src/features/candidates/candidates.css
```

Правило: правьте не только машинные `refs`, но и **прозу заметок** — `--check-refs` проверяет
и то и другое, и ровно на этом мы горели три раунда назад. Доказательство, которое нельзя
проверить, перестаёт быть доказательством.

---

# P2. Контрастные проверки не в вашем списке валидации — а должны быть

Промпт оркестратора перечислял четыре команды: `npm test`, `npm run typecheck`, `npm run lint`,
`npm run build`. Вы выполнили их честно. Но проект содержит ещё один гейт, который запускается
в CI и ломает сборку, и он не был ни в промпте, ни в вашем отчёте.

**Ожидалось:** перед тем как написать «всё зелёно», прогнать полный набор проверок проекта:

```bash
cd frontend && npm test && npm run typecheck && npm run lint && npm run build
cd .. && python3 scripts/measure-contrast.py
        python3 scripts/measure-contrast.py --audit
        python3 scripts/measure-contrast.py --check-refs
cd backend && python -m pytest -q -m "not integration"
```

Локально то же самое делает `make contrast` (см. `Makefile`, коммит `98eebda`). Отчёт,
в котором четыре зелёные галочки при красном CI, хуже отчёта с честным «не проверял»: он
заставляет заказчика поверить в готовность.

---

# Что сделать

1. **P1** — синхронизировать ссылки в `scripts/measure-contrast.py` (и `refs`, и прозу
   заметок) с фактическими строками `candidates.css`. Проверить каждую: на названной строке
   действительно должен стоять ожидаемый `var(...)`.
2. Прогнать и приложить результаты: `--check-refs` (0), гейт (0), аудит (0),
   `pytest -m "not integration"` (**0 failed**), `npm test` (402), typecheck, lint, build.
3. **P2** — включить контрастные проверки в свой обязательный список валидации навсегда.
   Любая правка CSS в этом проекте — это потенциально сдвинутая ссылка в доказательстве.
4. Обновить тело PR и `docs/MOCKUP_PARITY_PROGRESS.md` реальными числами. В частности,
   в `MOCKUP_PARITY_PROGRESS.md` acceptance-критерий «Нет TypeScript/ESLint ошибок» помечен
   `[x]`, при этом CI красный — это несоответствие факту.
5. Тело PR обновить через
   `gh api -X PATCH repos/sledovatel61/HR-Manager/pulls/50 -F body=@файл.md`
   (не `gh pr edit --body-file` — в этой среде он молча падает).

## Приёмка

Ветка берётся, когда:

* все jobs CI зелёные (`Frontend checks`, `Backend checks` включительно);
* `--check-refs` → 0 устаревших ссылок, и ни одна ссылка — ни машинная, ни в прозе — не
  указывает мимо;
* `pytest -m "not integration"` даёт **0 failed**;
* в отчёте перечислены **все** прогнанные команды проекта, а не четыре из промпта;
* ни одно число в отчёте не противоречит выводу собственных команд.

Функциональную часть — провайдер, секцию «Внешний вид», page-head'ы, плотность, 14 тестов —
не трогать: она проверена и права. Чинится только синхронизация ссылок и честность отчёта.
