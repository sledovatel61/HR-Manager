# Текущее состояние и handoff

> Фазы 0–13 приняты. Phase 14 завершена в PR #27 и ожидает merge после
> обязательного CI на точном SHA. Продолжать разработку следует в ветке агента 2
> `arena/01a08ff0-hr-manager-02`; PR и coding-сессию не закрывать.

## Что принято

- Фундамент продукта: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, React,
  TypeScript, сессии/CSRF, RBAC, аудит, CI и Docker Compose.
- Кандидаты, передача ответственности, Kanban и карточка, события и календарь,
  воспроизводимая аналитика и CSV.
- Эксплуатационный контур: шифрованные backup, restore drill, health/metrics,
  production overlay, HTTPS proxy, deploy/rollback.
- Phase 8: внутренние уведомления, личные напоминания, PostgreSQL
  transactional outbox, отдельный worker, lease/retry/dedup, тихие часы,
  рабочие дни и пилотный полный доступ.
- Phase 9: реальные опциональные SMTP и Telegram, добровольная привязка,
  диагностика каналов и безопасная тестовая отправка.
- Phase 10: шесть типов односторонних русскоязычных сообщений кандидатам через
  тот же outbox/worker, согласия по каналам, история точного текста,
  server-owned recipient/content, cancel-wins и send-time revalidation.

Подробные отчёты находятся в `docs/phase-8-report-agent2.md`,
`docs/phase-9-report-agent2.md`, `docs/phase-9-backup-smoke-report.md` и
`docs/phase-10-report-agent2.md`.

## Результат Phase 10

- PR: https://github.com/sledovatel61/HR-Manager/pull/14
- Reviewed SHA: `4b44ff8bb446982aea4609e96bfa6819a8fe2331`
- Merge commit в `main`: `7cebac27a89b7544ff4ff494f45eb906840d96e7`
- Проверки reviewed SHA: Backend checks, PostgreSQL integration tests,
  Frontend checks и Compose stack smoke — успешно.
- Миграционный head: `0011`.

После review в Phase 10 добавлены настоящий email double opt-in (одноразовый
HMAC-токен с TTL, в БД только хеш, raw token не персистится), атомарный one-shot
claim и сериализация конкурентных подтверждений/инициаций, HTTP-idempotency
ручной отправки, повторная проверка события и согласия непосредственно перед
provider call, маскирование ссылки в истории и отсутствие секретов/PII в
логах. `accepted` означает только техническое принятие провайдером, а не
доставку или прочтение.

PR #13 с альтернативной реализацией закрыт как superseded by PR #14.

## Ограничения локальной проверки merge

- На Windows полный backend-прогон локально блокировался Unix-only импортом
  `fcntl` в эксплуатационном коде.
- Docker Desktop был недоступен, поэтому локальный PostgreSQL/Compose smoke не
  выполнялся.
- Эти ограничения не выдавались за успешные локальные проверки: соответствующие
  Linux backend, PostgreSQL integration и Compose jobs прошли в GitHub CI на
  reviewed SHA. Frontend checks также прошли в CI.

## Результат Phase 11

- PR: https://github.com/sledovatel61/HR-Manager/pull/18
- Reviewed SHA: `381e24b89fa54919d25d74778d86d659b2c59e88`
- Merge commit в `main`: `a6ac73cb797919383b80ce65b1614cd6e4dad47f`
- Миграционный head: `0012`.
- Backend, PostgreSQL integration, frontend и Compose smoke прошли на reviewed
  SHA и повторно на merge-коммите в `main`.

Принята owner-based политика: HR/manager без `candidate_documents_all` работает
только с кандидатами, где он owner; scope явно открывает документы всей базы.
Реализованы неизменяемые версии списков и исторические снимки, optimistic 409,
request/reminder через существующий outbox, send-time revalidation, закрытые
stage/scheduled rules, durable dedupe и append-only история. Полный отчёт:
[phase-11-report-arena.md](phase-11-report-arena.md).

## Результат Phase 12

- PR реализации: https://github.com/sledovatel61/HR-Manager/pull/22.
- Windows installer `0.13.0`, PowerShell engine, pilot Compose, первый запуск,
  update/rollback/resume, diagnostics и uninstall реализованы.
- На реальной Windows с Docker Desktop успешно проверены trusted update,
  намеренно сломанный update с автоматическим rollback и install/uninstall.
- Uninstall удаляет приложение и контейнеры, но сохраняет StateDir,
  PostgreSQL/backup volumes и зашифрованные backup-файлы.
- SHA256 принятого installer:
  `a9670b3921bd218f27cd571d7eba21775ba951c697d41b06f5cd798650e56a05`.
- Подробный результат: [phase-12-local-acceptance.md](phase-12-local-acceptance.md).

## Ограничение локальной проверки

Профильные backend-тесты нативно на Windows блокируются Unix-only модулем
`fcntl`. Backend в финальном acceptance hardening не менялся. Linux backend,
PostgreSQL integration и Compose должны подтверждаться CI точного SHA; это
ограничение нельзя выдавать за локальный passed.

## Результат Phase 13

- PR: https://github.com/sledovatel61/HR-Manager/pull/23.
- Reviewed SHA до owner-handoff:
  `ac4e7ec302915e36ec614893ebd4559020cea903`.
- Добавлены detached Ed25519 manifest, HTTPS download/staging, строгая SemVer-
  политика, административный UI и fail-closed release pipeline поверх Phase 12.
- Workflow перенесён владельцем из проверенного артефакта в
  `.github/workflows/update-channel.yml`; production signing secrets в git не
  добавлялись.
- Полная матрица результатов и честные ограничения:
  [phase-13-local-acceptance.md](phase-13-local-acceptance.md).

## Результат Phase 14

- Победителем независимой проверки выбран исправленный кандидат агента 2:
  ветка `arena/01a08ff0-hr-manager-02`, PR #27.
- На Windows + Docker Desktop дважды подтверждён изолированный live Compose E2E:
  **17/17 passed**. Проверены bootstrap, синтетический кандидат, реальные байты
  backup, SHA-256/sidecar/state, restore в отдельную БД, подписанный канал,
  tamper-отказы, сохранность данных после перезапуска и cleanup без остаточных
  ресурсов.
- Исправления drill зафиксированы отдельным коммитом
  `02b749bc931afe062d02834269c63d1998ec6bce`; SHA-256 JSON повторного exact-code
  прогона: `503873f08f8a3c62f444ba9d914656e87aabb275c55d37a6b9cb35c874c9e6eb`.
- Полные Phase 14 workflow перенесены из проверенных `review-artifacts` в
  `.github/workflows/` коммитом `7d3097c`; merge разрешён только после зелёного
  CI на финальном SHA ветки. Linux live Compose остаётся дополнительной
  платформенной проверкой и не подменяет уже выполненную Windows-приёмку.

## Следующая работа: готовая поставка для Марии

Следующую разработку продолжать **в этой же ветке/сессии агента 2 после merge
Phase 14**, не начиная новый конкурс агентов. Конечный результат должен быть
ориентирован на пользователя с базовыми навыками:

1. Один понятный подписанный `HR Manager Setup.exe`, запускаемый двойным кликом.
2. Никаких обязательных команд Docker, PowerShell, терминала, ручного редактирования
   `.env` или работы с базой данных. Все зависимости должны устанавливаться или
   проверяться мастером с понятными действиями и сообщениями на русском языке.
3. После установки приложение само открывается и проводит Марию через короткий
   первый запуск; штатные запуск, обновление, backup и восстановление доступны из UI.
4. Вместе с установщиком поставляется короткая русская инструкция «Установка и
   первые шаги» со скриншотами/простыми действиями и отдельным перечнем возможностей:
   кандидаты и Kanban, события и напоминания, документы, коммуникации, аналитика,
   пользователи и права, backup/restore и безопасные обновления.
5. Готовность такой поставки подтверждается clean-machine Windows acceptance от
   лица обычного пользователя. Текущий технический installer всё ещё требует
   заранее установленный Docker Desktop, поэтому финальную поставку «из коробки»
   пока нельзя объявлять выполненной.
