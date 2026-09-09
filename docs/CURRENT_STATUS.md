# Текущее состояние и handoff

> Фазы 0–11 приняты. Phase 11 влита через PR #18 merge-коммитом
> `a6ac73cb797919383b80ce65b1614cd6e4dad47f`. Ветка агента 5
> `arena/01a081aa-hr-manager` сохранена для продолжения работы над Phase 12.

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

## Следующая фаза

Следующая работа — Phase 12. Её продуктовый контракт должен быть согласован
отдельно. Продолжать работу следует в сохранённой ветке агента 5
`arena/01a081aa-hr-manager`, предварительно синхронизировав её с `main`.
