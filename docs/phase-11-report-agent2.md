# Отчёт Phase 11 — Списки документов и правила автоматизации (agent-2)

- **Ветка:** `arena/01a081a2-hr-manager` (сессия Arena жёстко привязана к
  этой ветке, поэтому запрошенное имя `arena/phase-11-document-rules-<suffix>`
  использовать было нельзя; ветка создана от актуального `origin/main`)
- **Baseline SHA (origin/main на старте):** `1a5d0291780a8e41964ff9e47de8e8f09983544f`
  («docs: prepare phase 11 handoff»)
- **Final SHA:** см. раздел «PR и CI» в конце (заполняется после push)
- **PR:** см. раздел «PR и CI»
- **Миграционный head:** `0011` → **`0012`**
  (`0012_document_lists_and_automation_rules`)
- **Merge в `main`:** не выполнялся (по правилам — владелец)

Работа выполнена с чистого `origin/main` (`1a5d029`), а не поверх PR #16/#17.
Из PR #17 заимствованы только идеи структуры; перечисленные владельцем
дефекты (endpoint с `501`, hard delete правил с каскадом на историю,
неполная валидация params, ошибки mypy, отсутствие scope/grant enforcement)
в этой реализации отсутствуют: `501` нет ни в одном router-е, удаление
правила — только soft (`deleted_at`), история срабатываний без каскада
(`ondelete=RESTRICT`), params проверяются закрытыми pydantic-схемами с
`extra="forbid"`, `mypy app tests` чист, scope/grant проверяются и в HTTP, и
при каждом срабатывании правила, и в worker перед provider call. Небезопасный
status endpoint из PR #16 не переносился.

## Что сделано

### 1. Версионируемые списки документов (admin)

- Стабильный `document_lists.id`; цепочка `document_list_versions` со
  статусами `draft|published|archived`, `row_version` (optimistic
  concurrency), `published_by/at`, `archived_at`; элементы
  `document_list_items` (`item_key`, `name`, `explanation`, `is_required`,
  `sort_order`, до 20 элементов, ключи уникальны в версии).
- Инварианты **на уровне PostgreSQL**: `UNIQUE(list_id, version_number)`,
  partial unique `uq_document_list_versions_one_published`
  (`WHERE status='published'`), `UNIQUE(version_id, sort_order)`.
- Публикация атомарна: `FOR UPDATE` заголовка + сравнение
  `expected_row_version` + архивирование прежней published + переключение
  в одной транзакции; параллельная публикация — ровно один победитель
  (integration test с барьером), остальные 409. Нарушение partial unique
  переводится в 409 только для этого конкретного индекса
  (`is_duplicate_key_error`), любые другие `IntegrityError` пробрасываются.
- Опубликованная версия неизменяема: `PUT …/items` принимает только draft
  (иначе 409); один draft на список; новый draft — копия published.
- Аудит без PII: `document_list_created/updated/version_created/
  version_items_updated/version_published/version_archived` с id/номером
  версии/количеством элементов — без названий документов.
- API (`/document-lists`, admin, CSRF): `GET ""`, `POST ""` (201, список +
  первый draft), `GET/PATCH /{id}` (`expected_version`), `POST
  /{id}/versions` (201), `PUT /{id}/versions/{vid}/items`
  (`expected_row_version`), `POST …/publish`, `POST …/archive`, `GET
  …/versions/{vid}`. `GET /document-lists/published` — всем ролям (только
  published, для применения и параметров правил).

### 2. Снимок у кандидата и учёт «получен/не получен»

- `candidate_document_assignments` (точная `version_id` + `version_number`
  + `list_name_snapshot`, кто/правило/когда, `replaced_at/by`; partial unique
  «один текущий список у кандидата») и `candidate_document_items`
  (`name_snapshot`, `explanation_snapshot`, `is_required`, `sort_order`,
  `status missing|received`, `version`, `changed_by_user_id`, `changed_at`).
- Снимок не зависит от последующих публикаций (unit test
  `test_apply_snapshot_is_immune_to_later_publications`). Замена списка
  закрывает старую запись (история), не удаляет.
- Отметка статуса: блокировка строки + `expected_version`; расхождение →
  409 с русским текстом; успех → `version += 1`, `changed_by/at`, аудит
  (`candidate_document_item_changed`: item_key + статус, без имён).
- Доступ (`app/access.py`): HR — только свои (чужие → 404, без утечки
  существования), manager — все, admin — только с пилотным grant (иначе
  403), soft-deleted кандидат исключён везде (404/409). Матрица покрыта
  `test_candidate_documents_access_matrix`.
- API (`/candidates/{id}/documents`): `GET ""` → `{current, history}`,
  `POST /apply {list_id, replace}` (201; замена только с `replace=true`),
  `PATCH /items/{item_id} {status, expected_version}`, `GET
  /missing?required_only=` — только реально недостающие в стабильном
  порядке с точной версией (пустой набор — честно пустой список).

### 3. Запрос и напоминание о документах

- `POST /candidates/{id}/documents/messages {message_type
  document_request|document_reminder, channel?, idempotency_key}` — клиент
  **не передаёт** текст, адрес и названия документов; сервер рендерит
  существующим Phase 10 шаблоном только сейчас недостающие элементы
  применённой версии, ставит в тот же outbox (`object_type=
  "document_assignment"`, `object_version`, PII-free `object_snapshot`
  `{list_id, version_id, version_number, item_keys}`), только по
  разрешённым каналам (без consent/канала → 409). Ничего не хватает → 409.
- Worker перед provider call (`revalidate_document_message`): кандидат не
  удалён, снимок не заменён (`assignment_replaced` → skip), перечисленные
  документы всё ещё missing (`documents_complete` → skip), канал по-прежнему
  разрешён; тело пересобирается из актуального состояния (уже полученные
  позиции не уходят). Cancel-wins/lease/retry — механика фаз 8–10 без
  изменений.
- Integration test `test_rule_reminder_delivered_then_skipped_after_receipt`
  проверяет реальную доставку в SMTP-заглушку (`accepted`), а затем
  `skipped/documents_complete` после отметки документа полученным.

### 4. Правила автоматизации («Мои правила»)

- Словари закрыты и отдаются сервером (`GET /automation-rules/vocabulary`):
  триггеры `stage_entered{stage}`, `documents_missing_due{days_after 1..30}`;
  условия `stage`, `list_id`, `has_missing_required`, `channel
  email|telegram`; действия `apply_document_list{list_id}`,
  `send_document_request{channel?}`, `send_document_reminder{channel?,
  delay_days 0..30}`; матрица `ALLOWED_TRIGGER_ACTIONS` (для
  `documents_missing_due` только reminder). Любые лишние ключи/значения →
  422 (`extra="forbid"`), покрыто `test_vocabulary_and_validation`.
- Правила личные (`owner_user_id`; чужие → 404), с `version`; создание
  требует, чтобы владелец в принципе имел доступ к кандидатам (admin без
  grant → 403).
- `stage_entered` выполняется внутри `update_candidate` в той же
  транзакции, что и переход (outbox-строки коммитятся вместе с переходом),
  каждое правило — в своём savepoint: исключение записывается как
  `failed/internal_error:<ExcType>` (без текста исключения) и не ломает
  HTTP-операцию (`test_rule_failure_is_contained`, включая `caplog` без
  PII). Конкурентные переходы сериализованы `SELECT … FOR UPDATE` кандидата
  — правило выполняется один раз (integration test с барьером).
- `documents_missing_due` сканируется worker-ом (`scan_due_rules`) —
  dedupe `due:{assignment}:{days}:{local_day владельца}`; параллельные
  проходы worker-а ставят ровно одно задание (integration test).
- Durable dedupe: `UNIQUE(rule_id, dedupe_key)` в
  `automation_rule_executions` + `dedupe_key` outbox `rule:{rule}:{key}`.
- История срабатываний неизменяема: `rule_id/rule_version`,
  `trigger_object_type/id/version`, `candidate_id`, `action_type`,
  `outcome queued|applied|skipped|failed`, `outcome_class`, `dedupe_key`,
  `outbox_ids`, `list_id/list_version_id`, `executed_at` — без текста и PII.
  `GET /automation-rules/{id}/executions?limit&offset`.
- Quiet-hours/workdays/timezone — по настройкам **владельца правила**
  (`initiator_user_id` строки outbox), bypass недоступен
  (`test_stage_rule_reminder_respects_owner_quiet_hours_and_workdays`).
- Потеря прав: при каждом срабатывании — `candidate_in_scope`
  (`owner_inactive`, `out_of_scope`); в worker перед отправкой —
  `_rule_row_skip_class` (`rule_inactive`: правило выключено/удалено или
  владелец неактивен/вне scope).
- Disable/update/delete отменяют ещё не начатые задания правила
  (`cancel_pending_rule_jobs`, аудит `cancelled_jobs=N`); удаление — soft.
- Флаги: `AUTOMATION_RULES_ENABLED` (default `true`),
  `AUTOMATION_RULES_BATCH_SIZE` (default 100) — worker-скан due-правил.

### 5. Frontend (русский, design-system, клавиатура)

- «Списки документов» (`#/document-lists`, admin в навигации; backend
  всё равно проверяет): список/детали, версии с бейджами статуса, редактор
  черновика (до 20 элементов, порядок, обязательность, ключ из названия
  транслитерацией), publish/archive через подтверждение, header с областью
  применения; 409 → «Данные устарели» + обновление; 403 →
  «Недостаточно прав»; loading/empty/error/retry.
- Вкладка «Документы» в карточке кандидата: применение/замена списка
  (подтверждение), чекбоксы с `expected_version` и отметкой кто/когда,
  запрос/напоминание с client `idempotency_key` (`crypto.randomUUID()`)
  без текста/адреса, состояние «всё получено — отправлять нечего»,
  история списков, read-only для удалённого кандидата.
- «Мои правила» (`#/rules`, все роли): конструктор только из словаря
  сервера (действия фильтруются по триггеру), клиентская валидация
  диапазонов, русские описания «Когда/Что/Если», включение/выключение и
  редактирование с версией, удаление с `alertdialog`, последние
  срабатывания с русскими классами исхода; loading/empty/error/retry/403/409.

## Изменённые файлы

Новые (backend):

- `backend/alembic/versions/0012_document_lists_and_automation_rules.py`
- `backend/app/document_lists.py`, `backend/app/access.py`,
  `backend/app/automation_rules.py`
- `backend/app/routers/document_lists.py`,
  `backend/app/routers/candidate_documents.py`,
  `backend/app/routers/automation_rules.py`
- `backend/tests/test_document_lists.py` (11),
  `backend/tests/test_automation_rules.py` (12),
  `backend/tests/test_integration_documents_rules.py` (7, PostgreSQL)

Изменённые (backend): `app/models.py` (enums, 7 моделей,
`NotificationOutbox.object_snapshot`, `AuditAction` Phase 11),
`app/schemas.py`, `app/candidate_messages.py` (object_* / rule_id в
outbox-строке), `app/notification_service.py`, `app/worker.py`
(send-time revalidation документных сообщений, rule-row checks, owner
quiet hours, скан due-правил), `app/routers/candidates.py` (FOR UPDATE +
хук stage_entered), `app/main.py` (роутеры), `app/config.py`,
`tests/conftest.py`, `tests/test_migrations.py` (head 0012 + 7 таблиц),
`tests/test_integration_candidate_messages.py`,
`tests/test_integration_worker.py` (см. «Ограничения»).

Новые (frontend): `src/features/automation/RulesPage.tsx`,
`ruleHelpers.ts`, `automation.css`, `RulesPage.test.tsx` (14);
`src/features/documents/DocumentListsPage.tsx`, `listHelpers.ts`,
`DocumentListsPage.test.tsx` (13); `src/features/candidates/DocumentsTab.tsx`,
`DocumentsTab.test.tsx` (9).

Изменённые (frontend): `src/types.ts`, `src/api.ts`,
`src/app-shell/useWorkspaceSection.ts`, `src/app-shell/Workspace.tsx`,
`src/features/candidates/CandidateDrawer.tsx` (вкладка «Документы»),
`src/design-system/icons/Icon.tsx` (`chevron-up`), `src/App.auth.test.tsx`
(навигация по ролям).

Документация: `docs/ARCHITECTURE.md` (раздел этапа 11),
`docs/CURRENT_STATUS.md`, `README.md`, `agents.md`, этот отчёт.

## Миграция 0012

Обратимая, имена ограничений стабильны (`pk_*`, `fk_*`, `uq_*`, `ck_*`,
`ix_*`). Доказано на локальном PostgreSQL 16.2:

```
alembic downgrade 0011 → upgrade head → downgrade -1 → upgrade head → upgrade head (no-op)
```

После цикла в БД присутствуют 7 новых таблиц и колонка
`notification_outbox.object_snapshot`. `tests/test_migrations.py`
(alembic CLI subprocess, head `0012`, up/down/up) — в составе integration
прогона.

## Покрытие обязательных категорий тестов

| # | Категория | Где |
|---|-----------|-----|
| 1 | lifecycle draft/published/archived, неизменяемость published | `test_list_lifecycle_publish_archives_previous`, `test_list_header_update_and_optimistic_conflicts` |
| 2 | атомарная публикация, конкурентное создание версии | `test_concurrent_publication_single_winner`, `test_published_version_cannot_be_deleted_while_referenced` (PG) |
| 3 | RBAC/scopes, CSRF, IDOR, soft-deleted | `test_admin_api_rbac`, `test_candidate_documents_access_matrix`, `test_rules_are_private_and_versioned`, `test_rule_creation_requires_candidate_scope` |
| 4 | применение точной версии и стабильность снимка | `test_apply_snapshot_is_immune_to_later_publications`, `test_concurrent_apply_single_current_assignment` (PG) |
| 5 | optimistic conflict received/missing | `test_item_status_optimistic_concurrency`, `test_concurrent_item_status_change_single_winner` (PG) |
| 6 | реально недостающие обязательные / пустой набор | `test_apply_refuses_unpublished_list_and_missing_endpoint`, `test_document_message_from_applied_list` |
| 7 | ручной request/reminder email+Telegram через outbox | `test_document_message_from_applied_list`, `test_rule_reminder_delivered_then_skipped_after_receipt` (PG, SMTP stub) |
| 8 | запрет без consent/канала, send-time отмена | `test_document_message_requires_allowed_channel`, `test_worker_skips_when_documents_received_meanwhile` |
| 9 | закрытые словари, отклонение произвольных данных | `test_vocabulary_and_validation`, `test_item_validation` |
| 10 | stage trigger, scheduled reminder, disabled rule, утрата прав | `test_stage_rule_applies_list_then_queues_request`, `test_due_rule_queues_reminder_once_per_day`, `test_stage_rule_not_executable_without_scope_or_when_disabled`, `test_worker_skips_rule_row_when_rule_disabled_or_scope_lost` |
| 11 | quiet-hours/workdays/timezone/DST | `test_stage_rule_reminder_respects_owner_quiet_hours_and_workdays` + существующие `test_quiet_hours.py` (регрессия) |
| 12 | идемпотентность HTTP/event/rule, параллельные worker-ы, lease/retry, cancel-wins | `test_stage_rule_dedupe_and_conditions`, `test_disable_and_edit_cancel_queued_jobs`, `test_concurrent_stage_transitions_execute_rule_once`, `test_parallel_due_rule_passes_queue_once` (PG) + существующие `test_integration_worker.py` |
| 13 | immutable execution/message history с версиями | `test_stage_rule_applies_list_then_queues_request` (rule_version/list_version_id/outbox_ids), `test_disable_and_edit_cancel_queued_jobs` |
| 14 | нет PII/текста/секретов в логах, audit, ошибках | `test_rule_failure_is_contained` (caplog), audit-проверки в `test_document_lists.py`/`test_automation_rules.py` |
| 15 | миграционный цикл, OpenAPI, регрессии 0–10 | `test_migrations.py` (0012), `test_analytics.py::test_openapi_documents_analytics_contract`, полный unit + integration прогон |
| 16 | frontend loading/empty/error/retry/403/conflict + happy paths | `RulesPage.test.tsx` (14), `DocumentListsPage.test.tsx` (13), `DocumentsTab.test.tsx` (9), `App.auth.test.tsx` (навигация по ролям) |
| 17 | backup и Compose overlays | существующие integration backup-тесты в прогоне; Compose — только CI job `stack` (см. ограничения) |

## Команды и результаты (локально, Linux, Python 3.11, PostgreSQL 16.2)

Backend (`cd backend`):

| Команда | Результат |
|---------|-----------|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 100 files already formatted |
| `mypy app tests` | Success: no issues found in 87 source files |
| `pytest -m "not integration" -q` | **481 passed**, 93 deselected (baseline 458) |
| `pytest -m integration -q` (PostgreSQL) | **93 passed**, 481 deselected (baseline 86) |
| `pytest tests/test_analytics.py -k openapi` | 1 passed (OpenAPI-контракт) |
| Migration cycle 0011↔0012 (см. выше) | OK |

Frontend (`cd frontend`):

| Команда | Результат |
|---------|-----------|
| `npm ci` | OK |
| `npm run lint` | 0 problems |
| `npm run typecheck` | OK |
| `npm test -- --run` | **21 files, 163 passed** (baseline 126) |
| `npm run build` | built (337 kB JS / 96 kB gzip) |
| `npm audit --audit-level=high` | 0 vulnerabilities |

Не выполнено локально (честно): `docker compose … config -q` для dev/prod
overlay — Docker недоступен в песочнице (нет доступа к apt/docker hub).
Эквивалентная проверка выполняется CI job **«Compose stack smoke test»**
на итоговом SHA — ссылка в разделе «PR и CI».

Python 3.12 (как в CI) локально установить не удалось (блокировка TLS к
релизам); код не использует 3.12-only синтаксис, CI job `backend` на 3.12
— источник истины.

## Ограничения и решения

- **`test_integration_worker.py::test_end_to_end_event_to_notification`
  был time-dependent** и до этой ветки: он запускает in-app доставку по
  реальным часам, а системные quiet hours по умолчанию — 21:00–08:00
  Europe/Moscow, поэтому при запуске CI/локально в этом окне worker
  *корректно* откладывал уведомление и тест падал (воспроизведено на чистом
  baseline `1a5d029` без изменений Phase 11). Исправлено в тесте: у
  получателя явно создаются настройки с выключенными quiet hours
  (`start == end`) и всеми рабочими днями. Поведение приложения не менялось.
- Правила не имеют «предпросмотра» и dry-run — вне scope.
- `documents_missing_due` считает «дни после применения» в часовой зоне
  владельца по локальной дате (`local_day` в dedupe-ключе) — одно
  напоминание в день на список; повторное применение (replace) начинает
  отсчёт заново.
- Ключ элемента списка генерируется на клиенте транслитерацией только как
  удобство; сервер валидирует формат и уникальность независимо.
- Frontend навигация — не граница безопасности: все проверки повторяет
  backend (HR/manager без grant получат 403/404 от API).

## Безопасность

- Recipient, текст, канал и названия документов в сообщениях — только
  сервер; клиент передаёт лишь тип сообщения, опциональный канал и
  idempotency key.
- Consent/канал/актуальность снимка/отмена перепроверяются непосредственно
  перед provider call; `accepted` ≠ доставлено/прочитано.
- Аудит, история срабатываний, логи и классы ошибок содержат только
  идентификаторы/ключи/счётчики — без ФИО, контактов, текста сообщений,
  токенов и текста исключений.
- Optimistic concurrency на всех изменяемых сущностях (list `version`,
  version `row_version`, item `version`, rule `version`), блокировки строк
  на критических путях и уникальные/partial индексы PostgreSQL как
  последний барьер.
- IDOR: чужие кандидаты/правила/списки → 404; admin без grant → 403; soft
  delete везде, каскадов на историю нет.

## Handoff

- Следующий шаг — review и merge PR владельцем; после merge обновить
  `docs/CURRENT_STATUS.md` (merge SHA) и `agents.md`.
- Кандидаты на следующую фазу по `ROADMAP.md` — не входят в этот PR.

## PR и CI

- **PR:** _(заполняется после создания)_
- **Final SHA:** _(заполняется после push)_
- **CI:** _(ссылки на jobs backend / integration / frontend / stack на final SHA)_
