# Промпт для AI-агента: аналитика и отчёты (Phase 6)

Реализуй **Phase 6 — аналитику** HR Manager. В `ROADMAP.md` это этап 5
роадмапа («Аналитика»), а в нумерации prompt-файлов — шестой prompt после
`prompts/PHASE_5_PROMPT.md`. Предыдущий этап событий и календаря принят в
`main` merge-коммитом `9da8f1a4bed674a5ca087b54e272d70bdd25291c`.

## Перед началом

1. Прочитай `agents.md`, `PRODUCT_SPEC.md`, `ROADMAP.md`, `README.md`,
   `docs/ARCHITECTURE.md`, все предыдущие phase reports и этот документ.
2. Изучи модели и миграции кандидатов, взаимодействий, переводов, событий,
   пользователей и audit log; существующие enum статусов/источников, роутеры,
   права, frontend API-клиент, workspace/navigation, design-system и тесты.
3. Изучи `design/IMPLEMENTATION_GUIDE.md`, `design/DESIGN_SYSTEM.md`,
   `design/PERSONAS_AND_FLOWS.md`, `design/ACCESSIBILITY.md`,
   `design/REVIEW_CHECKLIST.md` и `design-prototype/`. `AnalyticsPage`,
   `KpiStrip`, `FunnelChart` и `SourceBreakdown` — только UX/визуальный
   ориентир. `mockData` нельзя использовать в production.
4. Обнови локальный репозиторий и начни строго с актуального `origin/main`,
   содержащего указанный merge-коммит или его потомка. Создай отдельную ветку
   `arena/phase-6-analytics` (если имя занято — `arena/phase-6-analytics-<agent>`).
5. Не выполняй merge в `main`. Сначала запусти baseline-проверки, зафиксируй
   краткий план и только затем меняй код.

## Цель и результат

Добавь production-контур аналитики на реальных данных:

- общие KPI команды и персональные KPI выбранного HR;
- funnel по этапам найма и конверсии между этапами;
- показатели отказов и увольнений;
- фильтры по HR, источнику, периоду и timezone;
- серверную пагинацию/сортировку детализации, если она отображается;
- экспорт текущего отчёта с теми же фильтрами и явными параметрами отчёта.

Экран аналитики доступен только `manager` и `admin`. HR не получает
аналитические данные даже при ручном вызове API. Все числа на экране и в
экспорте должны поступать из API и пересчитываться при изменении фильтров.
Запрещены mockData, захардкоженные KPI, client-only экспорт и ложный toast об
успешной операции.

## Зафиксированные определения метрик

### Период и время

- API принимает `from` и `to` как ISO-8601 datetime с timezone, например
  `2026-01-01T00:00:00+03:00`. `from` включителен, `to` исключителен:
  `from <= fact_at < to`.
- Сервер нормализует сравнение к UTC, но интерпретирует preset-периоды в
  timezone запроса `timezone` (IANA, например `Europe/Moscow`). Если timezone
  не передан, используется UTC; сервер и frontend не должны молча использовать
  локальную timezone машины.
- Поддержи preset `day`, `week`, `month`, `quarter` и `custom`. Frontend для
  preset отправляет вычисленные `from`/`to`, чтобы ответ был воспроизводим.
- В ответе всегда возвращай нормализованные `from`, `to`, `timezone` и
  применённые фильтры. Некорректный диапазон, неизвестная timezone и период
  больше 366 дней дают `422`.

### Факты

Метрики периода считают **факты, произошедшие в периоде**, а не просто строки,
которые сейчас находятся на этапе. Источник правды — существующая история
взаимодействий/изменений и новый append-only журнал аналитических фактов,
если текущая схема не позволяет восстановить нужный факт. Нельзя задним числом
придумывать историю из текущего `stage`.

- `created_candidates`: кандидаты, созданные в периоде; soft-deleted кандидаты
  не исключаются из исторического факта.
- `processed_candidates`: уникальные кандидаты, у которых в периоде была хотя
  бы одна бизнес-активность (взаимодействие, изменение этапа, перевод или
  событие, созданное/завершённое); один кандидат считается один раз.
- `calls`: созданные записи взаимодействия типа `call` в периоде.
- `reached`: уникальные кандидаты с зафиксированным результатом дозвона/этапом
  `reached` в периоде. Само наличие звонка не означает дозвон.
- `interviews_scheduled` и `interviews_done`: соответственно события типа
  `interview`, созданные/назначенные и завершённые в периоде; уникальность —
  по событию.
- `offers`: уникальные кандидаты, впервые переведённые в `offer` в периоде.
- `hired`: уникальные кандидаты, впервые переведённые в `hired`/`started` в
  периоде. Совместимость с фактическими enum текущего backend обязательна.
- `dismissed`: уникальные кандидаты с бизнес-событием отказа в периоде
  (переход в `rejected`), а не все кандидаты, сейчас имеющие этот этап.
- `terminated`: уникальные кандидаты с отдельным событием увольнения в периоде,
  с датой и причиной. Простого текущего статуса `fired` недостаточно, если
  дата увольнения не зафиксирована.
- `funnel_counts`: число уникальных кандидатов, достигших каждого этапа в
  выбранном периоде, в фиксированном порядке воронки из backend. Нельзя
  включать этапы, не существующие в общем enum-контракте.

Для каждой конверсии `A -> B` возвращай `numerator`, `denominator` и `rate`.
`denominator` — уникальные кандидаты, достигшие A в периоде; `numerator` —
те же кандидаты, достигшие B в тот же период после A. Если denominator равен
нулю, `rate` равен `null`, а не `0`. Процент — число 0..100 с максимум двумя
знаками после запятой. Это cohort-конверсия, не отношение текущих snapshot-
остатков. Повторный переход назад/вперёд не должен удваивать кандидата.

### Разрезы

`by_source` и `by_hr` используют те же определения фактов и те же уникальные
кандидаты. Источник — source кандидата на момент создания; HR — ответственный
на момент факта (для transferred candidates это не текущий владелец). В ответе
должны быть также строки с нулевыми значениями для выбранного известного
разреза только если это уже принятое поведение существующего UI; не создавай
фиктивные пользователи/источники.

## Обязательный API-контракт

Все endpoint'ы требуют сессию и серверную проверку роли.

### `GET /analytics/kpi`

Query:

```text
from: datetime, required
to: datetime, required
timezone: string = UTC
hr_id: UUID | null
source: CandidateSource | null
```

`hr_id` доступен только manager/admin; без него — вся видимая команда. Ответ:

```json
{
  "period": {"from": "...", "to": "...", "timezone": "..."},
  "filters": {"hr_id": null, "source": null},
  "scope": "team",
  "kpis": {
    "created_candidates": 0, "processed_candidates": 0, "calls": 0,
    "reached": 0, "interviews_scheduled": 0, "interviews_done": 0,
    "offers": 0, "hired": 0, "dismissed": 0, "terminated": 0
  },
  "conversions": [{"from_stage":"new", "to_stage":"contacted",
    "numerator":0, "denominator":0, "rate":null}],
  "by_source": [{"source":"site", "created":0, "hired":0,
    "dismissed":0, "terminated":0}],
  "by_hr": [{"hr_id":"uuid", "username":"...", "created":0,
    "processed":0, "hired":0, "dismissed":0, "terminated":0}]
}
```

Внешний контракт может использовать snake_case и UUID как строки, но должен быть
описан в схемах OpenAPI и frontend types. Не возвращай PII кандидатов.

### `GET /analytics/funnel`

Принимает те же `from`, `to`, `timezone`, `hr_id`, `source` и возвращает:

```json
{"period":{"from":"...","to":"...","timezone":"..."},
 "filters":{"hr_id":null,"source":null},
 "stages":[{"stage":"new","reached":0}],
 "conversions":[{"from_stage":"new","to_stage":"contacted",
   "numerator":0,"denominator":0,"rate":null}]}
```

### `GET /analytics/export`

Принимает ровно те же фильтры, что `/analytics/kpi`, плюс обязательный
`format=csv`. Возвращает `text/csv; charset=utf-8` как attachment с безопасным
фиксированным именем вроде `analytics-2026-01-01-2026-02-01.csv`. CSV должен
содержать UTF-8 BOM для Excel, заголовок отчёта, период/timezone/фильтры и
табличные секции KPI, funnel/conversions и breakdown'ов. Поля и порядок колонок
зафиксируй в документации и тестах. Значения с `,`, `;`, кавычками и переводом
строки корректно экранируй. Формулы CSV injection (значения, начинающиеся с
`=`, `+`, `-`, `@`) нейтрализуй. Не включай ФИО, телефоны, email, заметки или
другую PII. Экспорт аудируется как `analytics_exported` без содержимого отчёта.

Для `401`/`403`/`422` и ошибок БД используй существующий формат ошибок и не
возвращай частично сформированный успешный файл.

## Backend

- Добавь миграцию после текущей последней Alembic-миграции только при
  необходимости. Если нужен ledger, сделай его append-only, с FK, CHECK,
  индексами `(fact_at)`, `(fact_at, owner/source)` и защитой от дубликатов.
- Интегрируй запись факта с операциями кандидатов, interactions, events,
  dismiss/termination и изменением владельца в одной транзакции. Ошибка audit
  или ledger должна откатывать бизнес-операцию.
- Для увольнения добавь отдельную бизнес-сущность/операцию с `terminated_at` и
  непустой безопасной причиной, если её ещё нет; не подменяй её audit log.
- Используй SQL-агрегации, не загружай всю таблицу кандидатов в Python. Стабильно
  обрабатывай пустые данные, duplicate facts, soft delete и NULL.
- Не раскрывай наличие чужих данных HR: для analytics endpoint HR получает 403
  (после успешной аутентификации), не фильтрованную командную статистику.
- Обнови `docs/ARCHITECTURE.md` и `README.md`: определения, ledger/backfill,
  timezone, роли, endpoint'ы, экспорт и ограничения.

## Frontend

Создай/подключи `frontend/src/features/analytics/` и реальный раздел Analytics
в workspace. Перенеси подход прототипа без его моков:

- preset tabs День/Неделя/Месяц/Квартал и произвольный диапазон с timezone;
- фильтры HR и источника только для manager/admin;
- KPI-strip с понятными подписями, tooltip/описанием определения и `N/A` для
  `null`-конверсии;
- доступную таблицу/минималистичную воронку с числовыми значениями и процентами;
- блоки отказов/увольнений и разрезы по источнику/HR;
- экспорт вызывает API с текущими параметрами, обрабатывает имя/ошибку файла и
  не показывает успех до получения ответа;
- loading, empty, error/retry, 401/403 и stale-response состояния;
- фильтры и период не должны сбрасываться из-за переключения представления.

Не добавляй saved views, конструктор произвольных отчётов, сравнение периодов,
графики, интеграции, расписание/почтовую рассылку отчётов или импорт данных.
Не меняй существующие auth/session/CSRF, роли, кандидатов, календарь,
soft-delete, transfer, health, Docker Compose и CI без необходимости для
аналитики.

## Тесты и Definition of Done

Добавь и запусти:

### Backend unit/integration (PostgreSQL)

- точность каждого KPI на fixture с повторными переходами, переводами,
  soft-delete, пустым периодом и NULL;
- включённые/исключённые границы `from`/`to`, UTC и `Europe/Moscow`, DST;
- cohort numerator/denominator/rate, zero denominator=`null`, округление;
- фильтры HR/source, ownership scope, inactive/unknown HR, 401 и 403;
- dismissals и terminations с датой/причиной, включая отсутствие ложного fired;
- отсутствие PII и SQL/API injection в JSON, CSV и audit details;
- CSV headers, BOM, escaping, formula injection, content-disposition;
- идемпотентность ledger, concurrency, rollback при ошибке audit/ledger;
- SQL-агрегации, стабильный порядок, OpenAPI schema;
- Alembic upgrade, downgrade и повторный upgrade.

### Frontend Vitest/React Testing Library

- API получает ровно выбранные фильтры, даты и timezone;
- KPI/funnel/breakdowns, null-rate, empty/loading/error/retry;
- manager/admin visibility и 401/403;
- экспорт скачивается только после успешного ответа, ошибка не маскируется;
- keyboard navigation, labels, focus-visible, semantic table, screen-reader
  text alternatives и reduced motion.

Добавь regression-тесты, подтверждающие, что Phase 1–5 auth, RBAC, CSRF,
candidates, transfers, events/calendar, audit, health и lifecycle shutdown не
сломаны. Выполни доступные backend/frontend lint, format, typecheck, unit и
PostgreSQL integration tests, production build, migration checks, `git diff
--check`, Docker Compose config/smoke и `npm audit`, если последняя уже входит
в проект. GitHub Actions должны быть зелёными. Не заявляй о непроверенных
командах; отдельно перечисли недоступные проверки и причины.

## Результат

Опубликуй ветку `arena/phase-6-analytics`, создай PR в `main`, но не выполняй
merge. Добавь `docs/phase-6-report-<agent>.md`. В отчёте укажи commit SHA,
ссылку на PR и CI, изменённые файлы, миграции, точные определения и SQL/source
of truth фактов, API/CSV schemas, RBAC, timezone, concurrency, команды и
фактические результаты тестов, smoke-проверку и известные ограничения.