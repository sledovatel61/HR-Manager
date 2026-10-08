# Текущее состояние и handoff

> Фактический текущий checkout содержит реализацию этапов 0–16 (Phase 15 —
> офлайн-лицензия, Phase 16 — текстовый MVP шаблонов документов). Этот файл и
> `docs/handoff-release-0.14.0.md` являются актуальным описанием состояния;
> старые формулировки «15–16 не влиты» больше не использовать. Функциональная
> готовность не является разрешением на публикацию: пилот пока `NO-GO`.

## Что принято

- Фундамент продукта: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, React,
  TypeScript, сессии/CSRF, RBAC, аудит, CI и Docker Compose.
- Кандидаты, передача ответственности, Kanban и карточка, события и календарь,
  воспроизводимая аналитика и CSV.
- Импорт/синхронизация графика выхода, ручное назначение HR и актуальный XLSX
  описаны в [`schedule-import.md`](schedule-import.md).
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

Эксплуатационная готовность Windows-пилота: production release signing,
автоматизированный end-to-end release/upgrade drill, предпусковая диагностика,
проверяемые restore/rollback, наблюдаемость и операторский runbook. Этап влит в
`main`; полный отчёт и честные ограничения:
[`phase-14-report-arena.md`](phase-14-report-arena.md).

## Результат Phase 15 — офлайн-лицензия пилота

Проверяемая офлайн-лицензия установки: миграция `0014_license`, проверка подписи
на сервере, deny-by-default middleware с закрытым allowlist и административный
раздел «Лицензия». Документы: [`license-owner.md`](license-owner.md)
(что подписывается и как выдаётся), [`license-maria.md`](license-maria.md)
(локальная выдача), [`runbook-pilot-release.md`](runbook-pilot-release.md) §11.
Лицензия — отдельная от продуктовых данных сущность: секретов и PII в ней нет.

## Результат Phase 16 — шаблоны документов (текстовый MVP)

- Миграция `0015`: `document_templates`, `document_template_versions`,
  `candidate_document_generations` + PostgreSQL-гарды (append-only снимки,
  замороженное содержимое версии, запрет удаления шаблона, одна активная версия).
- API `/document-templates` (CRUD, версии, публикация, архивация) и
  `/candidates/{id}/generated-documents` (предпросмотр, сохранение снимка,
  история, скачивание HTML/текст). Управление — admin или
  `document_lists_manage`; документы кандидата — по owner-based `can_access`.
- Интерфейс: раздел `#/templates` для всех ролей и вкладка «По шаблону» в
  карточке кандидата.
- Плейсхолдеры — закрытый allowlist: [`document-placeholders.md`](document-placeholders.md).
- В контуре *генерации* файлы не участвуют: PDF/DOCX на сервере не
  генерируются, кандидату ничего не отправляется. Чтение требования «загружает
  новую версию» зафиксировано в рамках Phase 16 (ввод/вставка текста через UI).
  Открытые вопросы (F2 `update_channel_manage`, F3 retention/удаление ПДн,
  F4 строгий admin-only) и матрица требований:
  [`phase-16-report-arena.md`](phase-16-report-arena.md).
- Граница Phase 16 «без файлов» расширена отдельным контуром: файлы, полученные
  от пользователя (анкета `.docx`, скан `.pdf`), хранятся в карточке кандидата —
  вкладка «Документы и анкеты», байты в PostgreSQL, защищённое скачивание,
  квоты, аудит и проверка содержимого файла. Подробности:
  [`candidate-attachments.md`](candidate-attachments.md).

## Пилот 0.14 — финальная доводка для Марии (B1–B6, 2026-09-29)

Финальная доводка пилота для одного реального пользователя (Мария, не программист).
Решение владельца: **пилот без покупных сертификатов Authenticode/TSA**. Неподписанный или test-подписанный `Setup.exe` допустим, SmartScreen обходится инструкцией «Подробнее → Выполнить в любом случае». Отсутствие production-сертификата **не блокирует пилот**. Существующий fail-closed production-пайплайн не ломался — в пилоте он просто не используется.

| B | Проблема ревизии | Что сделано |
|---|---|---|
| B1 | В сборке нет открытого ключа лицензии (`infra/license/public_key.b64` отсутствовал, `compose.pilot.yml` требует `HRM_LICENSE_PUBLIC_KEY` fail-closed) | `installer/build.ps1` теперь берёт ключ из `infra/license/public_key.b64` или из GitHub variable/secret `HRM_LICENSE_PUBLIC_KEY`; без ключа сборка падает с понятной ошибкой. Файл `public_key.b64` коммитить можно (открытый ключ не секрет). |
| B2 | Генератор лицензий не выдаётся владельцу | CI job `license-issuer-windows` теперь публикует `license-issuer-dist.zip` как artifact `license-issuer-owner` (без приватных ключей). |
| B3 | Нет ярлыков | В `installer.iss` добавлена секция `[Icons]`: рабочий стол + меню Пуск — «HR Manager» (`-Action open`, скрытое окно), только в Пуск — «отчёт для разработчика», «перезапуск», «доступ по сети». Автозапуск через папку автозагрузки пользователя. |
| B4 | Диагностика только через PowerShell | Новое действие `-Action support-bundle`: создаёт на рабочем столе `HR-Manager-report-<дата>.zip` (diagnostics JSON, логи, версии, лицензия без подписи, health/readiness). Секреты удаляет `Redact-HrmText`, адреса почты и телефоны — `Redact-HrmPii`; имена и свободный текст из журналов автоматика гарантированно не удаляет, поэтому внутрь архива кладётся `README-ПЕРЕД-ОТПРАВКОЙ.txt` (просмотреть архив перед отправкой владельцу по закрытому каналу). Подсказка в UI готовности. |
| B5 | Доступ только с 127.0.0.1 | Действие `-Action lan-access -Enable/-Disable` (по умолчанию выключено): бинд `0.0.0.0` vs `127.0.0.1`, правило Firewall только Private/Domain (один UAC), переживает обновление. Loopback-only эндпоинты остаются недоступными по сети (X-Real-IP исправлен на `$remote_addr`). |
| B6 | Не доказано обновление поверх с бэкапом/откатом | `Install-HrmApp` теперь при новой версии ведёт через `Update-HrmApp` (бэкап → замены → миграции → health-check → откат к прежним образам). Windows CI: данные и лицензия сохраняются, сломанная миграция → откат. |

### Отложено до коммерческого релиза

* **Authenticode/TSA подпись, покупной сертификат, TIMESTAMP_URL, закреплённые корни** — перенесены из блокера пилота в этот раздел. Для пилота достаточно неподписанного `Setup.exe` с инструкцией по SmartScreen. Production-пайплайн (`.github/workflows/update-channel.yml`) сохранён и не ломается, но в пилоте не используется. Workflow `pilot-release` собирает пилотный релиз без подписи, без tag/Release.
* **HTTPS для LAN** — отсутствие TLS в офисной сети задокументировано как принятый риск пилота (см. `runbook-pilot-release.md`).

### Пилотный Go/No-Go — по пунктам этой задачи (B1–B6)

1. Лицензия: ключ встроен, Setup.exe стартует, лицензия загружается.
2. Ярлыки: после установки и перезагрузки приложение открывается по ярлыку.
3. Отчёт: ярлык создаёт zip без секретов/PII.
4. LAN: коллега открывает `http://<имя-ПК>:8080` при включённом доступе, `/setup/*` по сети недоступен.
5. Обновление поверх сохраняет данные и откатывается при ошибке.
6. Сборка `pilot-release` отдаёт `Setup.exe` + `SHA256SUMS.txt` + `license-issuer-dist.zip`.

Подробности: `docs/MARIA_GUIDE.md` (для Марии), `docs/OWNER_QUICKSTART.md` (для владельца), `docs/runbook-pilot-release.md` § пилота, `docs/handoff-release-0.14.0.md`.

## Вложения в карточке кандидата (.docx / .pdf)

Отдельный контур, расширяющий границу Phase 16 «документы без файлов»: файл,
**полученный от пользователя**, теперь хранится; контур генерации документов
остался текстовым и не изменился.

- Миграция `0020` добавляет таблицу `candidate_attachments` (байты в `BYTEA`),
  4 CHECK-ограничения, частичный уникальный индекс (одно активное вложение на
  пару кандидат+имя) и триггер неизменяемости; `downgrade` полностью обратим.
- Хранение в PostgreSQL, а не на диске: обычный `pg_dump`/`pg_restore` покрывает
  вложения, новый volume в `infra/docker-compose.yml` не нужен, обновление и
  переустановка файлы не теряют.
- API `/candidates/{id}/attachments`: список, загрузка, скачивание
  (`Content-Disposition: attachment`), мягкое удаление. Права проверяются
  сервером на каждый запрос, загрузка/удаление — с CSRF; чужой кандидат → 404.
- Файл проверяется по содержимому: расширение + MIME + размер + магическая
  сигнатура, для DOCX — только central directory (без распаковки, без Zip Slip,
  макросы отклоняются), для PDF — запрет `/JavaScript`, `/Launch`,
  `/EmbeddedFile`, `/XFA` и т.п. Имя файла санитарно обрабатывается и не
  задаёт путь.
- Лимиты в конфигурации: `ATTACHMENTS_MAX_FILE_BYTES` (10 МиБ),
  `ATTACHMENTS_MAX_COUNT` (30), `ATTACHMENTS_MAX_TOTAL_BYTES` (100 МиБ),
  `ATTACHMENTS_ENABLED`.
- Аудит: `candidate_attachment_uploaded`/`_downloaded`/`_deleted`, в `details` —
  только `attachment=<uuid> kind=<…> bytes=<n>`, без имени файла и путей.
  Поиск содержимое вложений не индексирует.
- UI: вкладка «Документы и анкеты» — список, загрузка с прогрессом, квота,
  «Скачать», «Сохранить как…» (File System Access API), «Открыть скачанный
  файл» только после успешного сохранения. Честное ограничение: браузер не
  запускает Word/Acrobat и не открывает локальный файл сам.
- Подробности, права, backup/restore и ручная проверка:
  [`candidate-attachments.md`](candidate-attachments.md).

## Следующая фаза

Открыто: политика ретенции и удаления ПДн (F3) и правило публикации (F4).
Отдельные технические follow-up вне Phase 16 перечислены в отчёте
[`phase-16-report-arena.md`](phase-16-report-arena.md) §9.

## Пилот 0.15.0 — финальная установочная сборка (P1–P12, 2026-10-07)

Работы по финальной сборке для Windows 10/11 x64: аудит `docs/PILOT_FINAL_AUDIT.md`,
итоговый отчёт `docs/PILOT_FINAL_REPORT.md`. Реализовано (код + автотесты + документы):

- **Docker-сценарий** (`infra/windows/engine/Docker.psm1`): поиск Docker Desktop/движка, WSL2, виртуализация,
  права, перезагрузка, место, порт; установка только официального установщика (`desktop.docker.com`, проверка
  Authenticode + Subject, без `--accept-license`); отмена UAC/reboot → `docker-pending.json`; ожидание движка
  с прогрессом; человеческие сообщения.
- **Стек** (`Compose.psm1`): состояния `absent/stopped/partial/running/degraded/unknown`, ремонт и запуск
  существующего проекта `hr-manager-pilot` **без удаления томов**.
- **Трей** (`Tray.psm1`, `Supervisor.psm1`, `hrm-tray.ps1`): значок, меню (открыть/проверить/перезапустить/
  отчёт/остановить/выйти), окно состояния с кнопками, единственный supervisor (mutex), безопасный автозапуск.
- **Обновление поверх** (`Update.psm1`): предпросмотр (версии, changelog, проверки, предупреждение о данных),
  бэкап-ворота, миграции, smoke, автоматический откат образов, `update-result.json`, сохранность лицензии/
  настроек/порта/LAN/томов.
- **Лицензирование**: `LicenseIssuer-Portable.exe` (один файл, без Python/Node/Docker/VS),
  распознавание загрузки приватного ключа в backend, 15 новых UX-тестов активации.
- **Тесты**: `docker.tests.ps1` (12), `stack.tests.ps1` (8), `supervisor.tests.ps1` (13),
  `pilot-final.tests.ps1` (18, включая предпросмотр, сохранность данных и откат), статические 0.15.0-контракты;
  `lint-engine.py` — 29 файлов, 0 провалов; контракт portable-issuer — 77 проверок (на момент первой итерации было 74); backend — 1200 passed / 146 skipped.
- **Документы**: `docs/MARIA_GUIDE.md` (0.15.0), `docs/UPDATE_GUIDE.md`, `docs/RECOVERY_GUIDE.md`,
  `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md`, `docs/DOCKER_RUNTIME_DECISION.md` (Docker Desktop остаётся runtime,
  с юридическим обоснованием), `docs/OWNER_QUICKSTART.md` (один `.exe` для владельца).

### CI-цикл 2026-10-07: что поймано и починено

- Прогон `37622968179` (SHA `239364c`): Backend — только реформат `backend/app/license.py`; Windows-джоб —
  `Test-HrmContainerHealthy` падал на отсутствующем поле `Health` (8 pilot-drill тестов), плюс два статических
  контракта (`docker.exe` в комментарии и литерал `--accept-license` в комментарии); license-issuer — шаг P8
  (сборка portable exe) не уложился в 30 минут.
- Починено в `929f87e`: ruff-формат и аннотация возврата в тесте; чтение `State`/`Health` через
  `PSObject.Properties`; `Get-HrmDockerCliPath` без `Get-Command`; статические проверки смотрят только
  исполняемые строки; полный список провалов тестов публикуется артефактом и в step summary; у launcher
  появился диагностический режим `HRM_PORTABLE_LOG`, payload пакуется `System.IO.Compression.ZipArchive`
  (вместо `Compress-Archive`), GUI-exe запускается через `Start-Process -Wait` с таймаутом.
- Прогон `37628319482` (SHA `929f87e`): Frontend, Backend checks, Release-policy, Backend integration и Compose —
  зелёные; Windows-джоб упал по другой причине: `Write-HrmLog` писал журнал в success stream, поэтому
  `Start-HrmStack` возвращал массив [строка журнала, объект], а `$stack.ok` под StrictMode падал
  («The property 'ok' cannot be found on this object»); license-issuer на этом SHA не прошёл бы статический
  контракт portable-issuer (он требовал литерал `Compress-Archive` и запрещал любое упоминание `Add-Type`).
- Починено в `e67f7a4`: журнал движка не пишет в конвейер (`Format-HrmLogLine` + `Write-Host`/файл
  `HRM_LOG_FILE`, который наследует фоновый процесс supervisor'а); регрессионные тесты на «ровно один объект с
  ok» у `Start-HrmStack` (stack/static/engine); полный список провалов тестов дополнительно публикуется
  notice-аннотациями (лимит `::error::` — 10 на шаг); контракт portable-issuer синхронизирован с новой упаковкой
  (74 проверки, локально все PASS).

Закрыто (прогон `37640729694`, коммит `7a5ed7a`, ветка `arena/4be5f952-hr-manager`): **8 из 8 джобов зелёные** —
Pester-наборы движка, сборка `Setup.exe` и `silent install/uninstall`, Phase 14 pilot drill, сборка portable exe
(`selfcheck`, CLI-цепочка, отказ по подделанной лицензии), backend, backend-PG, frontend, compose, release-policy.
Добавлен джоб `pilot-setup`: пилотный `Setup.exe` 0.15.0 без подписи + `SHA256SUMS.txt` + артефакт
`pilot-setup-0.15.0-unsigned`; хэши артефактов публикуются notice-аннотациями (артефакты скачиваются только через
веб-интерфейс Actions) и внесены в раздел 4 `docs/PILOT_FINAL_REPORT.md`.

Итерация по ревью раунда 12 (P1–P3) закрыта: переписаны три формулировки, обещавшие больше, чем делает код
(в архив отчёта добавлен `README-ПЕРЕД-ОТПРАВКОЙ.txt`, два страховочных теста следят за словами), издатель
официального установщика Docker проверяется точно по subject/отпечатку, снятым с настоящего файла (прогоны
`37653666837` и `37654388276`), `git diff --check` чист, а в отчёте есть таблица «какой файл отдаём Перепечай».
Итоговый прогон головы ветки `df25a39` — `37660015542`: 9 джобов, все зелёные или пропущены по замыслу
(`HRM engine tests: ВСЕ ТЕСТЫ ПРОЙДЕНЫ (162)`).

Остаётся (без этого GO нет): ручной чек-лист `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md` на чистой Windows с реальным
Docker Desktop (UAC, перезагрузка, движок, порт 8080, трей в пользовательской сессии, автозапуск, LAN, активация
лицензии, N→N+1 с данными и откатом) и письменный owner decision. В среде разработки нет Windows/PowerShell/Docker,
поэтому живая установка здесь не запускалась.

## Пилотный релиз: что осталось сделать

Этот раздел отвечает на вопрос «можно ли запускать пилот». Ответ на текущий
момент: **NO-GO** — код финальной сборки 0.15.0 готов, но Windows-приёмка
(`docs/WINDOWS_ACCEPTANCE_CHECKLIST.md`) ещё не выполнена. Не смешивать
завершённость функций с release readiness.

> **Решение владельца 2026-09-29:** покупной сертификат (Authenticode/TSA)
> пилот на ПК Марии **не блокирует** — SmartScreen обходится инструкцией.
> Сертификат перенесён в «отложено до коммерческого релиза» (ниже).

### Обязательные задачи пилота (агенты)

- [x] Установщик «из коробки» (B1–B6): реализовано в 0.15.0 (см. раздел выше и `docs/PILOT_FINAL_AUDIT.md`);
  приёмка на Windows — по `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md` (пока не выполнена).
- [ ] График выхода на работу (Этап 18): `prompts/PHASE_18_WORK_SCHEDULE_PROMPT.md`.
- [ ] Понятные правила + диагностика в администрировании:
  `prompts/UX_RULES_DIAGNOSTICS_PROMPT.md`.

### Обязательные проверки пилота

- [x] Полный backend CI и backend test suite, PostgreSQL integration,
  Compose smoke, frontend checks и Windows engine/installer checks на exact
  release SHA `7a5ed7a` (прогон `37640729694`, 8/8 джобов зелёные):
  backend, backend-PG, frontend, compose, release-policy, Windows-движок +
  установщик (Pester + `Setup.exe` + silent install/uninstall + pilot drill),
  `pilot-setup` (пилотный `Setup.exe` 0.15.0 + `SHA256SUMS.txt`) и
  license-issuer (bundle + portable exe: `parser/build/portable/runtime/accept`,
  контракт portable-issuer, backend-проверка выпущенных лицензий 7/7).
- [ ] Провести чистую Windows 10/11 приёмку: установка, первый вход, loopback,
  readiness, backup/restore, update, rollback, resume, сохранность данных и
  uninstall.
- [ ] Заполнить go/no-go evidence из `docs/runbook-pilot-release.md` и получить
  письменный owner decision. До этого запрещены tag, GitHub Release,
  production workflow dispatch и публикация installer/package.

### Отложено до коммерческого релиза

- Production Authenticode PFX, publisher identity, RFC 3161 TSA, signer/TSA
  roots и шесть Authenticode/TSA secrets в `update-channel-signing`. Не
  использовать fixture, self-signed certificate или придуманные значения.

### Открытые критерии продукта

- [ ] Принять решения по F2 `update_channel_manage`, F3 retention/удалению ПДн и
  F4 строгому admin-only из отчёта Phase 16 либо явно исключить их из scope
  пилота с письменным принятием риска.

### Порядок следующей работы

1. Сначала read-only проверить `git status`, ветку и exact SHA; не затирать
   пользовательские изменения.
2. Принять и смержить три задачи агентов (установщик, график выхода, правила и
   диагностика), каждую — с зелёным CI.
3. Запустить backend/интеграционные/Compose/Windows проверки на итоговом SHA и
   сохранить ссылки на CI evidence.
4. Взять пилотный `Setup.exe` 0.15.0 из артефакта веточного CI
   `pilot-setup-0.15.0-unsigned` (или собрать кнопкой workflow `pilot-release`,
   без Authenticode), сверить SHA256 с `SHA256SUMS.txt` и разделом 4
   `docs/PILOT_FINAL_REPORT.md`; проверить Ed25519-лицензию portable-issuer-ом.
5. Выполнить `docs/WINDOWS_ACCEPTANCE_CHECKLIST.md` на чистой Windows (реальный
   Docker Desktop, трей, обновление с данными и откатом) и только после всех
   PASS + письменного owner decision вынести GO/NO-GO.

Главный стартовый документ следующего чата:
[`handoff-release-0.14.0.md`](handoff-release-0.14.0.md).
