# Отчёт: этап 6 «Аналитика и отчёты» (agent-2)

Ветка: `arena/phase-6-analytics` · База: `origin/main` = `002fa4539ee271b2408b019a94d206ad10f8cc15`
(«docs: define phase 6 analytics contract», содержит `prompts/PHASE_6_PROMPT.md`).
Контракт этапа — `prompts/PHASE_6_PROMPT.md`; все решения ниже сверены с ним.

> Статус: ветка опубликована на GitHub (`origin/arena/phase-6-analytics`),
> открыт PR #8 в `main` (без merge). CI GitHub Actions зелёный, в т.ч. на
> head с исправлениями по ревью оркестратора (см. «Ревью-фиксы» и «CI/PR»
> ниже).

## Объём и план (зафиксированы до реализации)

1. **Контракт в ARCHITECTURE до миграции** (период/таймзоны, метрики-факты,
   конверсии, разрезы, роли, CSV, журнал фактов и увольнения).
2. **Backend**: миграция `0006` (append-only `analytics_facts` + бэкфил;
   `candidate_terminations`), запись фактов в транзакциях бизнес-операций,
   `GET /analytics/kpi|funnel|export` на SQL-агрегациях, RBAC (HR → 403).
3. **Frontend**: раздел «Аналитика» только для manager/admin, пресеты +
   произвольный период, таймзона, фильтры, KPI-полоса, воронка, разрезы,
   экспорт; состояния загрузки/пустоты/ошибки/повтора/403/stale.
4. **Тесты**: unit + PostgreSQL integration (точность KPI, границы,
   UTC/Europe-Moscow/DST, cohort/null, фильтры/RBAC, CSV по-байтово,
   идемпотентность/конкурентность/откат, alembic-цикл, OpenAPI) + frontend
   vitest/RTL + регрессии этапов 1–5.
5. **Quality**: ruff/mypy/eslint/tsc/build/npm audit, live smoke, статическая
   проверка Compose, `git diff --check`.
6. **Результат**: публикация ветки, PR в main (без merge), этот отчёт.

## Коммиты

| SHA | Коммит |
|---|---|
| `6b79d34` | feat(backend): analytics API with facts ledger, terminations and CSV export |
| `fa2c766` | feat(frontend): analytics section with presets, filters, KPI/funnel/breakdowns and CSV export |
| `686f2f8` | docs: analytics section in README and ARCHITECTURE; HTTP 422 constant |
| `9f68758` | docs: phase 6 analytics report |
| `b5ef66c` | docs: phase 6 report with PR #8 and CI links |
| `9fdc1ac` | docs: exact commit titles in phase 6 report |
| `3b1839d` | style(backend): remove trailing whitespace in migration 0006 |
| `fab3b19` | fix(backend): always emit explicit UTC offset for termination timestamps |

Полный список коммитов ветки — `git log --oneline origin/main..arena/phase-6-analytics`;
финальные docs-правки отчёта также видны там.

## Изменённые/новые файлы

Backend: `alembic/versions/0006_analytics_ledger_and_terminations.py` (новый),
`app/models.py` (AnalyticsFact, AnalyticsFactType, CandidateTermination,
audit-действия `candidate_terminated`/`analytics_exported`),
`app/analytics_ledger.py` (новый), `app/analytics.py` (новый: SQL-агрегации +
CSV), `app/routers/analytics.py` (новый), `app/routers/candidates.py` (факты
в create/update/interaction/transfer + endpoint'ы увольнения),
`app/routers/events.py` (факты create/complete), `app/schemas.py`
(аналитические схемы + схемы увольнения), `app/main.py` (роутер).
Тесты: `tests/test_analytics.py` (23), `tests/test_integration_analytics.py`
(10), `tests/test_migrations.py` (HEAD 0006 + таблицы).

Frontend: `src/features/analytics/` (AnalyticsPage.tsx, analytics.css,
time.ts + тесты), `src/api.ts` (fetchAnalyticsKpi/Funnel/exportAnalyticsCsv),
`src/api.analytics.test.ts`, `src/types.ts` (контракты + KPI-подписи и
определения), `src/app-shell/Workspace.tsx` + `useWorkspaceSection.ts`
(раздел `#/analytics` у manager/admin).

Документы: `README.md`, `docs/ARCHITECTURE.md` (контракт, журнал фактов,
бэкфил и его ограничения, таймзоны, роли, endpoints, CSV, ограничения).

## Модель данных и источник правды

**`analytics_facts`** — append-only журнал: `fact_type` (7 типов), `fact_at`
(UTC), `stage_from`/`stage_to`, `source` (снимок на момент факта),
`owner_user_id` (ответственный HR на момент факта), ссылки на бизнес-строки
(interaction/event/transfer/termination). CHECK словаря типов; индексы
`(fact_at)`, `(fact_at, owner_user_id)`, `(fact_at, source)`; частичные
уникальные индексы по бизнес-строкам (идемпотентность); для событий —
`(event_id, fact_type, fact_at)` (повторное завершение — легитимный новый
факт, точные дубли блокируются). Строки не обновляются и не удаляются
приложением.

**`candidate_terminations`** — увольнение как бизнес-событие:
`terminated_at` + непустая `reason` (CHECK `length(trim(reason)) > 0`) +
кто записал. Текущий статус `fired` без даты метрику не формирует.

**Бэкфил (в миграции, до данных):** создания из `candidates`, взаимодействия
из `candidate_interactions`, передачи из `candidate_transfers`, события из
`events` (created/completed), переходы этапов — **только** из `audit_log`
(`candidate_stage_changed`, `details = "old -> new"`); текущая стадия как
«переход» не выдумывается. Ответственный на момент факта для старых строк —
последняя передача до факта (fallback — текущий владелец); источник — снимок
«как сейчас». Ограничения бэкфила зафиксированы в ARCHITECTURE (история
старше аудита невосстановима).

**Транзакционность:** факт пишется в той же транзакции, что и бизнес-операция
(один `db.commit()`); сбой записи факта или аудита откатывает операцию —
регрессионные тесты с искусственным сбоем (SQLite и PG).

## Определения метрик (SQL-агрегации по журналу, `[from, to)`)

- `created_candidates` — DISTINCT кандидаты с фактом `candidate_created`
  (включая позже soft-deleted — исторический факт);
- `processed_candidates` — DISTINCT кандидаты с фактами
  interaction_added/stage_changed/transfer/event_created/event_completed;
- `calls` — факты `interaction_added` с subtype `call`;
- `reached` — DISTINCT кандидаты с `stage_changed` → `reached`;
- `interviews_scheduled` / `interviews_done` — DISTINCT `event_id` фактов
  `event_created`/`event_completed` с subtype `interview`;
- `offers` — DISTINCT кандидаты с `stage_changed` → `offer`;
- `hired` — DISTINCT кандидаты с `stage_changed` → `hired`/`started`;
- `dismissed` — DISTINCT кандидаты с `stage_changed` → `rejected` (событие,
  не текущий статус);
- `terminated` — DISTINCT кандидаты с фактом `terminated`.

Воронка — фиксированный порядок `CANDIDATE_STAGE_ORDER` без терминальных
`fired`/`rejected`; достижение `new` = факт создания, прочие — `stage_to`.
Конверсии A→B (когортные): знаменатель = DISTINCT кандидаты, достигшие A;
числитель = те же с фактом B **после** факта A (`fact_at(B) > fact_at(A)`,
SQL join по кандидату); `rate` = `null` при знаменателе 0, иначе
`round(n/d*100, 2)`. Повторные переходы не удваивают (DISTINCT).

Разрезы: `by_source` — источник-снимок факта; `by_hr` — владелец-снимок
факта (передачи не переписывают историю; факты, выполненные менеджером,
атрибутируются ответственному HR). Строки только по реальным данным
периода; сортировка стабильная (source; lower(username)).

## Период, таймзона, RBAC

- `from <= fact_at < to`; сравнение по UTC-инстантам; naive-вход = UTC
  (документировано, без машинного локального времени). 422: `from >= to`,
  период > 366 дней, неизвестная IANA-таймзона, `hr_id` не-HR.
- `timezone` валидируется и возвращается; пресеты считает клиент в
  выбранной таймзоне (`Intl` wall-clock итерации, DST-безопасно, дни
  23/24/25 часов) и шлёт явные `from`/`to`.
- `/analytics/*`: 401 аноним; аутентифицированный HR — **403** (никогда
  фильтрация до своих данных); manager/admin — команда + фильтры
  `hr_id`/`source`.

## API и CSV

`GET /analytics/kpi` → `{period:{from,to,timezone}, filters:{hr_id,source},
scope:"team", kpis:{…10…}, conversions:[{from_stage,to_stage,numerator,
denominator,rate}], by_source:[…], by_hr:[…]}`.
`GET /analytics/funnel` → `{period, filters, stages:[{stage,reached}],
conversions}`. `GET /analytics/export?format=csv` (format обязателен, иное →
422) → `text/csv; charset=utf-8`, UTF-8 BOM, attachment
`analytics-<от>-<до>.csv` (даты в выбранной таймзоне), секции: заголовок +
`period_from/to,timezone,scope,hr_id,source` → `section,kpi` (10 строк) →
`section,conversions` (`from_stage,to_stage,numerator,denominator,rate`, rate
с 2 знаками, пусто при null) → `section,funnel` (`stage,reached`) →
`section,by_source` → `section,by_hr`. Экранирование `,` `;` `"` `\n`;
нейтрализация формул (`=`,`+`,`-`,`@` → префикс `'`); PII не выгружается;
аудит `analytics_exported` (только параметры, без содержимого); ошибки —
JSON общего формата, частичный «успешный» файл не создаётся. Порядок
колонок зафиксирован тестами (точные байты).

## Команды и результаты

Базовая линия (до изменений, на `002fa45`): backend 176 passed, frontend 69
passed, `npm audit` 0 уязвимостей.

Backend (после изменений и ревью-фиксов):
```
ruff check app tests alembic → All checks passed!
ruff format --check app tests alembic → 48 files already formatted
mypy app tests → Success: no issues found in 41 source files
TZ=Europe/Moscow TEST_DATABASE_URL=postgresql+psycopg://…/hr_manager_test \
  pytest -q → 209 passed, 0 failed, 0 skipped (SQLite unit + PostgreSQL integration)
git diff --check origin/main...HEAD → чисто (весь диапазон ветки)
```
Для локального запуска интеграционных тестов обязателен `TEST_DATABASE_URL`
(PostgreSQL URL, тот же формат, что в `.github/workflows/ci.yml`:
`postgresql+psycopg://hr_manager:hr_manager_ci_password@localhost:5432/hr_manager_test`).
Без него pytest пропускает интеграционные и миграционные тесты (45 skips:
именно это видит ревьюер без настроенного PostgreSQL). SQLite-сюита отдельно:
`APP_ENV=test pytest -q` → 164 passed, 45 skipped, 0 failed.
Frontend (после изменений):
```
npm run lint → чисто · npm run typecheck → чисто
npm run test → 14 файлов, 101 тест passed
npm run build → собран · npm audit --audit-level=high → found 0 vulnerabilities
```
Миграции: `alembic upgrade head / downgrade 0005 / upgrade head` — OK;
`alembic upgrade head` дважды — OK (тесты `test_migrations.py`, HEAD `0006`).

Live smoke (реальный uvicorn + PostgreSQL, 28 проверок, все PASS): admin
login → создание manager/hr → HR на `/analytics/kpi` = 403 → HR создаёт
кандидата, сменяет этапы contacted/reached/offer, добавляет call,
увольняет с причиной → manager: kpi (created=1, calls=1, offers=1,
terminated=1, dismissed=0, 8 конверсий от `new`), funnel (new/contacted/
reached/offer = 1), фильтры `hr_id`/`source` (неизвестный hr_id → 422),
экспорт CSV (BOM, content-type `text/csv; charset=utf-8`, attachment,
секции, без PII/причины), `format=xlsx` → 422, без `format` → 422, аудит
`analytics_exported` без содержимого, аноним → 401, перевёрнутый период →
422.

Проверки, которые в песочнице недоступны: полный `docker compose` запуск
(нет docker; статически: оба compose-файла парсятся PyYAML с `!reset`,
`make -n` OK, `test_production_overlay.py` 6 passed). GitHub Actions по ветке
запустятся после публикации (workflow `ci.yml` не менялся).

## Ограничения этапа

- Без сохранённых представлений, конструктора отчётов, сравнения периодов,
  графиков, интеграций, расписаний/email-рассылок, импорта данных (по
  промпту).
- Бэкфил: см. раздел «Бэкфил» (аппроксимации владельца/источника для
  до-миграционных фактов зафиксированы).
- Внешние словари (snake_case, UUID строками) описаны в OpenAPI и
  frontend-типах; PII из аналитики не возвращается никогда.
- Формат CSV-имени файла продублирован на клиенте только как fallback:
  клиент берёт имя из `Content-Disposition` сервера.

## Ревью-фиксы (по результатам независимой проверки оркестратора)

1. **Timezone увольнений на SQLite.** Независимая проверка на машине с
   TZ=Europe/Moscow поймала: `test_termination_endpoint_and_metric` падал,
   т.к. SQLite DATETIME возвращает naive-значения и endpoint отдавал
   `terminated_at` без offset — на не-UTC машине инстант сдвигался на 3 часа
   (в песочнице TZ=UTC баг не проявлялся). Исправлено на уровне модели:
   `UTCDateTime` TypeDecorator (`backend/app/models.py`) нормализует
   terminated_at в aware UTC на чтении/записи для любого диалекта;
   дополнительно `CandidateTerminationOut` получил валидатор `_as_utc`
   (контракт на границе API). Тест усилен: явная проверка
   `tzinfo is not None` в ответах POST и GET — теперь падает на любой
   машине при регрессии. Проверено: тест зелёный в TZ=UTC, Europe/Moscow,
   America/New_York, Asia/Tokyo; полная сюита 209 passed в TZ=Europe/Moscow.
2. **Trailing whitespace.** `git diff --check` по диапазону
   `origin/main...HEAD` ловил 5 строк в `0006_analytics_ledger_and_terminations.py`
   (мой локальный `git diff --check` проверял только рабочее дерево и потому
   молчал). Убрано (коммит `3b1839d`), проверка по всему диапазону ветки —
   чисто (exit 0).
3. **Интеграционные тесты у ревьюера.** `45 skipped` у оркестратора — это
   интеграционные и миграционные тесты, пропущенные без `TEST_DATABASE_URL`
   (см. команду выше). На GitHub Actions они выполняются в service-контейнере
   PostgreSQL; локально — с локальным PostgreSQL по той же URL-схеме.

## CI/PR

- **PR**: https://github.com/sledovatel61/HR-Manager/pull/8
  («Phase 6: Analytics and reports», `arena/phase-6-analytics` → `main`,
  состояние OPEN, mergeable; merge в main не выполняется).
- **CI**: runs (событие `pull_request`, все — реальные исполнения GitHub Actions
  на раннере репозитория):
  - https://github.com/sledovatel61/HR-Manager/actions/runs/33774446217
    (head `9f68758`) — conclusion `success`, 4/4 job;
  - https://github.com/sledovatel61/HR-Manager/actions/runs/33774767503
    (head `9fdc1ac`) — conclusion `success`, 4/4 job;
  - https://github.com/sledovatel61/HR-Manager/actions/runs/33779265894
    (head `fab3b19`, с ревью-фиксами) — conclusion `success`, 4/4 job:
    `Backend checks`, `Frontend checks`, `Backend integration tests (PostgreSQL)`,
    `Compose stack smoke test (dev + prod overlay)` — все `success`
    (`gh pr checks` по PR #8: 4/4 pass). Полный список запусков ветки:
    `gh run list --branch arena/phase-6-analytics`.
