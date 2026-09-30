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
| `7a70c4e` | — | The rework prompt itself |
| `b52eec1` | fix | Migration `0017` repaired; the two red CI jobs closed |
| `7d8148e` | E′ | `.docx` import, PDF refused with an instruction |
| `70d5ee8` | B′/C′ | Recovery in the assignee picker; reminders in the candidate card |

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

## Rework of 2026-09-30 (PR #45 review)

Four further commits on top of the five blocks, in response to
`prompts/UX_REWORK_PROMPT_PR45.md`.

### The red CI jobs — and a correction

The review note reported «Backend checks» and «Backend integration tests
(PostgreSQL)» as failing, and attributed the second to `HEAD_REVISION` still
saying `0016`. **That diagnosis was wrong**, and following it literally would
have left the real bug in place:

* the failing step is `alembic upgrade head`, which never reads
  `HEAD_REVISION`. The assertion that compares it lives in the *next* step,
  which was **skipped** because the migration before it aborted;
* `main` (`87e4414`) is green, so this was a regression introduced here, and
  it made migration `0017` unappliable on every supported server.

The actual cause was one line of SQL. The duplicate-normalisation step ran
`SELECT MIN(id) FROM reminders GROUP BY event_id`, and `reminders.id` is a
UUID. `min(uuid)`/`max(uuid)` were only added in **PostgreSQL 20**; CI runs
`postgres:16-alpine`, where the aggregate does not exist — so `alembic upgrade
head` aborted before creating the index and every integration test was
skipped. The one statement that could not be executed in this environment was
exactly the one CI runs on every push.

It is now `DISTINCT ON (event_id) ... ORDER BY event_id, created_at, id`,
which needs only the btree ordering `uuid` has had since 8.3 — and is *more*
correct, because with random v4 UUIDs `MIN(id)` selected an arbitrary row
while `created_at` keeps the genuinely oldest link, which is what the
migration's own comment always claimed.

`HEAD_REVISION` was genuinely stale and is bumped to `0017`, and
`test_head_revision_matches_the_migration_chain` now runs `alembic heads` in a
subprocess to compare. It needs no database, so the drift fails in the fast
job instead of only in the PostgreSQL one, and it pins the chain to a single
head. Both regression guards were verified by re-introducing the defect and
confirming the test fails.

**A third job was red and is not in the review note:** «Frontend checks»,
on its `Dependency audit (high and above)` step — `undici` and
`brace-expansion`. ESLint, typecheck, unit tests and the production build all
passed. `frontend/package.json` and `package-lock.json` are **byte-identical
to `main`**, and `npm audit` reports the same 2 high findings on `main`, so
this is pre-existing and unrelated to this PR. It is left alone on purpose:
`npm audit fix` would move the lockfile, which is a separate decision for the
owner, not a drive-by change inside a UX PR.

### `.docx` and `.pdf` (P1)

`SUPPORTED_EXTENSIONS` now accepts `.docx`. A `.docx` is a ZIP, so this is
the one place untrusted compressed bytes are expanded and where a zip-bomb
would live: entry count, per-entry size, total size and compression ratio are
all capped *before* a byte is inflated, only `word/document.xml` is ever
opened, and a DOCTYPE is refused outright. Verified against real archives — a
100 MB entry compressing to 100 KB (1027:1) is refused on a ~100 KB upload, a
3 MB entry in 3 KB trips the ratio cap, 3000 parts are refused.
`MAX_UPLOAD_BYTES` stays at 512 KiB.

**PDF is refused on purpose.** A PDF describes a page, not text; without a
rendering engine, extraction means guessing at font encodings and content
streams, and a garbled legal form is worse than no template because it looks
authoritative. The refusal names the two ways out (save as `.docx` in Word, or
copy the text into `.txt`) and the form repeats it before the upload.

### Recovery actions and the candidate card (P2)

The assignee picker no longer dead-ends: «Повторить» re-requests after a
failed load (asserted by call count, not just by the message disappearing), and
an empty directory states the *real* predicate — no active user with role HR
and no pilot full-access account — with «Открыть «Пользователи»» for an admin
and «ask an administrator» for anyone else, plus «Обновить» in both cases.

`GET /reminders` gained the `candidate_id` filter the review note believed
already existed (it did not — those lines were in the create handler). It is
applied *after* the owner/assignee scope, so naming an inaccessible candidate
returns an empty page rather than a signal that something exists; two backend
tests pin both halves. The card's «События» tab now lists the candidate's
reminders with status and «· из события».

## Test results

Every figure below is from this run, on the final tree. Commands are exactly
what CI runs.

| Check | Command | Result |
| --- | --- | --- |
| Backend lint | `cd backend && ruff check .` | All checks passed |
| Backend format | `cd backend && ruff format --check .` | 155 files already formatted |
| Backend types | `cd backend && mypy app tests` | Success: no issues found in 137 source files |
| Backend unit | `cd backend && pytest -m "not integration" -q` | **999 passed**, 114 deselected |
| Frontend lint | `cd frontend && npm run lint` | clean (0 errors, 0 warnings) |
| Frontend types | `cd frontend && npm run typecheck` | clean |
| Frontend unit | `cd frontend && npx vitest run` | **301 passed** in 28 files |
| Frontend build | `cd frontend && npm run build` | `✓ built in 1.63s` |

The 114 deselected are the PostgreSQL-backed tests; see the limitations
below. Before the rework the same commands gave 978 and 294, so the rework
added 21 backend and 7 frontend tests without losing any.

### CI on head `017c36d`

GitHub Actions runs the PostgreSQL job, so it closes what the local
environment could not:

| Job | Before | Now |
| --- | --- | --- |
| Backend checks | failure | **success** |
| Backend integration tests (PostgreSQL) | failure | **success** |
| Frontend checks | failure | failure — `npm audit` only, see above |
| Windows engine tests + installer smoke | success | success |
| Release pipeline fail-closed policy | success | success |
| License issuer bundle (PS 5.1) | success | success |
| Compose stack smoke test | skipped | skipped |

Inside the PostgreSQL job every step is green, including
`alembic upgrade head`, the migration pipeline tests
(`upgrade → downgrade base → upgrade` and `upgrade → upgrade → downgrade -1 →
upgrade`) and the encrypted backup/restore drill. So `0017` does apply and
round-trip on a real PostgreSQL 16 — the thing this sandbox could not check.

## A note on the branch history

The five block commits are the ones under review, unchanged. During the
rework the sandbox's snapshot mechanism garbage-collected the earlier local
commits, which silently flattened blocks B–F into the rework commits. That
was caught by inspecting `git log` against the remote, and the work was
re-split: the branch was reset back to `f7e0f7d` and the rework was re-committed
as four separate commits on top. The re-split was verified byte-for-byte
identical to the tree before the reset (`diff -rq` over the whole workspace,
excluding `.git`, `venv`, `node_modules`, `__pycache__`, `dist` — no
differences), so nothing was lost or altered in the process.

If a future session sees the same symptom — a local history shorter than the
remote's — check `git log --oneline FETCH_HEAD` after a `git fetch` before
assuming the work is gone. The files are the source of truth, not the local
reflog.

## What is still missing — the release stays NO-GO

1. **The Compose stack was never started.** This environment has neither
   `docker` nor `docker compose`, so `infra/compose.pilot.yml` could not be
   brought up. Every P0 scenario — admin/manager/HR walking through the
   calendar, the funnel and «Мои правила» against a real PostgreSQL — is
   therefore **unverified end-to-end**. The unit and integration tests use
   SQLite and a mocked API.
2. ~~**Migration `0017` has never been applied to a real PostgreSQL.**~~ —
   **resolved by CI.** This was the highest-risk item when the rework was
   written, because the fix had been derived from the PostgreSQL release
   history rather than executed: there is no `docker` and no local server in
   the agent environment. GitHub Actions runs the real thing, and on head
   `017c36d` «Backend integration tests (PostgreSQL)» is **success**, with
   every step green — `alembic upgrade head`, the migration pipeline tests
   (`upgrade → downgrade base → upgrade`, and `upgrade → upgrade →
   downgrade -1 → upgrade`), and the encrypted backup/restore drill. The
   114 previously-skipped tests are no longer skipped there.
   *Still worth doing by hand on a copy before the pilot: confirm the partial
   index is present and the duplicate-normalisation behaves on a database
   that already holds several reminders for one event.*
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
#    This is the command that was broken and is now fixed; it MUST be run
#    against real PostgreSQL before trusting anything else here.
cd backend
alembic upgrade head
alembic downgrade -1 && alembic upgrade head   # 0017 must round-trip
#    Confirm 0017 and that the partial index exists:
#      SELECT version_num FROM alembic_version;
#      \d reminders   ->  Index "uq_reminders_event_id" UNIQUE, WHERE event_id IS NOT NULL

# 3. Restart the API and walk the P0 scenarios as each role:
#    * admin  — create a calendar event, attach a reminder, pick a candidate
#               by ФИО/телефон/email, confirm it appears in «Напоминания»;
#    * manager— open the same event; the assignee list must NOT be empty and
#               must not contain an inactive HR;
#    * HR     — see only the assignable HR and your own candidates; a foreign
#               candidate must be a 404, not a 403.
#
# 4. Block E: «Загрузить из файла» with a .md → draft; with a .docx → the
#    text only (paragraphs as line breaks, placeholders preserved); with a
#    .pdf → the instruction to save as .docx; with a renamed .txt → refused.
#    Check the audit row contains the format and not the text.
# 5. Block F: open «Настройки», follow each group, then paste an old link
#    such as #/documents into the address bar.
```

Backend tests with PostgreSQL (the 114 skips) become live once
`TEST_DATABASE_URL` is set; the 33 import tests and the event/reminder tests
also run there.
