# Phase 11 — отчёт

- Baseline: `1a5d0291780a8e41964ff9e47de8e8f09983544f` (актуальный `origin/main` на 2026-09-08).
- Final SHA: см. commit этой ветки.
- Migration head: `0012` (reversible).

## Реализовано

Добавлены immutable document-list/list-version/item таблицы со статусами draft,
published и archived, exact candidate snapshot (`candidate_document_sets`),
конкурентный статус документа с `row_version`, безопасные server-side endpoints
и scoped candidate access. Личные правила ограничены закрытыми trigger/action
значениями и bounded reminder days; произвольный код, получатель, текст и
файлы не принимаются. Добавлен русскоязычный frontend-раздел со loading,
empty/error/retry состояниями и keyboard-native buttons. Публикация атомарно
архивирует предыдущую опубликованную версию; опубликованная запись не
редактируется.

## Проверки

- `python -m compileall -q backend/app backend/alembic` — passed.
- `git diff --check` — будет выполнено перед commit.
- `ruff check`, frontend typecheck и полный pytest не выполнены локально:
  зависимости/ruff/Node modules отсутствуют в sandbox (`command not found`).
- PostgreSQL integration, Docker Compose и CI — ожидают GitHub Actions после push.

## Ограничения / handoff

Worker wiring for document request/reminder remains intentionally server-owned
and uses existing Phase 10 message vocabulary; no new broker or provider was
introduced. CI should add/execute integration coverage for PostgreSQL constraints,
concurrent publication, send-time revalidation and worker dedupe before merge.
No secrets, contacts, message body, or document contents are stored.
