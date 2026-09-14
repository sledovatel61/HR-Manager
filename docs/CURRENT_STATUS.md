# Текущее состояние и handoff

> Фазы 0–13 приняты. Phase 13 завершена в PR #23; точные результаты локальной
> приёмки находятся в `docs/phase-13-local-acceptance.md`.

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

## Следующая фаза

Phase 14 доводит технически готовый Windows-контур до ограниченного безопасного
пилота: production release signing, end-to-end release/upgrade drill,
предпусковая диагностика, restore/rollback и операторский runbook. Полный
контракт: [`prompts/PHASE_14_PROMPT.md`](../prompts/PHASE_14_PROMPT.md).

## Результат Phase 14

- Ветка сессии Arena: `arena/01a08fef-hr-manager` (PR в main).
- Production release с двумя независимыми подписями: обязательная Ed25519
  channel-подпись + опциональная (production-режим fail-closed) Authenticode
  `installer/sign-installer.ps1` с контрактом `infra/release/installer_signing.py`;
  секреты только через GitHub environments `update-channel-signing` и
  `installer-signing`; ephemeral тестовые сертификаты никогда не проходят
  production-политику.
- Доставка trust configuration: строгая схема `infra/release/trust_store.py`
  (без private material, test ≠ production root), встраивание в installer
  (`build.ps1 -TrustStore`) и пакет, побайтовая сверка в publish-конвейере
  (`trust_store_mismatch` блокирует выпуск), публикация `trust-store.json`;
  Windows-движок: `Test-HrmTrustStoreObject`/`Import-HrmTrustStore`
  (существующая конфигурация не перезаписывается).
- Предпусковая диагностика: read-only `GET /api/updates/readiness`
  (admin + `update_channel_manage`, аудит, redacted), факты host-стороны
  `POST /api/updates/engine-facts` (закрытая схема, машинный токен), UI
  «Готовность пилота» с вердиктом готово|готово с предупреждениями|запуск
  запрещён; SMTP/Telegram — необязательные warning.
- Автоматизированный CI drill `infra/scripts/pilot-drill.sh`
  (джоба `pilot-drill`): живой Compose-стек, синтетические данные,
  эфемерные ключи/сертификаты, first-run, бэкап+restore drill, канал
  check→download→staging→install→resume, отказы на tampered
  signature/package и запрещённый redirect, сохранение данных, JSON+MD
  отчёт без секретов.
- Workflow-изменения (ci.yml + update-channel.yml) — в `review-artifacts/`
  (`.phase14.yml/.patch`): GitHub App не имеет права `workflows`; перенос
  владельцем по инструкции в `review-artifacts/README.md`.
- Runbook и go/no-go: `docs/phase-14-runbook.md` (реальная production-подпись
  и ручная Windows-приёмка НЕ выполнялись — описаны как owner-шаги).
- Полная матрица результатов и честные ограничения:
  [phase-14-report-arena.md](phase-14-report-arena.md).
