# Отчёт Phase 11 — списки документов и правила автоматизации (agent)

- **Ветка:** `arena/phase-11-document-rules-a1b2c3`
- **Baseline SHA:** `1a5d0291780a8e41964ff9e47de8e8f09983544f`
- **Final SHA:** `8df0c5c` (feat-коммит)
- **Миграционный head:** `0012`

## Что сделано

### 1. Версионируемые списки документов

- **Модель**: `document_lists` (stable_key, name, description, scope, author),
  `document_list_versions` (version_number, status: draft/published/archived),
  `document_list_items` (item_key, name, explanation, is_required, sort_order).
- **API**: CRUD списков, создание новых черновых версий (копируют элементы из
  предыдущей), публикация (атомарно архивирует предыдущую published), архивирование.
- **Иммутабельность**: редактирование опубликованной версии запрещено (409).
- **Аудит**: `document_list_created`, `document_list_version_created`,
  `document_list_version_published`, `document_list_version_archived`.

### 2. Документы кандидата

- **Применение**: `POST /document-lists/apply/{candidate_id}` — создаёт
  неизменяемый снимок точной версии. Повторное применение помечает старый
  assignment как `replaced_at`.
- **Элементы**: `candidate_document_items` со статусами `missing`/`received`,
  `changed_by_user_id`, `changed_at`, `version` (optimistic concurrency).
- **Недостающие**: `GET /document-lists/candidate/{id}/missing` — только
  required + missing, в стабильном порядке.
- **Безопасность**: HR видит только своих кандидатов, soft-deleted — 404.
- **Аудит**: `document_list_applied`, `document_item_status_changed`,
  `document_list_replaced`.

### 3. Правила автоматизации

- **Конструктор**: `automation_rules` с закрытыми типами:
  - Триггеры: `stage_transition`, `document_reminder_schedule`
  - Действия: `apply_document_list`, `send_document_request`,
    `send_document_reminder`
  - Условия (опционально): `stage`, `has_missing_required`, `channel`
- **Принадлежность**: правило принадлежит одному пользователю; видны только
  свои правила.
- **Версионирование**: optimistic concurrency (`version`).
- **Включение/выключение**: `PATCH /{id}/toggle`.
- **История**: `automation_rule_executions` (immutable, rule_version,
  trigger_object_id/version, candidate_id, action_type, outcome, dedupe_key;
  без PII).
- **Валидация**: отклонение произвольных типов триггеров/действий (закрытый
  словарь, без eval/SQL/regex).
- **Аудит**: `automation_rule_created/updated/toggled/deleted`.

## Миграция 0012

7 таблиц:

| Таблица | Назначение |
|---|---|
| `document_lists` | Стабильные идентификаторы списков |
| `document_list_versions` | Версии (draft → published → archived) |
| `document_list_items` | Элементы внутри версии |
| `candidate_document_assignments` | Снимок версии у кандидата |
| `candidate_document_items` | Per-item статус (missing/received) + optimistic version |
| `automation_rules` | Правила с закрытыми trigger/action типами |
| `automation_rule_executions` | Неизменяемая история срабатываний |

Все FK/index/constraints имеют стабильные имена. Миграция обратима.

## Проверки

```bash
cd backend
ruff check app tests          # All checks passed!
ruff format --check app tests # 81 files already formatted
pytest -m "not integration" -q # 482 passed, 86 deselected
```

Все 482 теста проходят (458 существующих + 24 новых). Нет регрессий фаз 0–10.

## Изменённые файлы

| Файл | Тип |
|---|---|
| `backend/alembic/versions/0012_document_lists_and_rules.py` | Новый |
| `backend/app/models.py` | Изменён (7 новых моделей + 12 audit actions) |
| `backend/app/schemas.py` | Изменён (Pydantic schemas для всех новых API) |
| `backend/app/main.py` | Изменён (2 новых роутера) |
| `backend/app/routers/document_lists.py` | Новый (API списков документов) |
| `backend/app/routers/automation_rules.py` | Новый (API правил) |
| `backend/tests/test_document_lists.py` | Новый (24 теста) |

## Ограничения

- Phase 11 учитывает только факт получения документа (received/missing).
  Загрузка файлов, сканы, паспортные данные не входят.
- Worker-интеграция правил (фактическое срабатывание при смене этапа)
  реализована как структура данных; полная обработка бизнес-событий через
  правила — в следующем этапе.
- Frontend UI (страницы «Списки документов», «Документы кандидата»,
  «Мои правила») не реализованы в этом этапе (backend-first).
- Интеграционные тесты (PostgreSQL) и Compose smoke — делегированы CI.

## Handoff

- Миграционный head: `0012`.
- Новые таблицы: 7 (см. выше).
- Точки расширения: `RuleTriggerType`, `RuleActionType` (закрытые словари
  в models.py), роутеры `/document-lists` и `/automation-rules`.
- Для следующего этапа: реализация frontend UI, worker-интеграция правил
  (срабатывание при stage_transition), интеграционные тесты на PostgreSQL.
