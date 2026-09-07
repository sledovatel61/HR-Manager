# Промпт для AI-агента: backup, deployment и release (Phase 7)

Продолжи разработку HR Manager с этапа «backup, deployment и release» из
`ROADMAP.md`. Это этап 6 роадмапа и седьмой prompt в репозитории, после
`prompts/PHASE_6_PROMPT.md`. Аналитика и увольнения приняты в `main` merge-
коммитом `19fd3f4a4c81cd2ab4c83c9b34fce472eccdcec4`.

## Перед началом

1. Прочитай `agents.md`, `PRODUCT_SPEC.md`, `ROADMAP.md`, `README.md`,
   `docs/ARCHITECTURE.md`, все `docs/phase-*-report-*.md` и этот prompt.
2. Изучи backend-конфигурацию, lifespan, Alembic и миграции, health endpoint,
   модели пользователей и audit log, все Dockerfile, Compose-файлы, preflight-
   скрипты и `.github/workflows/ci.yml`. Установи фактические команды запуска,
   порты, healthchecks, владельцев процессов и точки хранения данных.
3. Изучи существующие тесты и текущий стек. Не добавляй новую библиотеку,
   облачный сервис или оркестратор без обоснованной необходимости. Не меняй
   бизнес-функции этапов 1–6 ради косметики.
4. Начни строго от актуального `origin/main`, содержащего указанный merge-
   коммит или его потомка. Создай отдельную ветку
   `arena/phase-7-release` (если имя занято —
   `arena/phase-7-release-<agent>`).
5. До изменений выполни доступные baseline-проверки и зафиксируй краткий план.
   Не выполняй merge в `main` самостоятельно.

## Цель и результат

Подготовь воспроизводимый и безопасный production-контур для единого
монолитного приложения:

- ежедневный автоматический backup PostgreSQL с retention минимум 7 дней;
- шифрование backup до записи в долговременное хранилище;
- журналирование результата backup без PII и секретов;
- health-check backup и регулярный restore drill в отдельную БД;
- ручной backup перед опасной операцией;
- deployment Docker-образов через контролируемый CI/CD pipeline;
- HTTPS через reverse proxy и безопасные security headers;
- контролируемое применение Alembic-миграций;
- проверяемый rollback приложения и документированный rollback миграций;
- мониторинг доступности, backup freshness, миграций и критических ошибок;
- инструкции администратора, release checklist и процедура аварийного
  восстановления.

Решение должно работать локально в Docker Compose и быть пригодно для
production без секретов в git. Если полноценное внешнее объектное хранилище,
KMS, SMTP или managed monitoring отсутствуют в текущем репозитории, реализуй
безопасные интерфейсы/скрипты на текущем стеке и явно зафиксируй точку
интеграции и ограничение. Не имитируй успешный backup, restore или deploy.

## Зафиксированные инварианты безопасности

- Backup содержит персональные данные и рассматривается как секретный актив.
  Он не должен попадать в git, Docker image layers, CI artifacts с публичным
  доступом, логи или обычный volume приложения.
- Ключ шифрования, credentials БД, registry token, SSH key, TLS private key и
  signing key передаются только через environment/secret storage или CI secrets.
  Не добавляй `.env`, ключи, сертификаты с private key, дампы и тестовые PII.
- Шифрование должно быть аутентифицированным и проверяемым до загрузки/удаления
  исходного файла. Ротация ключа и восстановление старого backup должны быть
  описаны; потеря ключа должна быть явно обозначена как невозможность restore.
- Backup/restore-скрипты должны использовать безопасные права файлов, временные
  каталоги, `umask`, безопасную обработку аргументов и не передавать пароль в
  командной строке или выводе процесса.
- Restore выполняется только в отдельную БД/каталог, никогда автоматически не
  затирает production. Перед восстановлением проверяются checksum, формат,
  версия схемы и свободное место; после восстановления выполняются миграции,
  health-check и smoke-проверки.
- Административные операции требуют серверной авторизации, allowlist команд и
  audit log. Не логируй содержимое backup, connection string, токены или PII.
- Production не должен публиковать PostgreSQL или backend напрямую. Reverse
  proxy принимает только HTTPS, HTTP либо перенаправляет на HTTPS, либо
  отклоняется согласно документированной политике. TLS private key недоступен
  приложению.

## Backup и restore

Добавь production-ready скрипты/команды для:

1. полного PostgreSQL backup в согласованном формате (custom или иной формат,
   поддерживаемый текущей версией `pg_dump`/`pg_restore`);
2. шифрования, checksum и атомарной публикации backup;
3. retention cleanup, который удаляет только backup старше политики и не
   удаляет последние доступные копии при ошибке текущего запуска;
4. проверки свежести и целостности backup;
5. restore drill в отдельный PostgreSQL database/container;
6. ручного backup с reason/request-id и понятным ненулевым exit code при сбое.

Определи и документируй timezone всех расписаний, формат имён, минимальное
свободное место, retry/backoff, lock от параллельных запусков, поведение при
частично созданном файле и критерий «backup успешен». Restore drill должен
проверять не только распаковку, но и фактическое подключение к восстановленной
БД, наличие ключевых таблиц/миграционной версии и `/health` приложения,
направленного на отдельную БД. Зафиксируй RPO/RTO и границы ответственности.

Не добавляй фальшивое API `200 OK`: endpoint или команда health backup должна
возвращать failure, если backup просрочен, checksum неверен, restore drill не
проходил в установленное окно либо место хранения недоступно.

## Deployment и release

Сохрани текущую совместимость `docker compose -f infra/docker-compose.yml -f
infra/compose.prod.yml config`. Раздели dev и production настройки. Добавь,
если их ещё нет:

- pin/документирование версий базовых images и стратегию обновления;
- production image с non-root процессом, без dev-зависимостей и секретов;
- отдельный job/скрипт миграций, запускаемый до переключения трафика и только
  один раз с lock/concurrency guard;
- deploy только из защищённой ветки/tag после обязательных CI checks;
- проверку image digest/health до публикации релиза;
- atomic или blue-green/rolling переключение на проверенный контейнер;
- smoke-check после deploy и автоматическую остановку/возврат трафика при
  провале readiness;
- retention предыдущих образов для rollback и release notes с commit SHA,
  миграциями, конфигурационными изменениями и известными ограничениями.

Миграции должны быть backward-compatible с предыдущей версией приложения либо
иметь явно документированный двухшаговый deploy. Никогда не удаляй колонку или
таблицу до того, как старый код перестанет её использовать. Раздели rollback
кода и rollback схемы: downgrade допустим только если это безопасно для данных;
для destructive migration опиши restore-forward процедуру. Не выполняй
автоматический downgrade production после ошибки без явного безопасного плана.

## HTTPS и наблюдаемость

Добавь конфигурационный пример reverse proxy без секретов: TLS termination,
redirect/strict transport policy, `X-Content-Type-Options`, `Content-Security-
Policy` совместимую с UI, `Referrer-Policy`, clickjacking protection и
ограничение размера запроса. Не утверждай наличие сертификата или DNS — опиши
операторские шаги и проверку срока действия сертификата.

Добавь минимальные проверяемые сигналы без PII:

- readiness/liveness приложения и зависимость от БД;
- время последнего успешного backup, размер/срок и результат restore drill;
- ошибки миграций/deploy и состояние текущего release SHA;
- latency/error rate без URL query, cookie, authorization header и тел запросов.

Документируй alerts, severity, deduplication, cooldown и действия дежурного.
Не добавляй тяжёлую observability-платформу, если текущая инфраструктура её не
поддерживает.

## Ограничения scope

Не реализуй в этом этапе новые функции кандидатов, календаря, аналитики,
контента, интеграции с HH/Авито, мобильный клиент, микросервисы, Kubernetes,
публичную регистрацию, полноценную платёжную или managed-cloud интеграцию.
Не меняй auth/session/CSRF, RBAC, audit, soft delete, analytics contracts,
health semantics или существующие API без необходимости для release-контура.

## Тесты и Definition of Done

Добавь автоматические проверки, не требующие реальных секретов или production:

- backup создаётся в изолированном PostgreSQL, шифруется и восстанавливается в
  отдельную БД; повреждение ciphertext/checksum даёт failure;
- повторный запуск идемпотентен, параллельный запуск блокируется, неполный файл
  не считается backup, retention сохраняет минимум требуемые копии;
- отсутствующие/слабые secrets, неверный key id, недоступная БД и недостаток
  места дают безопасную ошибку без утечки значения;
- restore drill проверяет миграционную head, ключевые таблицы, health и cleanup;
- Compose config для dev/prod, non-root image, отсутствие secret-like файлов и
  отсутствие backup в image/context;
- миграционный pipeline: upgrade, повторный upgrade, проверка lock и
  документированный безопасный rollback;
- deploy smoke, readiness failure и rollback/traffic switch в тестовом контуре;
- HTTPS config, security headers и отсутствие прямых production ports;
- audit events для ручного backup/restore/deploy без PII;
- regression-тесты подтверждают, что auth, RBAC, CSRF, candidates, events,
  analytics, health и shutdown из этапов 1–6 не сломаны.

Запусти все доступные backend/frontend lint, format, typecheck, unit и
integration tests, migration checks, production build, `git diff --check`,
Compose config/smoke и `npm audit`, если он уже есть в проекте. GitHub Actions
должны быть зелёными. Не заявляй о непроверенных командах; отдельно перечисли
проверки, недоступные в окружении, и причину. Проверь, что в git diff нет
секретов, дампов, PII, TODO, `pass`, заглушек и фиктивных успехов.

## Документация и результат

Обнови `README.md` и `docs/ARCHITECTURE.md`: схему deployment, backup/restore,
RPO/RTO, retention, encryption/key rotation, роли и audit, миграции, release
flow, rollback, HTTPS, мониторинг, alerts и команды администратора.

Опубликуй ветку `arena/phase-7-release`, создай PR в `main`, но не выполняй
merge. Добавь `docs/phase-7-report-<agent>.md`. В отчёте укажи полный commit
SHA, ссылку на PR и CI, изменённые файлы, формат backup и команды, фактические
результаты backup/restore drill, модель секретов, RPO/RTO, миграционную и
rollback-стратегию, smoke-проверки и известные ограничения. Не включай в отчёт
значения секретов, PII или содержимое backup.