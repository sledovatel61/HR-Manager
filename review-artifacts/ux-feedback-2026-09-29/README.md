# Артефакты разбора UX-замечаний 2026-09-29

Отчёт по результатам: `docs/ux-feedback-report-2026-09-29.md`
Handoff (что закрывать дальше): `docs/ux-feedback-handoff-2026-09-29.md`
Ветка: `arena/01a0edff-hr-manager` (коммиты `bda4d2b`, `5298eac`,
`f209af7`, `3a65b63`).

## Доказательства проверок (детерминированные, 29.09.2026)

```
$ cd backend && .venv/bin/python -m pytest tests/
939 passed, 114 skipped, 44 warnings in 247.45s   # база: 924 passed, регрессий нет

$ .venv/bin/mypy app
Success: no issues found in 65 source files

$ .venv/bin/ruff check app tests
All checks passed!

$ cd frontend && npx vitest run
Test Files  29 passed (29)     # база: 266 passed
Tests       282 passed (282)

$ npm run typecheck   # tsc -b — clean
$ npm run lint        # eslint . — clean
$ npm run build       # vite build — success (index 425.21 kB / gzip 117.95 kB)
```

Новые тесты по блокам:

- A+B: `backend/tests/test_event_reminder_link.py` (14),
  `frontend/src/features/calendar/EventFormModal.test.tsx` (+3),
  `frontend/src/features/candidates/CandidateDrawer.test.tsx` (+4),
  `frontend/src/features/notifications/RemindersPage.test.tsx` (+4);
- F: `backend/tests/test_documents.py::test_rule_server_revalidates_publication_stage_and_grant`,
  `frontend/src/features/documents/Documents.test.tsx` (+2);
- C: `frontend/src/features/candidates/KanbanPage.test.tsx` (+3 — перенос
  в первую/среднюю/последнюю колонку);
- G: `frontend/src/features/settings/SettingsPage.test.tsx` (5),
  `frontend/src/app-shell/Workspace.test.tsx` (обновлён под новую модель).

## Чего в артефактах НЕТ и почему

- Скриншоты/веб-артефакты интерфейса: не создавались — в среде нет
  Docker Compose/PostgreSQL для запуска приложения, фиктивные
  доказательства запрещены. Ручная проверка happy path перенесена в
  чек-лист приёмки (см. handoff).
- Импортированные данные из `LLM\23.09.2026`: автоимпорт запрещён ТЗ;
  материалы использовались только как контекст при чтении.
