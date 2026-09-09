# Фаза 12 — установка, первый запуск и безопасное обновление пилотного контура

Ты продолжаешь работу в репозитории `sledovatel61/HR-Manager` после принятой
Phase 11. Работай как самостоятельный coding-agent: изучи проект, реализуй
функции, тесты и документацию, отправь изменения в отдельную рабочую ветку и
открой PR. Не ограничивайся планом или рекомендациями.

## Обязательное начало

Работа передана агенту 5 через сохранённую ветку
`arena/01a081aa-hr-manager`. Сначала проверь её и актуальную базу:

```bash
git fetch origin --prune
git switch arena/01a081aa-hr-manager
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git merge-base --is-ancestor origin/main HEAD
```

Принятая Phase 11 находится в PR #18: reviewed SHA
`381e24b89fa54919d25d74778d86d659b2c59e88`, merge-коммит
`a6ac73cb797919383b80ce65b1614cd6e4dad47f`, миграционный head `0012`.
Документационный handoff в `main` зафиксирован коммитом
`8d6c2a34e11da9a3aae8a4fc6cbd4aacf16c910f`. Если `origin/main` уже новее,
не откатывай его: изучи новые коммиты и синхронизируй рабочую ветку с актуальным
tip до реализации. Не переписывай опубликованную историю без необходимости.

После синхронизации создай от сохранённой ветки новую рабочую ветку
`arena/phase-12-local-pilot-<короткий-суффикс>`. Не работай прямо в `main`, не
используй чужой незавершённый worktree и не выполняй merge самостоятельно.

Перед изменениями полностью прочитай:

- `agents.md`;
- `PRODUCT_SPEC.md`;
- `ROADMAP.md`;
- `README.md`;
- `docs/CURRENT_STATUS.md`;
- `docs/ARCHITECTURE.md`;
- `docs/backup-and-restore.md`;
- `docs/phase-11-report-arena.md`;
- `prompts/PHASE_11_PROMPT.md`;
- этот файл.

Изучи текущие Compose-файлы, production guard, bootstrap admin, setup wizard,
`/health`, `/ops/status`, backup/restore, `check_env.sh`, `migrate.sh`,
`deploy.sh`, Alembic, frontend settings/setup UI и CI. Зафиксируй baseline SHA,
миграционный head и реальные результаты baseline-проверок.

## Продуктовый контракт

Phase 12 закрывает требование `PRODUCT_SPEC.md`: установка, обновление и
первоначальная настройка должны быть понятны обычному пользователю и не должны
требовать ручной работы с Docker, PostgreSQL, Alembic или CLI.

Целевой сценарий этой фазы — **один локальный пилот на Windows 10/11 с Docker
Desktop и Docker Compose v2**. Приложение остаётся клиент-серверным web-продуктом
с PostgreSQL и Linux-контейнерами. Оно не превращается в Electron-приложение,
Windows service, SQLite-сборку или отдельную однопользовательскую архитектуру.

Должны появиться:

1. понятный PowerShell-сценарий установки/запуска из checkout или release-
   архива;
2. автоматическая безопасная генерация локальных секретов и сохранение
   состояния между запусками;
3. проверка зависимостей и честная диагностика;
4. браузерный первый запуск с созданием пилотного пользователя;
5. безопасный повторный запуск и управляемое обновление с backup, миграцией,
   smoke-check и откатом к предыдущему коду при ошибке;
6. короткая русскоязычная инструкция для пользователя без ручных команд Docker,
   PostgreSQL и Alembic в основном happy path.

## 1. Поддерживаемый локальный профиль

Создай отдельный явно названный Compose overlay/profile для локального пилота.
Не ослабляй `infra/compose.prod.yml` и не меняй development Compose в скрытый
production-вариант.

Требования:

- PostgreSQL, backend, frontend, worker и backup запускаются в Linux-
  контейнерах одной стабильной Compose project name;
- наружу публикуется только frontend на `127.0.0.1`; БД, backend и worker не
  доступны из локальной сети;
- frontend продолжает проксировать API к backend внутри Compose-сети;
- данные PostgreSQL и зашифрованные backup используют именованные volumes и не
  теряются при обычном stop/restart/update;
- внешние SMTP/Telegram-интеграции по умолчанию выключены и не мешают запуску;
- dev-секреты, dev-пароли и `APP_DEBUG=true` в пилотном профиле запрещены;
- production security guard, Secure/SameSite cookies и фактическая схема
  `http://127.0.0.1` должны быть согласованы. Нельзя получить неработающий login
  из-за Secure-cookie по HTTP и нельзя молча отключить защиту production во всех
  окружениях. Зафиксируй и протестируй выбранную локально-доверенную модель;
- не обещай HTTPS, удалённый доступ или многопользовательский production deploy,
  если сценарий их не предоставляет.

## 2. Windows launcher

Добавь один основной PowerShell entry point (например,
`infra/windows/hr-manager.ps1`) с закрытым набором действий минимум:

- `install` — preflight, создание локальной конфигурации, первый запуск;
- `start` — идемпотентный запуск уже установленного контура;
- `stop` — остановка без удаления данных;
- `status` — состояние Docker Desktop, контейнеров, health, worker, backup и
  установленной версии без секретов/PII;
- `update` — безопасное обновление;
- `diagnostics` — санитаризированный отчёт для поддержки.

Допустим тонкий `.cmd`-launcher для запуска PowerShell двойным щелчком, но вся
логика должна оставаться тестируемой и версионируемой. Не требуй глобально менять
ExecutionPolicy, запускать терминал от администратора или вручную вводить
Docker-команды в happy path.

Preflight обязан проверять как минимум:

- Windows и поддерживаемую версию PowerShell;
- наличие и доступность Docker CLI/daemon (Docker Desktop должен быть запущен);
- `docker compose` v2 и минимальную версию, нужную overlay;
- доступность обязательных портов;
- возможность записи в каталог локального состояния;
- достаточное свободное место с документированным порогом;
- корректность уже существующей конфигурации без её перезаписи.

Ошибки должны быть по-русски, содержать понятное действие пользователя и
возвращать ненулевой exit code. Скрипт не должен зависать на интерактивном
prompt в CI/test mode.

## 3. Секреты и локальное состояние

При первом `install` автоматически генерируй криптографически случайные значения
как минимум для signing key, PostgreSQL, bootstrap admin и шифрования backup.

- Не печатай секреты в stdout/stderr, диагностике, process arguments или URL.
- Не коммить и не размещай их внутри repository-tracked файлов.
- Храни конфигурацию в однозначном локальном state directory; добавь его в
  `.gitignore`, если он может оказаться внутри checkout.
- На Windows ограничь ACL текущим пользователем настолько, насколько позволяет
  штатный PowerShell/.NET, и fail closed при невозможности безопасно сохранить
  секреты. Не добавляй тяжёлый secret-manager ради локального пилота.
- Повторный запуск не ротирует ключи и пароли самовольно. Особенно нельзя
  потерять ключ расшифровки существующих backup или инвалидировать сессии без
  явного обновления.
- Bootstrap credential должен быть одноразовым способом войти и создать/выдать
  пилотный доступ, а не постоянным общим паролем. Реализуй безопасный понятный
  flow обязательной смены временного пароля либо эквивалентное решение с
  серверной проверкой и аудитом.
- Не передавай пароль пилота через командную строку, URL, логи или frontend
  bundle.

## 4. Первый запуск в браузере

После успешного запуска launcher открывает только локальный URL приложения и
показывает тот же URL в консоли. Первый запуск должен быть завершён через UI:

- пользователь видит понятный экран состояния и следующие шаги;
- создаёт или активирует единственную пилотную учётную запись с полным явно
  выданным `pilot_full_access`, включая scopes Phase 11;
- задаёт собственный пароль по существующей политике;
- выбирает часовой пояс, рабочие дни и тихие часы;
- видит, что email/Telegram необязательны, и может пропустить их;
- получает подтверждение готовности backend, БД, worker и backup-контура;
- после завершения setup повторный пользователь не может захватить bootstrap
  flow или создать второго полного пилота.

Переиспользуй существующие `/setup/state`, `/setup/pilot`, preferences,
integration status и access grants, но исправь их контракт, если текущий flow
не обеспечивает безопасный first-run. Не создавай unauthenticated admin API.
Bootstrap endpoint/token, если он действительно нужен, должен быть локальным,
одноразовым, короткоживущим, храниться только в виде хеша, защищаться от гонок и
переставать работать после завершения установки. Предпочти более простой
безопасный вариант.

Все mutating endpoints: CSRF после установления сессии, server-side RBAC,
rate-limit там, где применимо, аудит без паролей/токенов/PII и конкурентно
безопасная идемпотентность.

## 5. Update и rollback

`update` должен принимать только явный доверенный источник версии. Не скачивай
и не исполняй произвольный URL/branch и не делай небезопасный `git pull` поверх
грязного checkout. Если полноценная проверка подписанного release-артефакта не
поддерживается текущим release-процессом, реализуй обновление из явно указанного
локального release directory/tag и честно задокументируй trust boundary.

Обязательный порядок:

1. preflight и проверка текущего состояния;
2. сериализация update через lock — два update одновременно невозможны;
3. проверка целевой версии и Compose-конфигурации;
4. **успешный зашифрованный backup и integrity check до миграции**; при ошибке
   обновление прекращается;
5. получение/сборка целевых образов без уничтожения текущих;
6. one-shot Alembic upgrade под существующим PostgreSQL advisory lock;
7. переключение контейнеров;
8. bounded readiness и smoke: frontend, `/health`, `/ops/status` с ожидаемым
   release SHA/version, worker health и отсутствие migration drift;
9. запись санитаризированного результата обновления.

При ошибке после переключения восстанови предыдущие образы/код и проверь их
health. Не выполняй автоматический Alembic downgrade и не заявляй полный rollback
данных после уже применённой необратимой миграции. Покажи пользователю честный
статус: код восстановлен, backup сохранён, требуется операторское восстановление
или forward-fix. Не удаляй предыдущую рабочую версию до успешного smoke.

Повторный `update` после interruption должен либо безопасно продолжить, либо
остановиться с понятной диагностикой; он не должен повторно портить данные.

## 6. Status и diagnostics

Сделай один агрегированный пользовательский результат, где различаются минимум:

- Docker Desktop отсутствует / daemon не запущен;
- приложение остановлено / запускается / готово / degraded;
- база недоступна;
- миграционный head не совпадает;
- worker отсутствует или heartbeat просрочен;
- backup отсутствует, просрочен или последняя проверка неуспешна;
- установленная и запущенная версии различаются;
- необязательные Telegram/SMTP не настроены.

Diagnostics может собирать версии, Compose service states, health codes и
санитаризированные последние логи. Запрещены значения env, connection strings,
cookies, токены, пароли, email/телефоны, ФИО, message bodies и содержимое
кандидатских документов. Добавь автоматические regression-тесты redaction.

## 7. Совместимость и границы

Обязательно сохрани:

- PostgreSQL как единственную production/пилотную БД;
- миграционный путь от `0012` и все данные фаз 0–11;
- owner-based доступ к документам и `candidate_documents_all`;
- append-only history, outbox/worker, quiet-hours, consent, retry/lease/dedup;
- production Compose/deploy/backup сценарии для серверного контура;
- отсутствие PII и секретов в git, логах, audit, metrics и diagnostics.

В Phase 12 **не входят**:

- Electron/native desktop UI, Windows service или установщик MSI;
- встроенная поставка Docker Desktop либо автоматическое принятие его лицензии;
- macOS/Linux desktop launchers (существующие Linux server scripts сохраняются);
- облачный control plane, auto-update daemon и фоновая загрузка релизов;
- удалённая публикация локального пилота в интернет;
- изменение бизнес-функций кандидатов, документов, правил и коммуникаций;
- новая очередь, микросервис, SQLite или хранение секретов в БД открытым текстом.

Не добавляй внешние runtime-библиотеки без доказанной необходимости. Для
PowerShell предпочти штатные средства, Docker Compose и существующие API.

## 8. Тесты

Добавь автоматические тесты минимум для:

1. чистого install и повторного идемпотентного start;
2. отсутствующего Docker CLI, остановленного daemon и неподдерживаемого Compose;
3. занятых портов, недостатка места и недоступного state directory;
4. генерации уникальных сильных секретов, сохранения между запусками и ACL;
5. отсутствия секретов в выводе, аргументах, diagnostics и tracked-файлах;
6. валидного pilot Compose overlay: наружу только loopback frontend;
7. first-run UI, обязательной смены временного секрета и single-pilot race;
8. setup RBAC/CSRF/rate-limit/idempotency и невозможности повторного takeover;
9. применения preferences и пропуска необязательных интеграций;
10. update lock и повторного запуска после interruption;
11. запрета update при неуспешном backup/integrity check;
12. успешного `0012 -> head` upgrade с сохранением данных;
13. неуспешной миграции, readiness и smoke с проверкой code rollback semantics;
14. совпадения release SHA/version и обнаружения migration drift;
15. status/degraded состояния worker, DB и backup;
16. redaction PII/secrets в диагностическом bundle;
17. регрессий backend/frontend фаз 0–11 и production overlay;
18. PowerShell parser/static checks и сценарных тестов с mockable command runner.

Гонки, миграции, backup и constraints доказывай настоящими PostgreSQL/Compose
integration tests. PowerShell-логику структурируй так, чтобы ошибки можно было
детерминированно тестировать без реального удаления volumes и пользовательских
данных. Ни один тест не должен трогать реальную локальную установку.

## Проверки перед PR

Выполни все доступные проверки и запиши точные результаты:

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
docker compose -f infra/docker-compose.yml config -q
docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml config -q
# Добавь сюда точную config-команду нового pilot overlay.
docker compose -f infra/docker-compose.yml up --build --wait --wait-timeout 300
pwsh -NoProfile -File <путь-к-тестов-или-check-скрипту>
git diff --check
```

Дополнительно проведи изолированный Windows/Docker acceptance smoke в чистом
временном каталоге: install → browser/API readiness → status → stop → start →
update/failure drill. Не используй пользовательские volumes текущей машины.

На Windows host полный Python backend collection сейчас может останавливаться на
POSIX-only `fcntl` из backup-кода. Это не означает, что тесты прошли: укажи
точную команду и ошибку, а полный backend запускай внутри Linux-контейнера или
подтверждай GitHub CI на точном итоговом SHA. Для самой Phase 12 Windows launcher
и Windows acceptance обязательны; их нельзя заменить только Linux CI.

Не используй реальные контакты, кандидатов, пароли, токены, SMTP/Telegram
credentials, backup или персональные данные.

## Definition of Done

Фаза принимается только если:

1. обычный пользователь Windows с уже установленным Docker Desktop выполняет
   основной install/start/update без ручных Docker/PostgreSQL/Alembic-команд;
2. pilot overlay безопасно публикует только frontend на loopback и не использует
   development secrets/debug;
3. секреты генерируются автоматически, защищённо сохраняются, не ротируются при
   повторном запуске и не попадают в вывод/диагностику/git;
4. first-run создаёт ровно одного пилота с явным полным доступом, собственным
   паролем и настройками времени без unauthenticated admin backdoor;
5. update блокируется без валидного encrypted backup, применяет миграции один
   раз и проверяет точную запущенную версию;
6. ошибочное обновление возвращает предыдущий код, не делает автоматический
   downgrade БД и честно сообщает границу восстановления;
7. status/diagnostics различают обязательные и необязательные компоненты и не
   раскрывают PII/секреты;
8. данные и функции фаз 0–11 не потеряны, migration path доказан на PostgreSQL;
9. backend, PostgreSQL integration, frontend, Compose и новые Windows checks
   зелёные для точного итогового SHA;
10. создан `docs/phase-12-report-<agent>.md` с baseline/final SHA, архитектурой,
    threat model локальных секретов/bootstrap/update, migration head, файлами,
    точными командами и counts, CI links, Windows acceptance, ограничениями и
    handoff.

После реализации закоммить изменения, отправь только рабочую Phase 12 ветку и
открой PR в `main`. Дождись проверок именно для итогового SHA и сообщи URL PR,
ветку, baseline SHA, final SHA и только подтверждённые результаты. Merge
выполняет владелец проекта отдельно.