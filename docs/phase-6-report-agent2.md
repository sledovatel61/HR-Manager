# Отчёт: этап 6 «Аналитика и отчёты» (agent-2)

Ветка: `arena/phase-6-analytics` · База: `origin/main` = `002fa4539ee271b2408b019a94d206ad10f8cc15`
(«docs: define phase 6 analytics contract», содержит `prompts/PHASE_6_PROMPT.md`).
Контракт этапа — `prompts/PHASE_6_PROMPT.md`; все решения ниже сверены с ним.

> Статус на момент финальной правки отчёта: код, тесты, документы и smoke готовы
> и закоммичены локально; публикация ветки и PR отложены до восстановления
> GitHub-авторизации в песочнице (токен `GH_TOKEN` недействителен). SHA
> коммитов зафиксированы ниже; после push они не изменятся.

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

Backend (после изменений):
```
ruff check . → All checks passed!
ruff format --check . → 48 files already formatted
mypy app tests → Success: no issues found in 41 source files
pytest -q → 209 passed (SQLite unit + PostgreSQL integration)
```
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

## CI/PR

Ожидание после публикации ветки: 4 job (Backend checks, Frontend checks,
Backend integration PostgreSQL, Compose stack smoke). PR откроется из
`arena/phase-6-analytics` в `main` без merge — номера/ссылки будут вписаны
после восстановления GitHub-доступа.
