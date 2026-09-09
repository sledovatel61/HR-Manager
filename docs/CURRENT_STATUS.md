# Текущее состояние и handoff

> Фазы 0–10 приняты. Актуальный `main` после merge PR #14 —
> `7cebac27a89b7544ff4ff494f45eb906840d96e7`. Следующий этап — Phase 11;
> использовать `prompts/PHASE_11_PROMPT.md`.

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

## Следующая фаза

Строго по `ROADMAP.md` следующая работа — **Phase 11: списки документов и
правила автоматизации**. Полный самодостаточный контракт находится в
`prompts/PHASE_11_PROMPT.md`.

Scope:

1. версионируемые списки требуемых документов со статусами
   `draft/published/archived` и областью применения;
2. снимок применённой версии списка у кандидата и безопасный учёт
   «получен/не получен» без загрузки файлов;
3. запрос и напоминание только о реально недостающих документах через уже
   разрешённые Phase 10 типы сообщений;
4. отправка при выбранном переходе этапа и ограниченный конструктор личных
   правил из закрытых триггеров/условий/действий;
5. серверная область доступа, идемпотентность, аудит, quiet-hours,
   согласия и send-time revalidation.

Не входят: универсальный язык правил, произвольный код/SQL, двусторонний чат,
входящая почта, загрузка файлов через каналы, OCR, SMS, маркетинговые рассылки,
новая очередь или новый микросервис.

## Как начать

Новый агент должен скопировать целиком `prompts/PHASE_11_PROMPT.md`, затем:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git status --short --branch
git rev-parse HEAD
```

Ожидаемая база — merge-коммит выше либо более новый `origin/main`, который
агент обязан изучить. Работать нужно в новой ветке, не в `main`, и не выполнять
merge самостоятельно.

Минимальный baseline и финальный набор проверок приведены в phase-промпте.
Непроведённую команду нельзя называть успешной: нужно записать точную причину и
сослаться на соответствующий CI job для точного итогового SHA.

## Phase 11 — реализация в отдельной ветке, ожидает review

Ветка `arena/01a081aa-hr-manager`, baseline `1a5d0291780a8e41964ff9e47de8e8f09983544f`.
Списки/версии, exact receipt snapshots, document request/reminder через Phase 10,
реальное исполнение личных правил и frontend реализованы. Head миграций `0012`.
Отчёт, ограничения, результаты и handoff: [phase-11-report-arena.md](phase-11-report-arena.md).
Это не объявление фазы принятой: merge/приёмка остаются за владельцем.
