# Отчёт Phase 13 — безопасный канал доставки обновлений Windows-пилота (arena)

- **Ветка:** `arena/01a084e4-hr-manager` (сессия Arena закреплена за ней;
  работа начата строго от baseline `origin/phase12/windows-acceptance-final`,
  без продолжения ветки Phase 12 новыми коммитами поверх чужих)
- **Baseline SHA (на старте):** `7343025dcc5f33ea5f6298b014b646cddd0ffdc8`
  (= `phase12/windows-acceptance-final`, локально принятая Phase 12)
- **Final code SHA (адресная доработка по техприёмке):**
  `bc4e9d6226f8994384c97197a5f652d20c2140d3` — полностью зелёный CI
  (run 34570110327,
  https://github.com/sledovatel61/HR-Manager/actions/runs/34570110327);
  финальный docs-коммит поверх — в PR-комментарии. Предыдущий SHA до
  доработки (проверенный типом приёмки): `fdcc029…`.
- **PR:** #23 → `phase12/windows-acceptance-final`
  (https://github.com/sledovatel61/HR-Manager/pull/23)
- **Миграции БД:** новых миграций **нет** (промпт: по умолчанию не
  требуется; состояние канала — server-owned в памяти + host-файлы
  движка, честно возвращается в `idle` после перезапуска)
- **Merge в `main`:** не выполнялся (по правилам — владелец)

## Что сделано

Канал доставки обновлений поверх уже принятого Phase 12 update engine:
администратор видит доступную версию в UI, скачивает пакет (проверенный
подписью и хешами) и запускает существующий update/rollback без ручного
скачивания и распаковки каталога релиза. Локальный
`-Action update -ReleaseDir` полностью совместим.

### 1. Контракт канала (`infra/release/`)

- **Формат:** JSON-манифест, 10 полей (`schema_version=1`, `channel`,
  SemVer `version`, `release_sha` (полный Git commit), `package_url`
  (HTTPS, без query/fragment/userinfo), `package_size`, `package_sha256`,
  `minimum_supported_version`, `published_at`, `notes_ru`) +
  `signature {key_id, scheme: "ed25519", sig: hex-128}`.
- **Канонизация:** фиксированный порядок полей, строки `имя:значение`
  через LF, UTF-8, завершающий перевод строки; `signature` в payload не
  входит; неизвестные поля/повторяющиеся ключи/неверные типы — отказ.
  Golden-байты закреплены в `testdata/manifest.canonical.txt` и
  проверяются независимо Python- и PowerShell-тестами.
- **Криптография:** стандартная **detached Ed25519 (RFC 8032)**. Клиент
  хранит только публичные ключи; подпись делает владелец/CI secret.
  Проверка в Windows PowerShell 5.1 реализована на
  `System.Numerics.BigInteger` + SHA-512 (`engine/Crypto.psm1`, RFC 8032
  §5.1.7) и покрыта эталонным вектором RFC 8032 §7.1 TEST 1 и
  fixture-манифестами, подписанными независимой реализацией Python
  `cryptography` (эталонная проверка — `verify_channel.py`).
- **Fail closed:** отсутствующая/неизвестная/отозванная/неверная подпись,
  несовпадение SHA/размера, downgrade, некорректный SemVer, неизвестная
  версия схемы, HTTP-URL — отказ до распаковки и до изменения установки.
- **Инструменты:** `sign_channel.py` (`--gen-key`, подпись, публичный ключ
  из закрытого), `verify_channel.py` (независимая проверка),
  `build_package.py` (детерминированный zip: сортировка, эпоха ZIP),
  `make_test_fixtures.py` (31 детерминированный fixture: ключи,
  подписанные/негативные манифесты, атакующие пакеты, таблица SemVer).
  Зеркало контракта в контейнере backend: `app/update_channel_contract.py`
  (тест требует побайтового совпадения с источником).

### 2. Загрузка и staging (сервер + host)

- **HTTPS обязателен**; политика хостов (явный список) и лимит redirect
  (5) с проверкой конечной схемы/host; без GitHub token/credentials на
  клиенте; загрузка во временный файл с проверкой объявленного размера
  (потолок 512 МиБ) и SHA256, атомарная публикация в staging; обрыв —
  частичный файл релизом не считается, повторная загрузка безопасна.
- **Staging — bind mount** `HRM_STAGING_DIR:/updates` (host:
  `%LOCALAPPDATA%\HRManagerStaging`, ACL-защищён, **вне** каталога
  секретов и **вне** backup volume). Это единственная host-поверхность
  контейнера: никаких docker.sock/pipe, никакого command runner.
- **Распаковка — только на host** (`Expand-HrmPackage`): Zip Slip,
  absolute/UNC/device paths, ADS (`:`), backslash, symlink/reparse-point
  (unix-mode type bits), лишние корни, блокируемые расширения —
  отказ до записи; после распаковки сверка внутреннего `release.json` с
  манифестом (внутренний и внешний контракты не могут противоречить).
- **Офлайн/недоступность канала не мешают работе** установленного
  приложения (проверено тестами движка и API).

### 3. Версии и политика

- Строгое SemVer (общая таблица `testdata/semver_cases.json` для Python и
  PowerShell); `0.10.0 > 0.9.9`, prerelease по SemVer 2.0.
- same version + same SHA → `up_to_date`; same version + другой SHA →
  конфликт целостности; downgrade по сети запрещён; ниже
  `minimum_supported_version` → `manual_action_required` (без перескока
  цепочки).
- Фоновая проверка троттлится сервером
  (`update_check_min_interval_seconds`, по умолчанию 300 с) с фиксацией
  времени последней успешной проверки; кешируется только успешно
  проверенный подписанный манифест; **фоновая установка невозможна** —
  только явное действие администратора.

### 4. Backend API (`/api/updates/*`, server-owned состояния)

- Состояния: `idle | checking | up_to_date | available | downloading |
  ready | installing | restart_required | failed | manual_action_required`
  (хранилище `app/update_state.py`, инварианты переходов, блокировка
  одновременных действий, идемпотентность).
- Эндпоинты пользователя: `GET /status` (аутентификация + подтверждённый
  scope `pilot_full_access` ИЛИ `update_channel_manage`),
  `POST /check|/download|/install` (роль admin + scope
  `update_channel_manage`; грант никогда не обходит роли; frontend — не
  граница безопасности). CSRF — существующая double-submit проверка;
  rate limit per-user (30/300 c); аудит (`update_check_started/succeeded/
  failed`, `update_download_*`, `update_install_requested`,
  `update_engine_reported`) без URL/путей/секретов.
- Клиент не передаёт URL/пути/команды/манифест/release SHA — всё из
  серверной конфигурации (`UPDATE_CHANNEL_URL`,
  `UPDATE_CHANNEL_PUBLIC_KEYS`, `UPDATE_ENGINE_TOKEN`,
  `UPDATE_STAGING_DIR=/updates` — из `pilot.env`).
- Движок (host): `GET /engine-state`, `POST /engine-check`,
  `POST /engine-report` — только loopback + машинный токен
  `HRM_UPDATE_ENGINE_TOKEN` (без токена — 404; per-IP rate limit;
  команда выдаётся ровно одному опросу).
- Новый scope `update_channel_manage` (enum + Literal схемы грантов);
  миграций нет.

### 5. UI (фронтенд)

Раздел «Обновления» в администрировании (`features/updates/`):
установленная версия + commit, время/итог последней проверки, доступная
версия/дата/release notes, кнопки «Проверить обновления / Скачать /
Установить», состояния loading/offline/error/retry/403/conflict,
предупреждение о бэкапе и кратком перерыве, честные итоги
`updated | rolled_back | restart_required | manual_action_required`
(без универсального «успешно»), опрос статуса во время установки без
фиктивных процентов, `role="status"/"alert"` + `aria-live`,
keyboard-only работа и `prefers-reduced-motion`; без рекламы/телеметрии/
обязательного GitHub-аккаунта; без ключей/путей/стектрейсов.

### 6. Release pipeline

- Готовые компоненты: детерминированная сборка пакета (`build_package.py`),
  генерация внутреннего/внешнего манифеста, подпись ключом из secret,
  независимая verification (`verify_channel.py` — тем же публичным
  ключом, что встроен в клиент). Рабочий workflow с защищённым SemVer-тегом
  и публикацией immutable artifacts **не запушен этой сессией** (у GitHub
  App сессии нет права `workflows`) — точный патч приведён в разделе
  «Ограничения». Workflow завершается ошибкой при отсутствии signing
  secret; unsigned stable manifest не публикуется; для PR — только
  тестовые ключи из fixture (production-клиент им не доверяет).
- Команда владельца для выпуска — в `infra/release/README.md`.
- Настройки репозитория и реальные release/tag не менялись.

## Threat model

1. **Подмена канала (MITM/компрометация CDN):** подпись Ed25519 над
   каноническим payload проверяется в двух независимых местах (Python
   `cryptography` на сервере, PS-реализация на host) доверенным набором
   ключей с отзывом; fail closed.
2. **Вредоносный пакет:** размер/SHA256 до публикации; распаковка только
   на host с проверкой каждой записи (Zip Slip/ADS/UNC/symlink/exe);
   сверка внутреннего `release.json`; повторная проверка движком перед
   `Update-HrmApp`.
3. **Компрометация backend-контейнера:** контейнер не имеет host-команд и
   docker.sock; максимум — bind-mounted staging; движок повторно
   проверяет подпись/хеш, поэтому подсунутый пакет не пройдёт.
4. **downgrade/replay:** SemVer-политика + сравнение `release_sha`;
   кешируется только проверенный манифест; команды одноразовые.
5. **Авторизация:** статус — только подтверждённый scope; мутации —
   admin + scope; CSRF/rate limit/аудит; движковые эндпоинты — машинный
   токен (секрет, ACL, редакция) + loopback.
6. **Секреты:** закрытый ключ подписи — только secret/environment
   владельца при публикации; `HRM_UPDATE_ENGINE_TOKEN` — в
   `secrets.json`/`pilot.env` (ACL), никогда в argv/логах/диагностике;
   URL без query-секретов; публичные ключи — не секреты.

## Ротация и отзыв ключей

Новый ключ добавляется в `UPDATE_CHANNEL_PUBLIC_KEYS` рядом со старым;
следующий манифест подписывается новым `key_id`; старый помечается
`revoked: true` после выхода обновления со встроенным новым набором.
Отзыв — немедленный; клиенты без ключа в наборе отклоняют `key_id`
(unknown_key), fail closed. На host-стороне набор правится
`-Action channel-config -KeysJson` (попадает в `pilot.env`).

## Изменённые файлы (основное)

```
infra/release/                  channel_contract.py, sign_channel.py,
                                verify_channel.py, build_package.py,
                                make_test_fixtures.py, test_channel_contract.py,
                                testdata/ (31 fixture), README.md
backend/app/update_state.py     server-owned состояния
backend/app/update_channel_contract.py  зеркало контракта (тест на совпадение)
backend/app/channel.py          доверенные ключи, fetch/download (HTTPS-политика), SemVer
backend/app/routers/updates.py  5 эндпоинтов (status/check/download/install/engine-*)
backend/app/{main,config,models,schemas}.py  router, settings, audit actions, scope, схемы
backend/tests/                  test_updates_api.py, test_update_channel_contract.py,
                                test_pilot_overlay.py (канал/привязка staging), conftest (limiter reset)
frontend/src/features/updates/  UpdateChannelPage.tsx (+css), UpdateChannelPage.test.tsx
frontend/src/{api,types}.ts, app-shell/{Workspace,useWorkspaceSection}.ts
infra/windows/engine/Crypto.psm1   Ed25519/SHA/SemVer/канонизация (PS 5.1)
infra/windows/engine/Channel.psm1  конфиг канала, проверка, распаковка, наблюдатель
infra/windows/engine/{Common,Secrets,Install}.psm1  watcher-процесс, токен движка, pilot.env канала, start/stop/uninstall
infra/windows/hr-manager.ps1    actions channel / channel-config, параметры
infra/windows/tests/            channel.tests.ps1, расширения static/engine тестов, run-tests.ps1, lint-engine.py
infra/compose.pilot.yml         UPDATE_* + staging bind mount (только /updates)
installer/installer.iss         запуск наблюдателя после установки
docs/                           phase-13-report-arena.md, README-обновления

# --- Адресная доработка по техприёмке (rework) ---------------------------------
infra/release/publish_channel.py      исполняемое ядро release workflow
review-artifacts/update-channel.{yml,patch}  workflow + применяемый патч
                                      (push отклонён: нет права workflows)
backend/app/channel.py                ручной redirect-цикл с проверкой политики
                                      до запроса, восстановление staging
backend/app/{config,update_state,schemas}.py  UPDATE_CHANNEL_ALLOWED_HOSTS,
                                      apply_engine_report, строгая схема отчёта
backend/app/routers/updates.py        re-delivery install, строгая корреляция report
backend/tests/test_channel_network.py  11 redirect-тестов (локальные HTTPS)
backend/tests/test_staging_recovery.py 5 тестов восстановления staging
backend/tests/test_release_pipeline.py 5 fixture-тестов release pipeline
backend/tests/test_updates_api.py     +9 регрессий отчётов/доставки
backend/requirements-dev.txt          PyYAML (тест инвариантов workflow)
```

## Проверки (точные результаты)

Выполнены локально (Linux-песочница):

```
cd backend
ruff check .                              → All checks passed!
ruff format --check .                     → 113 files already formatted
mypy app tests                            → Success: no issues in 99 source files
pytest -m "not integration" -q            → 592 passed, 105 deselected
pytest tests/test_updates_api.py tests/test_update_channel_contract.py -q
                                          → 30 passed
pytest ../infra/release/test_channel_contract.py -q
                                          → 25 passed
pytest tests/test_pilot_overlay.py -q     → 13 passed

cd ../frontend
npm run lint                              → чисто
npm run typecheck                         → чисто
npm test -- --run                         → 152 passed (21 файл)
npm run build                             → собран

cd ..
python infra/windows/tests/lint-engine.py → структурная проверка пройдена (16 файлов)
git diff --check                          → чисто
```

**Не выполнены локально и почему (честно):** `powershell …/run-tests.ps1`
(pwsh отсутствует в Linux-песочнице; авторитетное исполнение — CI на
windows-latest), `pytest -m integration` (локальный PGlite сериализует
запросы и не покрывает реальную конкуренцию; авторитетный — CI
postgres:16), `docker compose config` (Docker в песочнице недоступен;
структура оверлея проверена тестами `test_pilot_overlay.py`, включая
новый тест поверхности канала). Все эти проверки зелёные в CI для точного
SHA `bc4e9d6` — run 34570110327 (5/5 job'ов success). GitHub-hosted
Windows runner не заменяет ручной acceptance с живым Docker Desktop
(см. handoff).

## Адресная доработка по техприёмке (`prompts/PHASE_13_REWORK_PROMPT.md`)

Стартовый SHA доработки (проверен типом приёмки): `fdcc029…`. Итоговый
code SHA: `bc4e9d6…` (зелёный CI, run 34570110327). Что закрыто:

1. **[P1] Release workflow Phase 13** — `infra/release/publish_channel.py`
   (исполняемое ядро: детерминированный пакет → внешний
   `update-channel.json` → подпись только ключом из secret → независимая
   проверка публичным ключом production-клиента + размер/SHA256 пакета →
   SHA256SUMS; fail closed без signing key/trust store) и тонкий workflow
   `.github/workflows/update-channel.yml` (защищённые SemVer-теги `v*` /
   dispatch владельца, environment `update-channel-signing`, без триггера
   `pull_request`, attestation, публикация draft GitHub Release с
   неизменяемыми активами только после проверки; `release.yml`
   deploy/rollback не тронут). Dispatch-безопасность (по замечанию
   оркестратора): `release_sha` валидируется (40 hex), проверяется его
   существование (`git cat-file -e`), checkout выполняется по нему, сборка
   идёт только при HEAD == release_sha, релиз создаётся `--target` на тот
   же SHA; значения пользователя попадают в shell только через env.
   **Пуш этого файла отклонён GitHub App сессии** (нет права `workflows`;
   точная ошибка и применяемый патч — `review-artifacts/`, см.
   «Ограничения»). Fixture-тесты happy path/неверная подпись/fail-closed
   + инварианты workflow (включая dispatch-SHA и отсутствие inline-inputs)
   — `backend/tests/test_release_pipeline.py` (без production secret, идут
   в существующем CI).
2. **[P1] Redirect до обращения** — `backend/app/channel.py`: авто-redirect
   отключён (`_NoAutoRedirectHandler`), каждый 3xx разбирается вручную,
   относительный `Location` резолвится через URL ответа, ПЕРЕД каждым
   запросом проверяются https/host-политика (`_assert_url_policy`),
   лимит цепочки и циклы — отказ, политика едина для manifest и пакета,
   host-список — серверная настройка `UPDATE_CHANNEL_ALLOWED_HOSTS`;
   streaming/таймаут/лимит размера/cleanup `.part`/безопасные коды —
   сохранены. Детерминированные HTTPS-тесты на локальных серверах
   (`backend/tests/test_channel_network.py`, 11 тестов) доказывают, что
   запрещённый target не получает запрос (счётчик hits == 0).
3. **[P1] Восстанавливаемая доставка install** — `/updates/engine-state`
   больше не очищает команду на опросе: команда с тем же неизменным
   `job_id` и server-owned путями выдаётся повторно до terminal report;
   повторный install при активной операции — 409; report прекращает
   выдачу. Regression-тесты в `test_updates_api.py`.
4. **[P1] Строгая корреляция report** — `job_id` обязателен (422 при
   отсутствии); `UpdateStateStore.apply_engine_report` — единственная
   атомарная точка приёма: точное совпадение с активной операцией,
   missing/unknown/stale/mismatched → 409 без изменения state/pending/
   версии/lock; повтор того же terminal report — идемпотентный 200 без
   второго audit-события и без повторного освобождения lock;
   противоречащий повтор — 409; lock освобождается ровно один раз и
   только владельцем активной операции; значения `state` и поля результата
   валидируются схемой (422). Race покрыт потоковым тестом (6
   конкурентных report → один audit-эффект).
5. **[P2] Восстановление staging** — `download_package`: существующий
   target переиспользуется только после проверки размера и SHA256;
   повреждённый/частичный атомарно заменяется новым проверенным файлом;
   cleanup temp при любой ошибке; валидный reuse без повторного
   скачивания. Тесты: `backend/tests/test_staging_recovery.py` (5).
6. **Отчёт и мелочи** — trailing whitespace в `infra/release/README.md`
   (строка 81) убран; README описывает реальный workflow (не «патч в
   отчёте»); в `review-artifacts/` — точный workflow, применяемый патч и
   точная ошибка push; требования-дев дополнены `PyYAML` (только тест
   инвариантов workflow).

Точные локальные числа (Linux-песочница, venv с теми же пинами, что CI):

```
backend: ruff check → чисто; ruff format --check → 113 файлов чисто;
         mypy app tests → Success (99 файлов);
         pytest -m "not integration" → 592 passed, 105 deselected
         (добавлено 30: 11 сеть, 5 staging, 9 отчёты/доставка, 5 release pipeline)
         test_updates_api + mirror + contract + pilot_overlay → 68 passed
frontend: eslint/typecheck → чисто; npm test → 152 passed; build → собран
infra/windows/tests/lint-engine.py → 16 файлов, пройдено
git diff --check → чисто
```

Не выполнены локально и почему (честно): `pytest -m integration` — нужен
PostgreSQL (локально нет; авторитетный результат — зелёный Linux CI job
`Backend integration tests (PostgreSQL)` на `bc4e9d6`); `powershell
run-tests.ps1` — Windows-раннер CI (зелёный на `bc4e9d6`); `docker
compose config` — зелёный CI job `Compose stack smoke test`.

## Ограничения

1. **[P1, блокер для merge — действие владельца]** `update-channel.yml` не
   запушен этой сессией (GitHub App без права `workflows`). Точная ошибка
   push:
   `remote rejected … (refusing to allow a GitHub App to create or update
   workflow `.github/workflows/update-channel.yml` without `workflows`
   permission)`. Вердикт оркестратора подтверждает: пока файла нет в
   `.github/workflows/`, GitHub Actions не исполняет release pipeline, и
   PR не merge-ready, даже если GitHub показывает MERGEABLE (это лишь
   отсутствие конфликта). Перенос владельцем (однократно) — по
   `review-artifacts/update-channel.patch` (проверен `git apply --check`;
   инструкция в `review-artifacts/README.md`), затем создать environment
   `update-channel-signing` (секреты `UPDATE_CHANNEL_SIGNING_KEY`/
   `UPDATE_CHANNEL_KEY_ID`/`UPDATE_CHANNEL_PUBLIC_KEYS`, protection
   rules) и tag protection `v*`. Инварианты workflow (включая
   dispatch-SHA и отсутствие inline-inputs) проверяются fixture-тестом
   на точной копии из `review-artifacts/`; после переноса тот же тест
   валидирует файл в `.github/workflows/`.

2. **Real release не публиковался**: канал по умолчанию указывает на
   GitHub Releases (`update-channel.json` появится при первом выпуске);
   до этого UI честно показывает `channel_offline`. Доверенных ключей в
   дистрибутиве по умолчанию нет (нет trust-all) — владелец вносит
   публичный ключ через `channel-config`/выпуск.

3. PowerShell-тесты выполняются на Windows-раннере CI (в песочнице —
   только структурный lint + ручная ревизия под PS 5.1).

## Отладка Windows CI (хронология)

Windows-джоб исполняется на GitHub-хостed раннере, а лог-приёмник
(results-receiver) в этой инфраструктуре стабильно отдаёт EOF; диагностика
велась через workflow-аннотации (`::error::` из harness/run-tests.ps1,
читаются через check-runs API). Найдены и исправлены следующие
PS 5.1-дефекты (все покрыты в CI на точном SHA):

1. `Invoke-HrmEdDouble`: специализированная формула удвоения в extended-
   координатах была неверной — любая подпись отклонялась (CI-аннотации:
   «Подпись недействительна» на валидных fixture). Вскрыто пошаговой
   Python-эмуляцией той же BigInteger-арифметики: эталонный affine-
   расчёт (совпадает с `cryptography`) показал, что даже `[2]B` через
   формулу удвоения не совпадает с `B + B`, тогда как полная формула
   сложения HWCD даёт точное совпадение. Исправление: `Invoke-HrmEdDouble`
   теперь выполняет `Invoke-HrmEdAdd $P $P` (для a=-1 закон сложения
   полон, исключительных точек нет). Эмуляция исправленного кода
   подтверждает RFC 8032 §7.1 TEST 1 и fixture-подпись.
2. `Get-HrmCanonicalBytes`: `.Count` на результат pipeline без `@()`
   (в StrictMode 2.0 у скаляра нет Count) — канонизация падала.
3. Пустое сообщение и параметр `$Message` в `Test-HrmEd25519Signature`:
   пустой массив не привязывается к Mandatory-параметру даже будучи
   типизированным `[byte[]]` (ошибка «Cannot bind argument ... empty
   array»). `Mandatory` снят с `$Message`; пустое сообщение — валидный
   вход (RFC 8032 §7.1 TEST 1).
4. UTF-8 BOM: новые `.psm1/.ps1` без BOM читались PS 5.1 как ANSI
   (кириллица в литералах) — BOM добавлен во все файлы движка.
5. `$Error` — автопеременная только для чтения; переименована в
   `$channelError`; параметры манифестов без типа (OrderedDictionary
   не приводится к Hashtable); имя секрета в `pilot.env` приведено к
   `HRM_UPDATE_ENGINE_TOKEN`; наблюдатель запускается только в
   интерактивном режиме (нет связности с неинтерактивными тестами).
6. CRLF на Windows-checkout: `core.autocrlf=true` конвертировал
   `infra/release/testdata/*.txt/*.json` в CRLF — golden-сравнение
   канонических байтов расходилось невидимо. Исправлено двусторонне:
   `Get-HrmFixtureText` нормализует `\r`, а `.gitattributes` фиксирует
   `infra/release/testdata/** -text` (byte-exact на любом checkout).
7. Знаковое чтение hex в .NET: `BigInteger.Parse(hex, "AllowHexSpecifier")`
   трактует старший байт ≥ 0x80 как two's-complement отрицательный
   (RFC-вектор R = 0xe5…, A = 0xd7…, h = 0x9f…; в диагностике CI h и
   координаты приходили отрицательными). Исправление: каждый динамический
   разбор hex-литерала теперь добавляет префикс `"0"`; регрессионный тест
   «little-endian: старший байт ≥ 0x80» закреплён в channel.tests.ps1.
8. `BigInteger.Remainder` сохраняет знак делимого — сравнение верификации
   сравнивало конгруэнтные противоположные представители. Добавлена
   `Get-HrmPositiveRemainder` (нормализация в [0, p)); финальное равенство
   Ed25519 сравнивает `(sB.x·rhs.z) mod p == (rhs.x·sB.z) mod p` и
   y-аналог через нормализованных представителей. Критический путь
   использует только статические API BigInteger (`op_RightShift`,
   `op_BitwiseAnd`, `IsOne`/`IsZero`, `Compare`, `Equals`) — поведение
   PS-операторов `-shr`/`%`/`-eq` на BigInteger не участвует.
9. Диагностический канал: отчёт движка серверу теперь несёт опциональное
   `error_detail` (сообщение исключения + `ScriptStackTrace`, без
   секретов — через `Redact-HrmText`); schema бэкенда расширена
   (`UpdateEngineReportRequest.error_detail`), watcher-тесты встраивают
   detail в текст провала. Это позволило локализовать оба оставшихся
   дефекта (ниже) по аннотациям CI без лог-приёмника.
10. `Get-HrmSha256Hex`: функция возвращала массив байтов одной защитой
    `,` — pipeline доставлял весь `byte[]` ОДНИМ объектом, и
    `$_.ToString("x2")` в `ForEach-Object` бросал `MethodException`
    («Cannot find an overload for "ToString" and the argument count:
    "1"») на PS 5.1. Исправление: хэш-байты присваиваются переменной до
    перечисления (переменная в pipeline перечисляется поэлементно). Это
    был пре-пин сбой, объяснявший оба падавших watcher-теста.
11. Захват success-stream: `Update-HrmApp` пишет журнал через
    `Write-Output` — строки лога попадали в возврат
    `Invoke-HrmChannelInstall`, упаковывая итоговый hashtable в массив;
    `$outcome.version` у вызывающего давал `PropertyNotFoundException`
    («The property 'version' cannot be found on this object», StrictMode).
    Исправление: `$null =` перед `Write-HrmLog` и перед
    `Update-HrmApp` в канале установки (присваивание поглощает весь
    вывод вызова; исключения по-прежнему пробрасываются). Заодно
    инициализирован `$errorDetail = ""` в успешной ветке отчёта —
    иначе StrictMode бросил бы на неприсвоенной переменной.
12. Итог: run 34502728837 на `e626794` — все пять джобов зелёные
    (backend integration, backend checks, frontend checks, Windows
    engine tests + installer smoke, compose stack smoke); оба
    watcher-теста («отчёт installed» и «отчёт rolled_back») проходят,
    installer smoke (silent install/uninstall) проходит.

## Handoff

- **Статус на handoff:** четыре прикладных замечания закрыты и CI
  полностью зелёный (run 34570110327 на `bc4e9d6`, 5/5 job'ов, плюс
  финальный прогон после фикса dispatch — в PR-комментарии). Остаётся
  один блокер: перенос `update-channel.yml` в `.github/workflows/`
  владельцем (у GitHub App сессии нет права `workflows`). До этого
  переноса PR не merge-ready (вердикт оркестратора); мерж — только
  владелец.
- **Владельцу перед выпуском:** перенести `review-artifacts/update-channel.patch`
  в `.github/workflows/` (у сессии нет права `workflows`), создать
  environment `update-channel-signing` (секреты
  `UPDATE_CHANNEL_SIGNING_KEY`/`UPDATE_CHANNEL_KEY_ID`/
  `UPDATE_CHANNEL_PUBLIC_KEYS`, protection rules) и tag protection `v*`.
- Выпуск: `infra/release/README.md` (генерация ключей, сборка пакета,
  подпись, verification, публикация). После выпуска — внести публичный
  ключ в конфигурацию канала и обновить `ExpectedHeadRevision` при новых
  миграциях.
- Windows-приёмка (вручную, с живым Docker Desktop): установить 0.14.0,
  настроить тестовый канал fixture-ключами (НЕ production-ключом),
  проверить: валидный подписанный manifest → обновление; испорченный
  manifest/пакет → отказ; сломанный update → rollback; uninstall с
  сохранением StateDir/томов/бэкапов; offline не мешает работе.
- После merge Phase 12 в `main` — переоткрыть PR Phase 13 на `main`.
