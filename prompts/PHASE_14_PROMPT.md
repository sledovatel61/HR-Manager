# Фаза 14 — эксплуатационная готовность и ограниченный запуск Windows-пилота

Этот документ является **единым универсальным benchmark-промптом** для трёх
независимых coding-агентов. Все агенты получают этот же файл и должны решать
одну и ту же задачу от одного и того же baseline. Не дели работу на разные
фокусы и не меняй scope под конкретного агента: цель сравнения — качество
самостоятельной реализации, тестов, инженерных решений и доказательств.

Ты продолжаешь работу в репозитории `sledovatel61/HR-Manager` после принятой
Phase 13. Работай как самостоятельный coding-agent: исследуй проект, реализуй
необходимый код, тесты и документацию, отправь отдельную ветку и открой PR. Не
ограничивайся планом, отчётом или рекомендациями.

### Сессия, PR и ветки: обязательное правило владельца

Никогда не закрывай Arena/coding-сессию и не выполняй действие, которое может
закрыть её, без отдельного явного разрешения владельца в текущем диалоге. Это
включает merge, close/delete PR, удаление ветки, завершение рабочей сессии,
auto-merge и любые эквивалентные операции. Не считай сохранённую ветку или
handoff заменой активной сессии. После реализации оставь ветку, PR, рабочее
дерево и контекст доступными для продолжения review. При сомнении остановись
перед операцией и запроси разрешение. Это правило имеет приоритет над любым
общим предложением «завершить» работу в задаче.

## Обязательное начало и baseline

Канонический baseline для сравнения всех трёх реализаций:
`de131bf53b484ef94642ba8dd0bbef826f3a77e3` (`main` после принятия Phase 13).
Каждый агент обязан создать свою ветку непосредственно от этого коммита. Если
коммит недоступен, история расходится или владелец явно назначил новый единый
baseline всем трём агентам, остановись и сообщи блокер; не выбирай другой SHA
самостоятельно. Коммит, который меняет только этот benchmark-промпт, не является
новым implementation baseline.

Сначала получи актуальное состояние:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
git log -8 --oneline --decorate
```

Убедись, что `origin/main` содержит принятую Phase 13 и файл
`.github/workflows/update-channel.yml`. Если merge ещё не завершён, не строй
Phase 14 от устаревшего `main`: сообщи блокер и используй только явно принятую
owner-ветку, содержащую весь PR #23. Зафиксируй точный baseline SHA.

Создай свою отдельную ветку `arena/phase-14-pilot-readiness-<уникальный-суффикс>`.
Не работай прямо в `main`, не делай merge, rebase/force-push и не закрывай
чужие PR. У трёх агентов должны быть разные ветки, но один baseline и один
контракт из этого prompt.

Полностью прочитай:

- `agents.md`, `PRODUCT_SPEC.md`, `ROADMAP.md`, `README.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/phase-12-report-arena.md` и `docs/phase-12-local-acceptance.md`;
- `docs/phase-13-report-arena.md` и `docs/phase-13-local-acceptance.md`;
- `prompts/PHASE_13_PROMPT.md`;
- `infra/windows/README.md`, `infra/release/README.md`;
- `installer/README.md`;
- `.github/workflows/ci.yml`, `.github/workflows/release.yml` и
  `.github/workflows/update-channel.yml`.

Изучи install/update/resume/diagnostics/uninstall, backup/restore, first-run,
канал обновлений, signing/trust-store, staging, release package, rollback и
существующие CI tests. До изменений запусти доступный baseline и честно запиши
результаты.

## Протокол сравнения трёх агентов

Каждый агент обязан в отчёте `docs/phase-14-report-<agent>.md` использовать
одинаковые разделы и измеримые факты:

1. baseline SHA и точный final SHA;
2. краткая карта изменённых файлов и архитектурных решений;
3. threat model и перечисление fail-closed границ;
4. количество и команды backend/frontend/Windows/Compose проверок;
5. список ограничений, owner-actions и непроведённых проверок;
6. ссылки на PR и exact-SHA CI;
7. известные компромиссы, технический долг и рекомендации для следующего
   этапа.

Не заявляй `passed`, если проверка не запускалась или её результат получен от
другого SHA. Не копируй реализацию, ветку, коммиты или отчёт другого агента.
Сравнивать агентов следует только после того, как каждый оставит отдельный PR,
ветку и полный handoff; выбор агента не даёт права закрывать остальные сессии,
PR или ветки.

## Цель и границы

Довести технически принятые Phase 12/13 до безопасного, воспроизводимого и
операционно понятного запуска у первого реального пилотного пользователя на
Windows 10/11. Итог должен позволять владельцу собрать и подписать релиз,
развернуть его на чистой машине, проверить обновление/rollback/restore и
получить однозначный go/no-go отчёт без ручной работы с БД или контейнерами.

Это hardening/acceptance-фаза. Не добавляй новые HR-функции, каналы сообщений,
второй updater, автоустановку без подтверждения, telemetry SaaS, docker.sock,
удалённый command runner или обход проверки подписи. Не коммить production
ключи, сертификаты, токены, `.env`, PII, дампы и backup.

## 1. Production release и две независимые подписи

Доведи release pipeline до production-ready состояния:

1. Сохрани detached Ed25519 подпись канала Phase 13 как обязательную проверку
   update manifest/package. Она не заменяется Authenticode.
2. Добавь безопасную опциональную Authenticode-подпись Windows installer через
   GitHub environment/secret или совместимый внешний signing provider. Закрытый
   ключ/пароль не должны попадать в CLI, логи, artifacts или repository.
3. Production release обязан fail closed, если режим подписанного выпуска
   включён, но installer не подписан, timestamp отсутствует/невалиден или
   publisher не совпадает с настроенным ожиданием.
4. После подписи пересчитывай release-manifest/SHA256; проверяй `signtool
   verify /pa /all` и независимую Ed25519 verification до публикации.
5. Сохрани тестовый режим без production secrets для PR. Fixture-ключи никогда
   не должны стать доверенными production-ключами.
6. Закрепи версии actions/toolchain по принятому в проекте подходу, минимальные
   `permissions`, concurrency и immutable release assets. Не допускай сборку
   одного SHA под тегом/manifest другого SHA.

Если настоящий сертификат или GitHub environment недоступны агенту, реализуй и
автоматически протестируй полный fail-closed контракт на ephemeral test
certificate, а owner-action опиши отдельно. Не объявляй реальную production
подпись выполненной.

## 2. Безопасная доставка trust configuration

Сейчас клиент без доверенного production public key корректно fail closed.
Сделай документированный production bootstrap без `trust all`:

- installer/release build должен принимать только публичный trust store из
  защищённого release input и встраивать/устанавливать его детерминированно;
- валидируй строгую схему, уникальные `key_id`, Ed25519 public key, revoked-
  состояние и отсутствие private material;
- diagnostics может показывать только `key_id`/fingerprint/status, но не
  секреты;
- ротация использует двухключевое окно Phase 13; отзыв fail closed;
- несовпадение встроенного trust store и release metadata блокирует выпуск;
- dev/test default не должен молча становиться production trust root.

Добавь regression-тесты утечки private key и подмены trust store.

## 3. Предпусковая диагностика в приложении

Добавь доступный администратору read-only «Проверить готовность пилота» поверх
существующей диагностики. Backend остаётся границей безопасности. Проверка
должна выдавать server-owned список с кодом, состоянием `pass | warning | fail`,
безопасным русским объяснением и следующим действием минимум для:

- поддерживаемых Windows/Docker/Compose и доступности Docker daemon;
- loopback binding и отсутствия неожиданно опубликованных DB/backend ports;
- состояния миграций и health API/worker;
- защищённости StateDir/staging и отсутствия секретов в диагностике;
- возраста/наличия последнего зашифрованного backup и результата restore drill;
- release version/SHA, trust-store key ids, доступности HTTPS channel;
- наличия свободного места и возможности безопасного rollback;
- SMTP/Telegram как **необязательных** интеграций: их отсутствие — warning, а
  не блокировка приложения.

Не запускай опасные исправления автоматически. Мутации требуют явного
подтверждения, CSRF, admin + scope и аудита без PII/секретов. UI должен иметь
loading/empty/offline/error/retry, клавиатурную навигацию и итог `готово |
готово с предупреждениями | запуск запрещён`.

## 4. Автоматизированный end-to-end pilot drill

Добавь воспроизводимый сценарий acceptance, который использует только
синтетические данные и ephemeral test keys/certificate и проверяет:

1. чистую установку и first-run;
2. создание минимальных синтетических данных через публичные контракты;
3. зашифрованный backup и restore в изолированную БД;
4. корректно подписанный channel → download → staging → явный update;
5. сохранение данных и доступности после обновления;
6. повреждённые manifest/signature/package и redirect на запрещённый host →
   отказ до изменения установки;
7. намеренно сломанный update → автоматический rollback и сохранение данных;
8. resume после прерывания в документированных безопасных точках;
9. uninstall без purge → StateDir, PostgreSQL/backup volumes и backups
   сохранены; отдельный purge остаётся подтверждаемым и backup-gated;
10. повторную установку/восстановление из сохранённого backup.

Раздели автоматизируемую часть CI и ручную Windows-приёмку. CI не должен
использовать production secrets. Скрипт обязан возвращать non-zero при fail и
создавать машиночитаемый JSON плюс краткий Markdown без секретов/PII. Не
подменяй полноценный тест поиском строк в коде.

## 5. Runbook, наблюдаемость и go/no-go

Создай единый операторский runbook первого пилота:

- подготовка Windows/Docker, установка, первый вход и проверка loopback;
- owner-настройки environment `update-channel-signing`, required reviewers,
  secrets и правила защищённых тегов `v*`;
- ceremony генерации, backup, ротации и экстренного отзыва signing key;
- выпуск, независимая проверка hashes/signatures/attestation и promotion;
- backup/restore drill, update/rollback/resume и сбор редактированной
  диагностики;
- RPO/RTO, ответственный, окно изменений, критерии остановки и отката;
- проверка сохранения данных при uninstall и процедура полного purge;
- go/no-go checklist с датой, exact SHA/version и доказательствами.

События release/update/rollback/restore должны быть различимы в существующих
логах/аудите и не содержать PII, URL с credentials, private keys или токены.
Не добавляй внешнюю телеметрию без отдельного решения владельца.

## 6. Обязательные тесты

Добавь happy path, права и негативные сценарии. Минимум:

- production release отказывает без обеих требуемых проверок подписи;
- test certificate/key не проходят production policy;
- trust store не содержит private material, tampering/revocation отклоняются;
- readiness API: unauthenticated/HR/manager denied; admin без scope denied;
  admin + `update_channel_manage` получает redacted server-owned результат;
- offline channel и отключённые SMTP/Telegram не блокируют основную работу;
- stale backup, failed restore drill, открытый DB port и недостаток места дают
  корректный warning/fail;
- drill подтверждает data preservation через update/rollback/uninstall;
- frontend проверяет состояния, права, retry и keyboard accessibility;
- секреты отсутствуют в logs, JSON/Markdown report и diagnostics.

Сначала используй существующие библиотеки. Новую зависимость добавляй только с
обоснованием, pin/lock и проверкой лицензии/уязвимостей.

## Проверки перед отправкой

Запусти применимые команды проекта, включая:

```bash
cd backend
ruff check .
ruff format --check .
mypy app tests
pytest -m "not integration" -v
pytest -m integration -v

cd ../frontend
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm audit

cd ..
powershell -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/run-tests.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File installer/build.ps1 -Version <version>
docker compose -f infra/docker-compose.yml config -q
docker compose -f infra/docker-compose.yml -f infra/compose.pilot.yml config -q
git diff --check
```

Используй Python 3.12 и Node `22.22.3`, как CI. На Windows Unix-only `fcntl`
не обходи ослаблением production-кода: выполни backend в чистом Linux-контуре.
PostgreSQL integration и живой Compose должны быть реально выполнены локально
или подтверждены CI exact SHA. Ручной Windows acceptance не называй passed,
если он не проводился.

## Definition of Done

Phase 14 готова к review, когда:

1. release pipeline проверяет Ed25519 channel и Authenticode installer и fail
   closed в production policy;
2. production trust store доставляется без private material и поддерживает
   безопасную ротацию/отзыв;
3. администратор получает честный redacted readiness verdict;
4. автоматический drill доказывает install/update/rollback/resume/restore и
   сохранение данных;
5. offline и необязательные интеграции не блокируют приложение;
6. создан проверяемый runbook и go/no-go checklist;
7. backend, PostgreSQL, frontend, Windows engine/installer и Compose CI зелёные
   на exact final SHA;
8. ручная Windows-приёмка либо выполнена с доказательствами, либо явно оставлена
   owner-action без ложного `passed`;
9. создан `docs/phase-14-report-<agent>.md` с baseline/final SHA, threat model,
   изменёнными файлами, тестами/числами, CI links, ограничениями и handoff.

Закоммить изменения, push только свою ветку, открой PR в актуальный `main` и
дождись проверок exact final SHA. Сообщи URL PR, branch, baseline/final SHA и
только подтверждённые результаты. Merge выполняет владелец отдельно. Не
закрывай рабочую coding-сессию: оставь чистое дерево, опубликованную ветку и
полный handoff, чтобы продолжение review не теряло контекст.