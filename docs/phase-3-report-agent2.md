# Отчёт по этапу 3 — единая база кандидатов (агент 2)

- **Промпт:** `prompts/PHASE_3_PROMPT.md` (роадмап: этап 2 «Единая база кандидатов»).
- **Ветка:** `arena/phase-3-candidates` (опубликована, **без merge**).
- **PR для ревью:** https://github.com/sledovatel61/HR-Manager/pull/4
- **Коммит:** `dbbaf76839551ba9bcc09fd82e9ed71ea228d374`
  (полный хеш из `git rev-parse HEAD` на момент публикации ветки).
- **База ветки:** фактический `origin/main` = `62838a92441a50da28c0088f6f930b1b8cb5a4a6`
  («docs: add candidates database phase prompt and update README»).
  ⚠️ В промпте ожидался base `0fc51161b0e6fba64c9e807ded19af35f8db63f3` —
  фактический main на момент старта был новее (содержит полный этап 2,
  дизайн-трек и CI), поэтому ветка создана от `62838a9`.
- **Дата:** 2026-09-02.

## Что сделано

### Backend

- **Модели** (`backend/app/models.py`):
  - `CandidateStage` — 11 стадий из PRODUCT_SPEC §5 (единый словарь):
    `new, contacted, reached, interview_scheduled, interview_done, offer,
    hired, started («вышел»), probation, fired, rejected`;
    `CANDIDATE_STAGE_ORDER` / `CANDIDATE_STAGE_POSITION` — порядок воронки.
    ⚠️ В дизайн-прототипе `started` отсутствует — добавлен по §5 спецификации
    между `hired` и `probation`.
  - `CandidateSource` — 7 источников (`site, referral, hh_manual,
    university, event, agency, inbound_call`).
  - `CandidateInteractionType` — 5 типов (`call, email, meeting, note,
    status_change`; `transfer` — следующий этап, исключён промптом).
  - `Candidate` (включая `full_name_normalized`, `phone_normalized`,
    `email_normalized`, `stage_position`, мягкое удаление
    `deleted_at`/`deleted_by_user_id`) и `CandidateInteraction`;
    `AuditEvent.candidate_id` (FK SET NULL).
- **Миграция** `backend/alembic/versions/0003_candidates.py` — обратимая
  (upgrade/downgrade/re-upgrade проверены на PG 18.4 в песочнице и PG 16 в
  CI), CHECK-ограничения на стадию/источник/тип, индексы, PG-native
  UUID/timestamptz.
- **Роутер** `backend/app/routers/candidates.py`:
  - `GET /candidates` — серверный поиск (ФИО, нормализованные телефон/email;
    регистронезависимо, кириллица корректна на обеих БД благодаря
    Python-casefold), фильтры `stage`/`source`/`owner_id`, сортировка
    (`created_at|updated_at|full_name|stage` — стадия сортируется по
    `stage_position`), `direction`, `limit ≤ 100`, `offset`.
  - `POST /candidates`, `GET/PATCH/DELETE /candidates/{id}`,
    `POST /candidates/{id}/restore`, `GET/POST /candidates/{id}/interactions`.
  - **Права:** HR — только свои кандидаты (чужие и мягко удалённые → 404,
    чтобы не раскрывать существование; `owner_id` игнорируется);
    manager/admin — все + фильтр по владельцу; создание: HR — только себе
    (иначе 403), manager/admin — любому активному пользователю;
    `owner_user_id` необязателен (по умолчанию — создатель).
  - **Дубликаты** (PRODUCT_SPEC §4): сравнение по нормализованным
    телефону/email → 409 `{message, duplicates}`; повтор с
    `confirm_duplicate=true` создаёт/изменяет и пишет
    `duplicate_candidate_created`. Поиск дублей ограничен видимостью
    пользователя — HR не узнаёт через 409 о кандидатах коллег.
  - **Аудит:** `candidate_created/updated/stage_changed/deleted/restored/
    duplicate_candidate_created/interaction_added` в существующую
    `audit_log` со связью `candidate_id`; в `details` — только
    id/стадии/источники/типы, **без** персональных данных.
  - CSRF (`X-CSRF-Token`) на всех мутирующих методах — как в этапе 2.
- **Прочее:** `record_event` принимает `candidate_id`;
  `AuditEventOut.candidate_id` в API аудита; `email-validator` добавлен в
  `pyproject.toml`/`requirements.txt` (EmailStr в схемах).

### Frontend (только типы и API-клиент — экраны по промпту исключены)

- `frontend/src/types.ts`: `CandidateStage` + `CANDIDATE_STAGE_ORDER` +
  `STAGE_LABELS`, `CandidateSource` + `SOURCE_LABELS`,
  `CandidateInteractionType`, `Candidate`, `CandidateInteraction`,
  `CandidateListQuery`, `CandidateCreateInput`/`CandidateUpdateInput`,
  `CandidateInteractionCreateInput`, `DuplicateCandidateDetail`;
  `AuditEvent.candidate_id`.
- `frontend/src/api.ts`: `listCandidates/getCandidate/createCandidate/
  updateCandidate/deleteCandidate/restoreCandidate/
  listCandidateInteractions/createCandidateInteraction`; `ApiError.rawDetail`
  + типизированный `DuplicateCandidateError` (409 → структурированные
  совпадения для будущего UX-подтверждения).
- Тесты в `frontend/src/api.test.ts` (+4 сценария).

### Документация

- `README.md`: статус этапа 3, таблица endpoint'ов, параметры
  `/candidates`, правила дубликатов и словарь стадий.
- `docs/ARCHITECTURE.md`: раздел «База кандидатов (этап 3)» — модель,
  нормализация в Python, права, дубликаты, аудит.

## Изменённые файлы

```
README.md
backend/alembic/versions/0003_candidates.py        (новый)
backend/app/audit.py
backend/app/main.py
backend/app/models.py
backend/app/routers/candidates.py                  (новый)
backend/app/schemas.py
backend/app/utils.py
backend/pyproject.toml
backend/requirements.txt
backend/tests/conftest.py
backend/tests/test_candidates.py                   (новый)
backend/tests/test_integration_candidates.py       (новый)
backend/tests/test_migrations.py
docs/ARCHITECTURE.md
docs/phase-3-report-agent2.md                      (этот файл)
frontend/src/api.test.ts
frontend/src/api.ts
frontend/src/types.ts
```

`.github/` не изменялся ни в одном коммите ветки.

## Фактически выполненные проверки

### Локально (песочница)

| Проверка | Результат |
|---|---|
| Backend `ruff check` + `ruff format --check` | ✅ чисто |
| Backend `mypy app tests` | ✅ 31 файл без ошибок |
| Backend `pytest` (unit SQLite + integration) | ✅ **122 passed** |
| В т.ч. integration на PostgreSQL **18.4** (embedded) | ✅ 12 integration-тестов |
| В т.ч. цикл alembic upgrade→downgrade→re-upgrade + идемпотентность | ✅ (в `test_migrations`) |
| Ручной цикл `alembic upgrade head → downgrade 0002 → upgrade head` на PG 18.4 | ✅ |
| Frontend `npm run typecheck` (`tsc -b`) | ✅ |
| Frontend `npm run lint` (eslint) | ✅ |
| Frontend `npm run test` (vitest) | ✅ **15 passed** |
| Frontend `npm run build` (production) | ✅ |
| Compose-стек статически: PyYAML-парсинг dev+prod с `!reset`, `tests/test_production_overlay.py` (6 passed), `make -n up` | ✅ |

### Live smoke (uvicorn + PostgreSQL 18.4, разработческая БД)

`/health` → 200 ok; bootstrap-админ → вход; создание HR-пользователей;
создание кандидата (owner по умолчанию); поиск по кириллице
(`query=СМИРНОВ`); дубль «8 901…» vs «+7 901…» → 409 с 1 совпадением,
`confirm_duplicate=true` → 201; смена стадии → 200 + аудит; взаимодействие →
201; мягкое удаление → исключение из списков; восстановление → 200;
HR2: чужие списки пусты, чужой кандидат → 404; admin видит все; в
`/admin/audit` события привязаны к кандидату через `candidate_id`, PII в
`details` отсутствует. ✅ SMOKE OK.

### GitHub Actions (реальное выполнение)

PR #4 запустил CI: run https://github.com/sledovatel61/HR-Manager/actions/runs/33655486574 —
**все 4 джобы прошли**:

| Джоба | Время | Результат |
|---|---|---|
| Backend checks (ruff/mypy/pytest) | 57s | ✅ |
| Backend integration tests (PostgreSQL **16**, alembic upgrade head + `pytest -m integration`) | 53s | ✅ |
| Frontend checks (eslint/tsc/vitest/build) | 30s | ✅ |
| Compose stack smoke test (dev + prod overlay, Docker на раннере) | 1m15s | ✅ |

Единственные аннотации — предупреждения раннера о deprecation Node 20 у
actions/checkout и actions/setup-* (не ошибки).

## Известные ограничения

1. **Перенос владельца** (`POST /candidates/{id}/transfer`) не реализован —
   по промпту относится к следующему этапу; `PATCH` намеренно не меняет
   `owner_user_id` (иначе перенос шёл бы в обход будущей операции и её
   аудита/прав).
2. **Экраны frontend** (таблица/карточка/канбан/корзина/очередь) — следующий
   этап; добавлены только типы и функции API-клиента.
3. **Маскирование PII в ответах API** не выполняется: авторизованные
   пользователи получают телефон/email кандидатов из своей зоны видимости
   (требование этапа — маскировать PII в **логах**/аудите, что выполнено).
   Маскирование для UI (по образцу дизайн-прототипа `phoneMasked`/
   `emailMasked`) — задача экранного этапа.
4. Комментарии взаимодействий пишутся в аудит-событии только типом (`type=`),
   не содержимым.
5. `started` («вышел») отсутствует в `design-prototype/src/types.ts` —
   расхождение дизайн-трека и единого словаря задокументировано; прототип
   по соглашению не правится в рамках backend-этапа.
6. В песочнице не было Docker: полный Compose-запуск выполнен только
   статически локально и фактически — джобой `stack` в GitHub Actions
   (см. выше).
