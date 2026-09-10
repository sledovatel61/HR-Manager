# Фаза 13 — безопасный канал доставки обновлений Windows-пилота

Ты продолжаешь работу в репозитории `sledovatel61/HR-Manager` после локально
принятой Phase 12. Работай как самостоятельный coding-agent: изучи проект,
реализуй функции, добавь тесты и документацию, создай отчёт, отправь отдельную
ветку и открой PR. Не ограничивайся планом или рекомендациями.

## Обязательное начало и baseline

Phase 12 пока не находится в `main`. Её принятый baseline опубликован в ветке
`phase12/windows-acceptance-final`. Сначала выполни:

```bash
git fetch origin --prune
git switch --detach origin/phase12/windows-acceptance-final
git status --short --branch
git rev-parse HEAD
git log -5 --oneline
```

Рабочее дерево должно быть чистым. Зафиксируй точный baseline SHA в отчёте.
Если к моменту старта Phase 13 Phase 12 уже влита в `main`, убедись, что актуальный
`origin/main` содержит этот baseline, и начинай от нового tip `origin/main`.
Не откатывай более новый принятый код.

Создай новую ветку
`arena/phase-13-secure-update-channel-<короткий-суффикс>`. Не работай прямо в
`main`, не продолжай ветку Phase 12 и не выполняй merge самостоятельно.

Перед изменениями полностью прочитай:

- `agents.md`;
- `PRODUCT_SPEC.md`;
- `ROADMAP.md`;
- `README.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/phase-12-report-arena.md`;
- `docs/phase-12-local-acceptance.md`;
- `prompts/PHASE_12_PROMPT.md`, если он присутствует в baseline;
- `infra/windows/README.md`;
- `installer/README.md`;
- `.github/workflows/ci.yml`;
- этот файл.

Изучи существующие `install/update/resume/diagnostics/uninstall`, формат
`release.json` и `release-manifest.json`, backup gate, журнал фаз обновления,
теги Docker-образов `:previous`, rollback, ACL каталога состояния, Compose
project/volumes и CI Windows installer smoke. До изменений запусти доступный
baseline и честно запиши результаты.

## Цель

Добавить управляемый и криптографически проверяемый канал доставки обновлений
Windows-пилота. Администратор должен видеть доступную версию, безопасно скачать
её и запустить уже принятый Phase 12 update/rollback без ручного скачивания и
распаковки каталога релиза.

Phase 13 не переписывает установщик и не заменяет существующий update engine.
Она добавляет над ним минимальный доверенный release channel и понятный UI.
Локальное обновление через `-ReleaseDir` должно остаться рабочим и полностью
совместимым.

## 1. Контракт канала обновлений

Определи версионируемый JSON-контракт канала как минимум с полями:

- версия схемы;
- стабильный channel (`stable`; допускается отдельно документированный
  `preview`, но он не должен включаться сам);
- версия приложения в SemVer;
- неизменяемый `release_sha` полного Git commit;
- URL релизного пакета;
- размер пакета и SHA256;
- минимальная поддерживаемая текущая версия, если обновление не может быть
  выполнено напрямую;
- дата публикации и краткие русские release notes;
- идентификатор ключа и криптографическая подпись канонического payload.

Не придумывай собственную криптографию. Выбери стандартную проверяемую схему,
доступную на поддерживаемой Windows без хранения закрытого ключа у клиента
(предпочтительно detached Ed25519 signature; допустима другая обоснованная
современная схема). Канонизация подписываемых байтов должна быть однозначной,
документированной и покрытой golden tests.

В репозитории и установщике находится только публичный ключ/набор доверенных
публичных ключей. Закрытый signing key:

- никогда не коммитится;
- не попадает в installer artifact, логи или diagnostics;
- используется только из GitHub Actions secret/environment при публикации;
- допускает документированную ротацию и отзыв ключа без `trust all` fallback.

Отсутствующая, неизвестная, просроченная или неверная подпись, несовпадение
SHA/размера/SHA256, downgrade, некорректный SemVer и неизвестная версия схемы
должны завершаться fail closed до распаковки и до изменения установки.

## 2. Безопасная загрузка и staging

Добавь к Windows-движку отдельные действия или однозначные параметры для:

- проверки наличия обновления без изменения системы;
- загрузки и верификации пакета;
- запуска обновления из проверенного staging-каталога;
- очистки безопасно определяемых временных файлов.

Требования:

- HTTPS обязателен; запрети downgrade на HTTP;
- ограничь число redirect и проверяй конечную схему/host по явной политике;
- не используй GitHub token или другие credentials в пользовательской
  установке для публичного stable-канала;
- скачивай во временный файл, проверяй объявленный размер с разумным верхним
  пределом и SHA256, затем атомарно публикуй в staging;
- повторный запуск после обрыва безопасен: частичный файл не считается
  доверенным релизом; допустимо либо корректное resume, либо повторная загрузка;
- защити распаковку от Zip Slip/path traversal, absolute/UNC/device paths,
  symlink/reparse-point escape, ADS и непредусмотренных исполняемых файлов;
- после распаковки повторно проверь внутренний release manifest и
  `release_sha`; внешний и внутренний контракты не должны противоречить;
- staging не должен находиться внутри каталога сохраняемых пользовательских
  данных или backup volume;
- логи и diagnostics не содержат подпись целиком, URL с query secrets,
  содержимое env, токены или персональные данные;
- сетевой сбой, offline-режим и недоступность GitHub не мешают запуску
  установленного приложения.

После успешной проверки переиспользуй существующий `-Action update
-ReleaseDir ...`. Не создавай второй несовместимый алгоритм миграции/rollback.
Сохрани pre-update encrypted backup gate, миграционную проверку, smoke,
закрепление старых образов `:previous`, автоматический rollback и resume после
перезагрузки.

## 3. Версии и политика обновлений

- Сравнивай SemVer строго, без строкового сравнения (`0.10.0` > `0.9.9`).
- Одинаковая версия и тот же SHA означают `up_to_date`.
- Та же версия с другим SHA — конфликт целостности, а не обычное обновление.
- Downgrade по сети запрещён. Существующий ручной recovery-путь не расширяй
  молча.
- Если версия ниже `minimum_supported_version`, покажи понятное состояние
  `manual_action_required`; не пытайся перескочить несовместимую цепочку.
- Проверка обновлений не должна выполняться чаще документированного интервала.
  Кешируй только успешно проверенный подписанный манифест и сохраняй точное
  время последней успешной проверки.
- Не устанавливай обновления автоматически без явного действия администратора.
  Фоновая проверка допустима, фоновая установка — нет.

## 4. Backend API и авторизация

Если UI взаимодействует с Windows host engine через backend, не давай
контейнеру произвольный доступ к host PowerShell/Docker socket и не добавляй
удалённое выполнение команд. Предпочти безопасный локальный IPC/loopback
контракт с минимальной поверхностью либо иной обоснованный вариант.

Нужны server-owned состояния как минимум:

`idle | checking | up_to_date | available | downloading | ready |
installing | restart_required | failed | manual_action_required`.

- Просмотр статуса доступен только аутентифицированному пользователю с
  отдельным подтверждённым scope или пилотным полным grant.
- Проверка, загрузка и установка — только администратору/отдельному scope;
  frontend не является границей безопасности.
- Mutating endpoints используют существующую CSRF-защиту, rate limit,
  идемпотентность и аудит.
- Клиент не передаёт произвольные URL, пути, команды, manifest payload или
  release SHA. Канал и доверенные ключи задаются серверной/host-конфигурацией.
- Аудит хранит действие, actor, версии, итог и безопасный код ошибки без URL
  query, локальных пользовательских путей и секретов.

Если безопасная связь browser/backend с host engine требует существенно новой
архитектуры, сначала зафиксируй threat model и выбери наименьшую поверхность.
Не включай Docker socket в backend и не создавай универсальный command runner.

## 5. Интерфейс

Добавь русскоязычный раздел обновления в существующее администрирование:

- установленная версия и commit;
- время и результат последней проверки;
- доступная версия, дата и release notes;
- действия «Проверить обновления», «Скачать», «Установить»;
- прогресс без фиктивных процентов, если точный progress неизвестен;
- состояния loading/empty/offline/error/retry/403/conflict;
- явное предупреждение о backup, кратком перерыве в работе и возможном
  автоматическом rollback;
- после завершения — честный итог `updated`, `rolled_back` или
  `manual_action_required`, а не универсальное «успешно»;
- keyboard-only работа, видимый focus, корректные labels/live regions и
  `prefers-reduced-motion`.

Не показывай закрытые ключи, полные локальные пути, stack traces или сырые
логи. Не добавляй рекламу, телеметрию или обязательную учётную запись GitHub.

## 6. Release pipeline

Расширь GitHub Actions существующими, минимально необходимыми jobs:

1. сборка Windows installer/release package закреплённой toolchain;
2. генерация внутреннего и внешнего manifest;
3. подпись внешнего manifest ключом из GitHub secret/environment;
4. независимая verification step тем же публичным ключом, который встроен в
   клиент;
5. публикация immutable artifacts только для защищённого SemVer tag;
6. SHA256SUMS и provenance/attestation средствами GitHub, если они доступны
   без ослабления модели секретов.

Workflow с отсутствующим signing secret должен завершаться ошибкой для release,
а не публиковать «временно неподписанный» stable manifest. Для pull request
используй тестовый ключ только в тестовой fixture: он не должен доверяться
production-клиентом и не должен публиковать release.

Не меняй GitHub repository settings и не публикуй реальный release/tag из PR.
Подготовь pipeline и документированную команду владельца для выпуска.

## 7. Совместимость и миграции

- Phase 13 по умолчанию не требует новой миграции БД. Если состояние канала
  действительно нужно хранить в PostgreSQL, обоснуй это и добавь обратимую
  миграцию поверх `0013`.
- Существующие install/start/stop/status/open/update/diagnostics/uninstall/
  resume и `-ReleaseDir` остаются совместимыми.
- Update/uninstall не удаляют `%LOCALAPPDATA%\HRManager`, PostgreSQL volume,
  backup volume и зашифрованные backups.
- Production Compose/HTTPS, Linux deployment и внешние SMTP/Telegram не должны
  зависеть от Windows update channel.
- Никаких Electron, SQLite, нового микросервиса, второго updater engine или
  auto-update без подтверждения.

## 8. Обязательные тесты

Добавь deterministic unit/static/integration tests минимум для:

1. канонизации и валидной подписи manifest;
2. изменённого байта, неверного/неизвестного/отозванного ключа и malformed
   signature;
3. SHA256/size mismatch, truncated/oversized package;
4. SemVer, same-version/different-SHA, downgrade и minimum supported version;
5. HTTPS/redirect/host policy;
6. Zip Slip, absolute/UNC/device paths, ADS и reparse/symlink escape;
7. обрыва загрузки, повторного запуска и атомарного staging;
8. offline/no-update/update-available состояний;
9. RBAC/scope, IDOR, CSRF, rate limit и idempotency API;
10. отсутствия произвольного URL/path/command в запросах;
11. UI loading/offline/error/retry/403 и admin happy path;
12. сохранения существующего trusted local update;
13. pre-update backup gate, failed smoke, rollback и resume;
14. uninstall с сохранением StateDir, PostgreSQL/backup volumes и backup files;
15. редактирования секретов/PII/URL query/local paths в логах и diagnostics;
16. release workflow на test fixture без production secret;
17. installer build и silent install/uninstall smoke.

Сетевые тесты не должны зависеть от живого GitHub/CDN: используй локальный
HTTP(S) fixture/server и детерминированные пакеты. Криптографические negative
tests обязательны; один happy path недостаточен.

## Проверки перед PR

Выполни все доступные проверки и укажи точные результаты:

```bash
cd backend
ruff check .
ruff format --check .
mypy app tests
pytest -m "not integration" -q
pytest -m integration -q

cd ../frontend
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm audit --audit-level=high

cd ..
python infra/windows/tests/lint-engine.py
powershell -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/run-tests.ps1
docker compose -f infra/docker-compose.yml config -q
docker compose -f infra/docker-compose.yml -f infra/compose.pilot.yml config -q
docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml config -q
git diff --check
```

На Windows backend может блокироваться Unix-only `fcntl`; Linux CI подтверждает
backend/PostgreSQL/Compose. GitHub-hosted Windows runner не заменяет ручной
acceptance с живым Docker Desktop. Не называй непроведённую проверку успешной:
запиши команду, причину и ссылку на соответствующий CI job точного SHA.

Для финального Windows acceptance собери новую версию, проверь корректно
подписанный тестовый channel, намеренно повреждённые manifest/package,
успешное обновление, сломанный update с rollback и uninstall с сохранением
данных. Не используй production private key в локальных/PR-тестах.

## Definition of Done

Фаза принимается только если:

1. клиент принимает только корректно подписанный доверенным ключом manifest;
2. пакет проверяется по размеру/SHA256 и безопасно распаковывается до update;
3. downgrade, replay/conflict и неподдерживаемая цепочка fail closed;
4. offline/ошибка канала не мешают работе установленного приложения;
5. установка запускается только явно уполномоченным администратором;
6. переиспользован Phase 12 update engine с backup gate/rollback/resume;
7. локальный `-ReleaseDir` остаётся рабочим;
8. UI показывает честные состояния и доступен с клавиатуры;
9. release workflow не публикует unsigned stable artifacts;
10. install/update/rollback/uninstall сохраняют пользовательские данные по
    принятому контракту Phase 12;
11. backend, PostgreSQL integration, frontend, Windows engine/installer и
    Compose CI зелёные для точного итогового SHA;
12. создан `docs/phase-13-report-<agent>.md` с baseline/final SHA, threat
    model, форматом подписи/канонизации, key rotation, изменёнными файлами,
    командами и числами passed/skipped, CI links, ограничениями и handoff.

После реализации закоммить изменения, отправь только свою ветку, открой PR в
актуальную принятую базовую ветку (в `main`, если Phase 12 уже влита; иначе в
`phase12/windows-acceptance-final`), дождись проверок именно для итогового SHA и
сообщи URL PR, ветку, baseline SHA, final SHA и только подтверждённые
результаты. Merge выполняет владелец проекта отдельно.