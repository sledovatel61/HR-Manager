# Phase 13 — адресная доработка PR #23 по результатам технической приёмки

## Контекст и режим работы

Продолжи существующую реализацию Phase 13 в PR #23:

- репозиторий: `sledovatel61/HR-Manager`;
- ветка: `arena/01a084e4-hr-manager`;
- base PR: `phase12/windows-acceptance-final`;
- принятый baseline Phase 12:
  `7343025dcc5f33ea5f6298b014b646cddd0ffdc8`;
- проверенный tip до доработки:
  `fdcc02921c1ca46c0a4fa2feba455080c508b775`.

Это адресная доработка уже существующего PR, а не новая реализация фазы. Не
создавай новую ветку или новый PR, не меняй base и не выполняй merge. Сначала
сделай `git fetch`, переключись на существующую ветку, подтяни её только
fast-forward и подтверди чистое рабочее дерево и точный стартовый SHA. Не
переписывай историю и не force-push.

Полностью прочитай `prompts/PHASE_13_PROMPT.md`, текущий
`docs/phase-13-report-arena.md` и затронутые update/release файлы. Сохрани весь
принятый scope Phase 13 и совместимость с локальным `-ReleaseDir`. Не
ограничивайся объяснением или новым отчётом: исправь код, workflow и тесты.

## Причина доработки

Техническая приёмка точного SHA `fdcc029` подтвердила зелёные CI и основные
локальные suites, но PR пока **не готов к merge**. Нужно закрыть следующие
замечания.

## 1. [P1] Реализуй обязательный release workflow Phase 13

`.github` сейчас идентичен baseline, а существующий
`.github/workflows/release.yml` является старым deploy workflow и не реализует
release channel из требований Phase 13. Фрагмент CI в отчёте и ручные команды в
`infra/release/README.md` не заменяют исполняемый workflow.

Расширь GitHub Actions минимально необходимой безопасной release-цепочкой,
которая:

1. запускается для явно документированного защищённого SemVer release tag;
2. собирает Windows installer и детерминированный release package закреплённой
   toolchain;
3. генерирует внутренний manifest и внешний `update-channel.json`;
4. подписывает внешний manifest только ключом из GitHub secret/environment;
5. fail closed завершает release ошибкой при отсутствии или некорректности
   signing secret — unsigned stable manifest публиковать нельзя;
6. отдельным шагом проверяет подпись тем публичным ключом/`key_id`, которому
   доверяет production-клиент, а также размер и SHA256 пакета;
7. публикует immutable artifacts только после успешной независимой проверки;
8. публикует `SHA256SUMS` и provenance/attestation, если возможности GitHub это
   позволяют без передачи signing secret в недоверенный контекст;
9. не предоставляет production signing secret pull request workflow и коду из
   PR/fork;
10. сохраняет существующий deploy/rollback workflow либо безопасно разделяет
    deploy и создание channel release без регрессии Phase 7/12.

Добавь автоматизированную проверку release workflow на test fixture без
production secret. Fixture key разрешён только тестам, не должен быть встроен в
production trust store и не должен публиковать настоящий release. Проверь хотя
бы happy path fixture, неверную подпись и обязательный fail-closed путь без
signing secret. Не выдавай YAML-фрагмент в отчёте за выполненное изменение:
рабочий workflow должен находиться в `.github/workflows/` и входить в diff PR.

Если используемая GitHub identity не имеет разрешения `workflows`, всё равно
подготовь полный целевой workflow и проверенный применимый patch в
`review-artifacts`, зафиксируй точную ошибку push и немедленно сообщи владельцу.
Однако Definition of Done и merge-ready достигаются только после попадания
рабочего workflow в PR и зелёной проверки его точного SHA.

## 2. [P1] Проверяй каждый redirect до сетевого обращения

Проблемные места находятся в `backend/app/channel.py`, прежде всего
`fetch_manifest_text()` и `_download_to_temp()`. Сейчас стандартный
`urllib.request.urlopen()` автоматически проходит redirect chain. Проверка
`response.geturl()` выполняется уже после обращения к конечному адресу, поэтому
allowlist и собственный лимит redirect можно обойти на уровне фактически
выполненного запроса.

Исправь сетевой слой так, чтобы:

- автоматическое следование redirect было отключено;
- каждый ответ redirect разбирался вручную;
- относительный `Location` корректно разрешался через URL исходного ответа;
- до каждого следующего запроса проверялись HTTPS, hostname/allowlist и прочие
  ограничения URL policy;
- запрещённый host/scheme/URL отклонялся **до** обращения к target;
- `DOWNLOAD_MAX_REDIRECTS` ограничивал реальную цепочку, включая loop;
- одинаковая политика применялась к manifest и package;
- streaming, timeout, максимальный размер, cleanup `.part` и безопасные коды
  ошибок не регрессировали;
- URL/query/local path не попадали в логи, аудит или пользовательские ошибки.

Добавь детерминированные локальные HTTP(S) тесты без обращения к GitHub/CDN:

- разрешённый redirect и относительный `Location`;
- redirect на запрещённый host;
- HTTPS → HTTP downgrade;
- цепочка длиннее лимита и redirect loop;
- доказательство, что запрещённый target не получил запрос;
- те же существенные негативные сценарии для manifest и package.

Не подменяй это простой проверкой `response.geturl()` после `urlopen()`.

## 3. [P1] Сделай доставку install action восстанавливаемой

В `backend/app/routers/updates.py` endpoint `/updates/engine-state` сейчас сразу
вызывает `store.clear_pending_actions()` после первого poll. Если watcher
завершится после получения ответа, но до выполнения/report, команда навсегда
теряется, state остаётся `installing`, а install lock — захваченным.

Реализуй надёжный, потокобезопасный протокол как минимум с такими свойствами:

- активная команда привязана к одному неизменному `job_id`;
- poll не считается подтверждением успешного выполнения;
- до terminal report команда может быть безопасно получена повторно с тем же
  `job_id` и теми же server-owned путями/действием, либо используется явно
  реализованный lease/ack с timeout и повторной выдачей;
- потеря ответа, рестарт/падение watcher и повторный poll не создают новую
  install operation и не оставляют систему навечно заблокированной;
- конкурентные poll/install/report не повреждают state и lock;
- произвольный URL/path/command по-прежнему нельзя передать из UI/API.

Добавь regression-тесты: poll → отсутствие report → повторный poll; повторный
poll после условной потери ответа; завершение report прекращает выдачу команды;
конкурентный/повторный install не создаёт второй job.

## 4. [P1] Строго коррелируй и идемпотентно обрабатывай engine report

Сейчас `UpdateEngineReportRequest.job_id` необязателен, а
`/updates/engine-report` отвергает только случай, когда одновременно существуют
два разных непустых ID. В результате report без ID, stale/unknown report и
report без активной установки могут менять state. После этого безусловный
`release_install_action()` способен вызвать `RuntimeError: release unlocked
lock` и вернуть 500.

Исправь контракт и state machine:

- `job_id` обязателен, непуст и валидируется;
- первый terminal report принимается только для активной install operation и
  при точном совпадении `job_id`;
- missing, unknown, stale или mismatched job получает детерминированный 4xx
  (предпочтительно 409 для конфликта) без изменения state, pending command,
  installed version или lock;
- повтор того же terminal report для уже завершённого `job_id` идемпотентен:
  не ухудшает state, не создаёт второй audit side effect и не освобождает lock
  повторно;
- противоречащий второй terminal result для уже завершённого job отклоняется;
- lock освобождается ровно один раз и только владельцем активной операции;
- допустимые значения `state` валидируются схемой, а данные installed
  version/SHA и error fields проверяются в соответствии с типом результата;
- чувствительные значения не добавляются в ответы или аудит.

Добавь API/state regression-тесты для отсутствующего ID, неверного ID, report
без active job, exact successful report, retry того же report, conflicting
retry, rolled-back/failed/restart-required и проверки отсутствия 500. Отдельно
покрой race настолько, насколько позволяет существующая тестовая архитектура.

## 5. [P2] Восстанавливай повреждённый staging artifact

В `backend/app/channel.py::download_package()` существующий target сейчас
возвращается без проверки, а новый уже проверенный temp-файл удаляется. Из-за
этого повторный download не исправляет повреждённый или частичный staging ZIP.

Перед reuse проверь существующий target по ожидаемому размеру и SHA256. Если он
валиден, reuse допустим. Если нет — безопасно и атомарно замени его новым уже
проверенным файлом. Не оставляй частичный release и не допускай TOCTOU в
практически достижимых конкурентных сценариях. Добавь тесты валидного reuse,
повреждённого target, неверного размера и cleanup при ошибке.

## 6. Исправь отчёт и мелкие дефекты качества

- Удали trailing whitespace в `infra/release/README.md` (ранее найден на строке
  81).
- Обнови `docs/phase-13-report-arena.md`: не утверждай, что старый SHA или
  непроведённая команда зелёные; убери описание отсутствующего workflow как
  допустимого handoff после того, как workflow реализован.
- Добавь раздел адресной доработки: стартовый SHA `fdcc029...`, перечень
  исправлений, точные команды и числа тестов, новый final SHA и ссылки на CI
  exact HEAD.
- Не включай секреты, приватные URL, query strings и локальные пути в отчёт.

## Обязательная валидация

Запусти все проверки из `prompts/PHASE_13_PROMPT.md`, включая:

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
python infra/release/test_channel_contract.py
python infra/windows/tests/lint-engine.py
powershell -NoProfile -ExecutionPolicy Bypass -File infra/windows/tests/run-tests.ps1
docker compose -f infra/docker-compose.yml config -q
docker compose -f infra/docker-compose.yml -f infra/compose.pilot.yml config -q
docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml config -q
git diff --check 7343025dcc5f33ea5f6298b014b646cddd0ffdc8...HEAD
```

Дополнительно выполни целевые тесты redirect, update API/state, staging recovery
и test-fixture release workflow. Если команда недоступна в среде, зафиксируй
реальную ошибку и укажи соответствующий зелёный GitHub Actions job exact final
SHA; не называй её локально пройденной. Native Windows `fcntl`-ограничение не
устраняй небезопасным production fallback: backend/PostgreSQL suites должны
подтверждаться Linux CI.

После всех code/workflow изменений сначала отправь ветку, дождись CI именно для
нового exact HEAD и только затем финализируй отчёт. Если коммит отчёта меняет
HEAD, дождись CI также для этого итогового SHA.

## Definition of Done доработки

Доработка готова только когда одновременно выполнено следующее:

1. все пять технических замечаний закрыты кодом и regression-тестами;
2. рабочий release workflow Phase 13 находится в diff PR и fail closed без
   production signing secret;
3. redirect target проверяется до сетевого запроса;
4. потерянный poll/report не теряет install job, а report строго привязан к
   активному `job_id` и безопасно повторяем;
5. повреждённый staging artifact автоматически восстанавливается;
6. `git diff --check` чист;
7. backend, PostgreSQL integration, frontend, Windows engine/installer,
   channel/release fixture и Compose CI зелёные на точном итоговом SHA;
8. отчёт содержит только фактически подтверждённые результаты и ссылки;
9. изменения закоммичены и отправлены в существующую ветку PR #23 без merge и
   force-push.

В финальном ответе сообщи: URL PR, ветку, стартовый и итоговый SHA, список
исправленных замечаний, изменённые файлы, точные результаты тестов/CI и все
оставшиеся ограничения. Не объявляй PR merge-ready, пока любой пункт выше не
подтверждён.