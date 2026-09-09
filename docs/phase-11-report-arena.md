# Phase 11 — документы и личные правила (Arena)

## PR и проверяемый код

- PR: https://github.com/sledovatel61/HR-Manager/pull/18
- Code SHA (включая обновление прав после ожидания блокировки): `0a6319b99ddf014e99158ad17ee8cdbefb1a5f3a`.
- CI итогового tip: https://github.com/sledovatel61/HR-Manager/pull/18/checks
- Final tip SHA, конкретный CI run и подтверждение всех четырёх jobs будут
  опубликованы в итоговом комментарии PR после завершения проверок. Этот
  отчёт сам входит в проверяемый tip; code SHA выше не подменяет final SHA.

## База и ветка

- Дата: 2026-09-08.
- Актуальный `origin/main`, проверенный повторным fetch:
  `1a5d0291780a8e41964ff9e47de8e8f09983544f`.
- Ветка сессии: `arena/01a081aa-hr-manager`. `main` не изменялся;
  чужие ветки/worktree не использовались, merge не выполнялся.
- PR #16/#17 не переносились. Нет нового status endpoint, `501`, hard-delete
  правил или cascade удаления execution history.
- Реальный baseline Alembic — `0011`, новый head — **`0012`**.
- Final SHA и ссылки на проверки фиксируются в итоговом комментарии PR после
  commit/push этого отчёта: SHA коммита не может содержаться внутри самого себя.
  Для точного checkout: `git rev-parse HEAD`.

## Что реализовано

### Списки и снимки

`document_lists` — стабильная сущность с общей областью или этапом из словаря.
`document_list_versions` — отдельные версии с русскими упорядоченными позициями,
ключом, обязательностью, пояснением, автором и временем. Изменение всегда создаёт
новый draft; нет endpoint редактирования опубликованного текста. Публикация
архивирует прежнюю published-версию в той же области. Архивные версии доступны
управляющему списками и через исторические снимки кандидата.

PostgreSQL: transaction advisory lock публикации (включая пустую область),
FOR UPDATE родителя, optimistic counter, partial UNIQUE по области. При
архивировании версии другого списка блокируется также его родитель, чтобы не
затереть конкурентное создание черновика. Номер версии выделяется из счётчика
родителя; после публикаций допустимы пропуски номеров.

`candidate_document_sets` — неизменяемые exact-снимки (полный состав, list ID,
version ID/number, автор). Новое применение не переписывает предыдущее.
`candidate_document_items` — receipt facts, `missing | received`, изменивший
пользователь/время, optimistic version. Новая версия применяется явно;
получение из старого снимка автоматически не переносится. Только факт получения:
нет файлов, номеров документов, сканов или свободных заметок.

PG triggers защищают версии, состав снимка, execution history и попытки
доставки от UPDATE/DELETE; правила защищены от DELETE даже до первого исполнения.
FK истории — RESTRICT, не CASCADE. Mutating API используют CSRF и
expected_version/expected_revision либо уникальную durable idempotency-запись.

### Доступ

- Управление контентом: admin или подтверждённый `document_lists_manage`.
- Документы кандидата: HR/manager — собственные кандидаты; расширенная область
  **всей общей базы** выдаётся явно через `candidate_documents_all`.
  Это текущая модель общей области (в проекте нет организационных подразделений).
- `pilot_full_access` включает обе возможности. Admin без кандидатского гранта
  управляет контентом, но не документами/отправками чужих кандидатов.
- Выдача/отзыв scopes — существующий `/admin/access-grants`, admin + CSRF,
  безопасный аудит. Старый payload без scope сохраняет пилотную семантику.
- IDOR/soft delete: 404, в том числе при прямом запросе receipt. История правила
  повторно фильтруется текущим доступом к кандидату. Существующий общий message
  history не раскрывает document rows руководителю без документного доступа.
- Scanner фильтрует текущую область уже при discovery; worker снова проверяет
  активность владельца, роль/grant, кандидата, этап и условия перед действием
  и перед внешним provider call.

### Ручные сообщения и совместимость Phase 10

Существующие `POST /candidates/{id}/messages/preview` и `/send`, типы
`document_request | document_reminder`, согласия, outbox, worker и адаптеры.
Документный payload теперь содержит `document_set_id`, канал, тип и (для send)
`idempotency_key`. Клиентские `documents`, event, text, recipient не допускаются.
Это намеренное ужесточение старого документационного контракта: старый свободный
список получает 422, frontend заменён одновременно. Интервью API и opt-in flows
сохранены; регрессионные тесты документов переведены на реальные серверные списки.

Текст формируется сервером только из missing+required позиций в исходном
порядке. Outbox хранит immutable text и document_context с list/version/items,
rule/version, template version. История отображает версии и технический статус;
accepted не означает доставку или прочтение. Provider ID сохраняется как вернул
адаптер (plain SMTP без ID остаётся NULL).

Перед provider call повторяются consent/channel/адрес/кандидат/access проверки.
Если **хотя бы одна** перечисленная позиция уже получена либо снимок заменён,
сообщение целиком skipped (`documents_stale`), без переписывания текста и без
отправки устаревшего подмножества. Пользователь может поставить новый запрос
по оставшимся позициям. Пустой набор всегда запрещает отправку.

Документные мутации берут тот же candidate advisory key, который worker держит
на выделенном соединении через provider I/O. Мутация либо успевает первой
(отправка видит новое состояние), либо ждёт завершения уже начатой отправки.
Последняя B0-перепроверка закрывает read transaction до сетевого вызова.

HTTP-ключ связан с пользователем/кандидатом/точным payload, повтор возвращает
исходный ответ даже после изменения согласия. Дополнительный логический ключ
защищает одинаковый тип/канал/snapshot/receipt-state от новых HTTP-ключей.
Новые типы/состояния receipt допускают новое сообщение.

### Настоящее исполнение правил

Закрытые Pydantic trigger/action/stage/channel enums, extra=forbid,
кросс-валидация сочетаний, целые days 1–30 (не bool, float или строка).
Никаких выражений, SQL, eval, URL, regex, шаблонов или чужих владельцев.

- Stage transition: источник — существующий `analytics_facts`, коммитящийся
  вместе с HTTP смены этапа. Не volatile callback. Immutable fact ID — trigger
  object, его версия 1; scanner читает факты после создания/редактирования правила.
- Scheduled reminder: источник — exact assignment; due = created_at + N × 24 ч.
  Одно напоминание на assignment и версию правила, не бесконечная рассылка.
- Discovery через bounded anti-join и уникальный outbox key; повторный проход и
  два scanner-а не дублируют job. Есть индекс `(rule_id, template_version,
  object_type, object_id)`.
- Job — **та же** `notification_outbox`, internal `document_rule_job`,
  `reminder_due / in_app`. Не новая очередь, процесс или broker. Job не создаёт
  пользовательское уведомление: worker выполняет typed action, записывает
  execution и ставит external message в одной транзакции.
- Применение: актуальная published версия указанного списка, только если у
  кандидата ещё нет списка. Request/reminder проверяют list ID, optional exact
  version и реальные missing required позиции.
- Ошибка исполнения откатывает действие/сообщение, существующий bounded retry
  пишет попытку. После лимита — failed execution. Истёкшие lease также имеют
  ограничение попыток. Ошибка discovery не затрагивает HTTP; ledger остаётся
  durable и discovery повторяется следующим проходом worker.
- Rule advisory key сериализует edit/disable, discovery, execution и external
  send. Disable/edit отменяет queued/claimed работу старой версии, записывая
  cancelled execution для неисполненного job. Старая история не изменяется.
- Quiet hours + workdays + timezone владельца применяются к job и сообщению;
  перед provider call правила перепроверяют календарь. Обход через
  quiet_hours_bypassed исключён. PG/UTC и zoneinfo/DST механика сохранена;
  документный путь исправляет отсутствие workdays в прежнем effective_send_time.
- Общие ops retry/cancel не клонируют личные документные задания с утратой
  rule context и не позволяют обойти их права/дедупликацию: 409 с направлением
  в карточку кандидата/«Мои правила». Автоматический retry остаётся рабочим.

### Frontend

- «Списки документов»: серверные версии, история, черновик/публикация/архив,
  создание новой версии, русские элементы/обязательность/пояснения, порядок.
- «Документы» в карточке: exact version, применение/замена, receipt checkbox,
  missing set, серверный preview, email/Telegram, отправка с retry ключом.
- «Мои правила»: список, создание, редактирование, enabled, оба триггера,
  три действия, условия exact/list/missing/channel/days, история последних 30
  исполнений. Никакого свободного JSON/кода.
- Loading/empty/error/retry/403/conflict, native labels/buttons/keyboard,
  общая session/CSRF API-функция. UI не является границей доступа.

## Проверки

Baseline: Ruff check/format и mypy — passed; **458 unit**, **126 frontend**.
Baseline PG/Compose до изменения кода не выполнялись (не заявляются passed).

Локально Linux, Python 3.11.2 (CI — Python 3.12), PostgreSQL 16.2, Node 22:

| Команда | Подтверждённый результат |
|---|---|
| `ruff check .` | passed |
| `ruff format --check .` | 99 файлов |
| `mypy app tests` | 86 файлов, без ошибок |
| `pytest -m 'not integration' -q` | 479 passed, 99 deselected |
| `pytest -m integration -q` | 99 passed, 479 deselected, 0 skipped |
| `npm ci` | passed |
| `npm run lint` / `npm run typecheck` | passed |
| `npm test -- --run` | 139 passed, 19 файлов |
| `npm run build` | passed |
| `npm audit --audit-level=high` | 0 vulnerabilities |
| `git diff --check` | passed |

После заключительной доработки scope-фильтра scanner и его индекса повторены
Ruff/mypy, 22 документных unit, 13 PostgreSQL документных тестов и цикл миграций.
Полный CI запускается на точном итоговом SHA, не подменяется локальным результатом.

Настоящие PG-доказательства: concurrent publication в пустой области,
конкурентное выделение draft, receipt 200+409, parallel scanner/worker,
actual stage PATCH → apply, scheduled/rights loss, disable/history,
DB UPDATE/DELETE rejection, provider call против receipt mutation. Кроме новых
тестов, Phase 10 PG-набор теперь отправляет реальные snapshot-based документы
через SMTP/Telegram stub и проверяет HTTP races, consent, single-flight, lease.

Миграции: `upgrade head → downgrade 0011 → upgrade head`; весь integration
набор также выполняет `upgrade → downgrade base → upgrade`, повторный upgrade.
Backup: все 11 настоящих pg_dump/pg_restore integration тестов прошли, включая
зашифрованный backup и restore drill. Данные/бинарники PG находятся вне Git.
Первоначально локальный кластер создал SQL_ASCII template; он исправлен на UTF8,
после чего полный PG-набор прошёл без пропусков. Зависимости приложения не менялись.

Docker локально отсутствует: все три обязательные Compose-команды возвращают
`docker: command not found` (127). Их успех должен подтверждаться CI stack job,
а не заявляться по статическому анализу. Добавлен regression-тест обновления cached роли владельца после ожидания блокировки;
он и весь документный набор (22 теста) прошли. Финальный полный unit-count в CI — 480.
Повторный полный unit-прогон выполнен
отдельно после timeout параллельного запуска (479 passed).

## Ограничения и handoff

- В области списков реализован этап (минимум задания); отдельного справочника
  должностей/организационных подразделений в этой фазе не добавляется.
- Published контент проверяется как безопасный русский однострочный текст,
  но администратор по-прежнему отвечает за неперсональное содержимое названий.
- Никакое SMTP/Telegram API не даёт проекту универсальной exactly-once гарантии
  при crash после принятия провайдером, но до фиксации результата. Сохранён
  Phase 10 контракт: durable logical dedupe, single-flight, bounded attempts,
  честный provider acceptance; не заявляется exactly-once внешняя доставка.
- Старые document rows без exact context fail closed. Автоматического
  превращения свободного текста в published списки нет.
- Downgrade удаляет новые таблицы/колонки и новые scopes. Это явная
  административная операция для тестового/согласованного отката схемы, не
  автоматический production rollback и не retention/purge API.
- Новых env/сервисов/runtime зависимостей нет. Точка входа worker:
  `scan_document_rules`, `perform_job`, `documents.revalidate_documents`.
- Следующий этап должен сохранить append-only историю и закрытые словари.
  Нельзя копировать outbox через ops без rule/version/context либо добавлять
  произвольный status endpoint с доступом к кандидатским данным.

## Инвентарь изменений

Новые: `app/document_schemas.py`, `documents.py`, `document_messages.py`,
`document_rules.py`, `routers/documents.py`, `routers/document_rules.py`,
миграция `0012_documents_and_rules.py`, `test_documents.py`,
`test_integration_documents.py`, frontend `features/documents/*`, этот отчёт.
Изменены: модели, schemas, main, worker, routers candidate_messages/setup/ops;
миграционные/Phase 10/worker regression fixtures/tests; frontend api/types,
workspace navigation, drawer, message composer/history и regression tests.
README/ARCHITECTURE/CURRENT_STATUS дополнены без объявления Phase 11 принятой.
