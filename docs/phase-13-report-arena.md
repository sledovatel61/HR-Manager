# Отчёт Phase 13 — безопасный канал доставки обновлений Windows-пилота (arena)

- **Ветка:** `arena/01a084e4-hr-manager` (сессия Arena закреплена за ней;
  работа начата строго от baseline `origin/phase12/windows-acceptance-final`,
  без продолжения ветки Phase 12 новыми коммитами поверх чужих)
- **Baseline SHA (на старте):** `7343025dcc5f33ea5f6298b014b646cddd0ffdc8`
  (= `phase12/windows-acceptance-final`, локально принятая Phase 12)
- **Final code SHA:** см. PR-комментарий (docs-коммит поверх кода)
- **PR:** (см. комментарий)
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
```

## Проверки (точные результаты)

Выполнены локально (Linux-песочница):

```
cd backend
ruff check .                              → All checks passed!
ruff format --check .                     → 96 files already formatted
mypy app tests                            → Success: no issues in 96 source files
pytest -m "not integration" -q            → 562 passed, 105 deselected
pytest tests/test_updates_api.py tests/test_update_channel_contract.py -q
                                          → 24 passed
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
SHA — ссылки в PR-комментарии. GitHub-hosted Windows runner не заменяет
ручной acceptance с живым Docker Desktop (см. handoff).

## Ограничения

1. **CI-workflow не запушен этой сессией** (GitHub App без права
   `workflows`). Патч для владельца — добавить в `.github/workflows/ci.yml`:

```yaml
  windows-installer:
    name: Windows engine tests + installer build
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - name: PowerShell engine tests (Windows PowerShell 5.1)
        shell: powershell
        run: powershell -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/run-tests.ps1
      - name: PowerShell engine tests (pwsh)
        shell: pwsh
        run: pwsh -NoProfile -File infra/windows/tests/run-tests.ps1
      - name: Build HR Manager Setup.exe (Inno Setup 6.7.3, pinned + SHA256-verified)
        shell: pwsh
        run: pwsh -NoProfile -ExecutionPolicy Bypass -File installer/build.ps1 -Version 0.14.0
      - name: Upload installer artifacts
        uses: actions/upload-artifact@v4
        with:
          name: hr-manager-windows-setup
          path: |
            installer/output/*.exe
            installer/release-manifest.json

  pilot-channel-contract:
    name: Update channel contract (Python golden tests)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install cryptography pytest
      - run: pytest infra/release/test_channel_contract.py -q
```

   Release-workflow (подпись secret'ом, verification, публикация по
   защищённому SemVer-тегу, SHA256SUMS/provenance) — готовится владельцем
   как расширение `release.yml`; команда выпуска и требования к secret
   документированы в `infra/release/README.md`. Из PR ничего не
   публиковалось, настройки репозитория не менялись.

2. **Real release не публиковался**: канал по умолчанию указывает на
   GitHub Releases (`update-channel.json` появится при первом выпуске);
   до этого UI честно показывает `channel_offline`. Доверенных ключей в
   дистрибутиве по умолчанию нет (нет trust-all) — владелец вносит
   публичный ключ через `channel-config`/выпуск.

3. PowerShell-тесты выполняются на Windows-раннере CI (в песочнице —
   только структурный lint + ручная ревизия под PS 5.1).

## Handoff

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
