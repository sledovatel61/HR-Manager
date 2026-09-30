# UX feedback 2026-09-29 — handoff (blocks B–F)

Baseline for this work: `agents.md` states the release is **NO-GO** until a
written GO and production evidence exist. Nothing in this handoff changes
that. See «Что осталось невыполненным» at the end.

Source of truth for the order and the wording of the work:
`prompts/UX_FEEDBACK_AGENT_PROMPT_2026-09-29.md` and
`docs/UX_FEEDBACK_TASK_2026-09-29.md`.

## What was delivered

Five independent commits on top of `main` (`87e4414`), each with its own
tests. They are deliberately separable so a reviewer can take one block at a
time; the API is additive everywhere except migration `0017`, which adds a
table and an index without touching existing columns.

| Commit | Block | Subject |
| --- | --- | --- |
| `668a3bc` | B | One event ↔ reminder ↔ candidate link; assignable HR for admin/manager |
| `76b0d27` | C | «Воронка кандидатов» and one-action stage moves |
| `89b8b95` | D | Document-list editor as a three-step wizard |
| `04a7434` | E | Import methodical material from files, in plain language |
| `4b0d6ab` | F | Set-up-once sections gathered into «Настройки» |

## Block B — events, reminders, assignees (P0)

The three problems in the feedback shared one root cause: an event, its
reminder and its candidate were unrelated records, so «did this reminder come
from that event?» was unanswerable.

* `backend/app/event_reminders.py` links event → reminder → candidate (now
  optional) → responsible HR, and keeps timezone, status and history.
  `backend/alembic/versions/0017_event_reminder_link.py` adds the table plus a
  **partial unique index** on the event: one event cannot grow two reminders.
* `backend/app/assignees.py` answers «which HR can be assigned?» for admin
  **and** manager. The previous answer came from the caller's own team, so a
  manager with an active team outside their own scope saw an empty dropdown
  and no way forward. When no eligible HR exists the API returns the creation
  path rather than a blank select.
* Reminder date/time are editable and clearable, validated against the event
  start, and interpreted in the stored timezone with a local-format reply.
* «Напоминания» gets a server-side candidate picker filtered by full name,
  phone and email, with per-candidate access re-checked — so it can neither
  leak a foreign candidate nor fall back to a client-side guess.
* Tests: `backend/tests/test_event_reminder_link.py` (happy path, refusals,
  rights, timezone, page reload), `backend/tests/test_assignees.py`.
  Frontend: `frontend/src/features/notifications/RemindersPage.test.tsx`,
  `frontend/src/features/calendar/EventFormModal.test.tsx`.

## Block C — the funnel (P1)

* «Kanban» is now «Воронка кандидатов» in the shell and in the board.
* Any column offers «Перенести в этап…», so first → last is one action.
* Drag still autoscrolls near an edge (`boardScroll.edgeScrollDirection`,
  pure and unit-tested).
* Mouse, keyboard, optimistic update with rollback, server-side stage check:
  `frontend/src/features/candidates/KanbanPage.test.tsx` — 11 tests.

## Block D — document lists (P1)

* The editor is a three-step wizard: «Для чего список?» → «Какие документы
  нужны?» → preview and save.
* Technical keys are generated once per row (`itemKey.autoKey`), kept unique,
  and moved under «Расширенные сведения». A key is **never** regenerated from a
  later rename: candidate document state is stored by key, so a rename must not
  orphan a document already attached to someone.
* Saving produces a *draft*; publishing is a separate, visible action. The
  list view leads with the current version and a «Что увидит сотрудник»
  preview.
* «Мои правила» keeps a real empty state with a link to create a list, and
  re-checks rights, publication and version on the server.
* Tests: `frontend/src/features/documents/Documents.test.tsx` — 60 tests.

## Block E — templates and the methodical base (P1)

`POST /document-templates/import` + `backend/app/template_import.py`:

* **Formats** — a closed list (`.txt`, `.md`, `.markdown`, `.csv`). A `.docx`
  or `.pdf` is refused with a message naming what *is* accepted, rather than
  half-parsed into nonsense.
* **Size** — 512 KiB, enforced *while* reading, so an oversized upload is
  discarded early instead of fully buffered.
* **Content** — strict UTF-8 (BOM tolerated); a NUL byte or mostly
  replacement characters means «this is not text»; control characters are
  stripped; the result still goes through the existing body validator, so a
  file cannot smuggle in a token outside the allowlist.
* **Naming** — the file name is only a *suggestion* for the title: reduced to
  its last path segment, separators stripped, never used as a path and never
  written to disk. Only the validated text becomes the body.
* **Audit** — the normal creation event carries the format and the safe
  display name; the file contents never enter the audit trail.
* **Rights** — one `require_manage` call, so ordinary HR gets 403 and a
  holder of `document_lists_manage` gets 201.

Frontend: the kind list leads with Документ / Чек-лист / Вопросник для
интервью / Памятка HR / Скрипт (existing keys untouched, so nothing in the
database re-renders under a new name); placeholder chips show «ФИО
кандидата» instead of `{{ candidate.full_name }}`, with the token in the
tooltip and a «Как это выглядит в тексте шаблона» disclosure; a legal-form
note tells the HR that the software cannot check whether an offer is current.
The page no longer claims «Файлы не загружаются».

Tests: `backend/tests/test_template_import.py` (33) and the Block E section of
`frontend/src/features/document-templates/Templates.test.tsx`.

## Block F — navigation (P1)

* Daily sections stay in the main panel: queue, candidates, calendar, funnel,
  schedule, deleted, analytics, notifications, reminders.
* «Настройки» groups the rest: Уведомления, Мои правила, Обновления,
  Лицензия, Администрирование, Пользователи, Интеграции, Контент и
  документы. Each card says what the section is *for*, not just what it is
  called.
* **Hiding a link is not a permission.** Group visibility is derived from the
  same `sectionsForRole` list the sidebar uses, so the two cannot drift, and
  a bookmarked `#/admin` still lands on the admin page — where the server
  decides. Tests assert HR gets no link to Администрирование, Лицензия,
  Обновления or Пользователи, while a direct link still reaches the section.
* **No broken links.** Every group points at existing section hashes;
  `#/readiness` still redirects into the diagnostics tab.
* A coverage test fails if a section is moved out of the sidebar without a
  group to receive it.

## Test results

Run on the final tree (identical content to the five commits above).

| Check | Result |
| --- | --- |
| `backend` `pytest -q` | **978 passed**, 114 skipped |
| `backend` `ruff check app tests` | clean |
| `backend` `ruff format --check app tests` | clean |
| `frontend` `vitest run` | **294 passed** in 28 files |
| `frontend` `npm run typecheck` | clean |
| `frontend` `npm run lint` | clean (0 errors, 0 warnings) |
| `frontend` `npm run build` | `✓ built in 1.42s` |

The 114 skips are the PostgreSQL-backed tests; see the limitations below.

## What is still missing — the release stays NO-GO

1. **The Compose stack was never started.** This environment has neither
   `docker` nor `docker compose`, so `infra/compose.pilot.yml` could not be
   brought up. Every P0 scenario — admin/manager/HR walking through the
   calendar, the funnel and «Мои правила» against a real PostgreSQL — is
   therefore **unverified end-to-end**. The unit and integration tests use
   SQLite and a mocked API.
2. **Migration `0017` was not applied to a real PostgreSQL.** Its structure
   is asserted by the structural tests, and the partial unique index is
   exercised on SQLite, but `alembic upgrade head` / `downgrade` against
   PostgreSQL has not been run. Do that first, on a copy.
3. **The methodical material in `LLM\23.09.2026` was not inventoried.** It
   is on the user's Windows machine and was not reachable from here, so no
   file format, size or personal-data check was possible. The import endpoint
   was built for exactly that inventory, and deliberately **does not**
   auto-import anything: the HR chooses each file, sees what will be created,
   and reviews the draft.
4. **`review-artifacts/ux-feedback-2026-09-29/` contains no screenshots.**
   Nothing in this work claims to reproduce a specific screenshot; where the
   brief described a symptom, the behaviour was fixed and covered by a test.
5. **No production evidence, no written GO, no release workflow.** Nothing was
   tagged, signed, published or purged, and no `production` workflow was
   triggered.

## How to verify this by hand

```bash
# 1. Bring up the stack (the step that could not be done here).
docker compose -f infra/compose.pilot.yml up -d

# 2. Apply migrations on a COPY first, and check the new table.
cd backend
alembic upgrade head
alembic downgrade -1 && alembic upgrade head   # 0017 must round-trip

# 3. Restart the API and walk the P0 scenarios as each role:
#    * admin  — create a calendar event, attach a reminder, pick a candidate
#               by ФИО/телефон/email, confirm it appears in «Напоминания»;
#    * manager— open the same event; the assignee list must NOT be empty and
#               must not contain an inactive HR;
#    * HR     — see only the assignable HR and your own candidates; a foreign
#               candidate must be a 404, not a 403.
#
# 4. Block E: «Загрузить из файла» with a .md → draft; with a .docx → a clear
#    refusal; check the audit row contains the format and not the text.
# 5. Block F: open «Настройки», follow each group, then paste an old link
#    such as #/documents into the address bar.
```

Backend tests with PostgreSQL (the 114 skips) become live once
`TEST_DATABASE_URL` is set; the 33 import tests and the event/reminder tests
also run there.
