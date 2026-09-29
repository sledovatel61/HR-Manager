# Handoff: pilot release 0.14.0 — стартовый документ нового чата

Дата снимка: 2026-09-29\
Рабочий checkout: `C:\Users\User\Documents\HR\HR Manager Desktop\HR-Manager-current`\
Ветка: `main`\
HEAD на момент снимка: проверить командой `git rev-parse HEAD` перед работой\
Последний commit: проверить командой `git log -1 --oneline` перед работой

## РЕШЕНИЯ ВЛАДЕЛЬЦА (2026-09-29) — приоритет над всем текстом ниже

Как работаем:

- Оркестратор (чат) не пишет код. Код пишут агенты на арене в своих ветках и
  пушат в GitHub. Оркестратор забирает ветку, проверяет, тестирует, мержит и
  ставит новые задачи. Токены впустую не тратить.
- Владелец не программист. Отчёты — коротко и простыми словами, сразу с решением
  и готовым промптом для агента. Ручная работа владельца — только то, что
  физически нельзя сделать без него.

Что это за продукт:

- Пилот для одного человека — Перепечай Марии Павловны (HR, массовый подбор).
  Её ПК — сервер, она же администратор. Установка «из коробки» в пару кликов.
- Лицензия: владелец у себя выпускает ключ-файл со сроком и лимитом
  пользователей (офлайн-генератор `tools/license-issuer`), Мария загружает его в
  программу. Второй HR подключается к ПК Марии по локальной сети.
- Обновление — новый Setup.exe поверх старого, без потери данных.
- Нужен сбор диагностики без персональных данных: Мария одним кликом делает
  файл-отчёт и пересылает владельцу.
- **Покупные сертификаты (Authenticode/TSA) для пилота НЕ нужны и пилот НЕ
  блокируют.** Предупреждение SmartScreen обходится инструкцией. Вопрос вернётся
  только при коммерческом релизе. Поэтому ниже раздел о secrets — «отложено».

Задачи агентам до пилотного релиза (все три обязательны):

| # | Задача | Промпт | Статус |
|---|---|---|---|
| 1 | Установщик «из коробки»: ключ лицензии в сборке, генератор ключей для владельца, ярлыки, отчёт для разработчика в один клик, доступ второго HR по сети, безопасное обновление поверх | `prompts/PILOT_FINAL_PROMPT.md` | в работе у агента |
| 2 | График выхода на работу (Этап 18): дата выхода в карточке, вкладка-таблица по дням, Excel, печать | `prompts/PHASE_18_WORK_SCHEDULE_PROMPT.md` | ждёт агента |
| 3 | Понятные «Мои правила» (живой блок «Что произойдёт») и перенос «Готовности пилота» во вкладку администрирования | `prompts/UX_RULES_DIAGNOSTICS_PROMPT.md` | ждёт агента |

После мержа всех трёх: зелёный CI на итоговом SHA → одна живая проверка на
Windows (установка, вход, лицензия, график, бэкап, обновление поверх — данные на
месте) → пилот GO.

## Состояние передачи

Цель текущего этапа — закрыть release readiness Windows-пилота `0.14.0`,
сохранить честный evidence и только после письменного GO владельца рассматривать
production publishing.

Текущий вывод: **NO-GO**.

Причина: отсутствуют подтверждённые production-материалы от владельца
сертификата/провайдера:

- настоящий Authenticode code-signing PFX;
- пароль PFX;
- точная publisher identity из сертификата;
- RFC 3161 timestamp URL;
- PEM trust roots для цепочки подписанта;
- отдельные PEM trust roots для цепочки TSA.

Placeholder, self-signed test certificate, fixture или значения, придуманные
агентом, использовать запрещено.

## Что уже подтверждено

- Имена шести Authenticode/TSA secrets и их назначение подтверждены.
- Workflow и signing scripts валидируют эти значения и работают fail-closed.
- Ed25519 channel signing и Authenticode installer signing — независимые
  проверки; Authenticode не заменяет Ed25519.
- Production workflow не запускался.
- Tag/release/publishing не создавались.
- Production-код для этой документационной задачи не менялся; обновлены только
  документационные файлы, перечисленные в текущем `git status`.
- Локально уже существовала пользовательская правка `agents.md`; её нельзя
  перезаписывать или откатывать.
- Этот файл является новым документом передачи и добавлен для следующего
  чата/агента.

## Обязательные secrets

Все значения добавляются в GitHub Environment `update-channel-signing`.
Значения не печатать в логи, не коммитить и не записывать в handoff.

| Secret | Требуемое значение | Состояние |
|---|---|---|
| `UPDATE_CHANNEL_AUTHENTICODE_PFX_BASE64` | Base64 полного production `.pfx`/`.p12`, включая private key | `MISSING` |
| `UPDATE_CHANNEL_AUTHENTICODE_PFX_PASSWORD` | Пароль этого PFX | `MISSING` |
| `UPDATE_CHANNEL_EXPECTED_PUBLISHER` | Точная publisher string, вычисляемая из signer certificate | `MISSING` |
| `UPDATE_CHANNEL_TIMESTAMP_URL` | RFC 3161 TSA URL провайдера | `MISSING` |
| `UPDATE_CHANNEL_AUTHENTICODE_SIGNER_ROOTS` | PEM CA roots для цепочки code-signing | `MISSING` |
| `UPDATE_CHANNEL_AUTHENTICODE_TIMESTAMP_ROOTS` | PEM CA roots для цепочки TSA | `MISSING` |

Дополнительно должны быть подтверждены уже существующие channel secrets:

- `UPDATE_CHANNEL_SIGNING_KEY`;
- `UPDATE_CHANNEL_KEY_ID`;
- `UPDATE_CHANNEL_PUBLIC_KEYS`.

В этом handoff их значения не проверяются и не раскрываются.

## Контракт сертификатов

`UPDATE_CHANNEL_AUTHENTICODE_SIGNER_ROOTS` и
`UPDATE_CHANNEL_AUTHENTICODE_TIMESTAMP_ROOTS` — два разных набора PEM.
Нельзя копировать signer roots в timestamp roots.

Каждый PEM secret должен быть UTF-8 без BOM, непустым, содержать один или
несколько разбираемых X.509 certificate blocks и не содержать private material.
В частности, запрещены `PRIVATE KEY`, PFX и пароль.

В trust roots нельзя класть leaf signer certificate, сертификат, извлечённый
из проверяемого `.exe`, или artifact `authenticode-roots.pem`, созданный тем же
signing job. Доверенный якорь должен быть получен независимо и заранее передан
в protected environment secret.

## Где находится реализация

- Workflow: `.github/workflows/update-channel.yml`
- Операторский runbook: `docs/runbook-pilot-release.md`
- Release policy/tooling: `infra/release/README.md`
- Независимый verifier: `infra/release/authenticode.py`
- Production channel publisher: `infra/release/publish_channel.py`
- Windows signing orchestration: `installer/sign.ps1`
- Test-only ephemeral signer: `infra/release/sign_authenticode.py`

`sign_authenticode.py` предназначен только для тестов и fixture. Его нельзя
выдавать за production certificate или использовать для production release.

Критические места текущего контракта:

- workflow принимает signer roots из
  `UPDATE_CHANNEL_AUTHENTICODE_SIGNER_ROOTS`;
- workflow принимает TSA roots отдельно из
  `UPDATE_CHANNEL_AUTHENTICODE_TIMESTAMP_ROOTS`;
- `authenticode.py verify` требует `--require-timestamp` и отдельный
  `--timestamp-roots`;
- production проверяет signer chain, timestamp chain, digest, publisher и
  наличие timestamp;
- несовпадение publisher или недоверенная цепочка должны приводить к отказу.

## Следующий порядок действий

1. Получить у реального Authenticode-провайдера production code-signing
   certificate, экспортируемый PFX и пароль через утверждённый защищённый канал.
2. Получить точную RFC 3161 TSA URL и документацию/цепочки доверия провайдера.
3. Извлечь publisher identity из фактического PFX/certificate и зафиксировать
   её без ручного переименования.
4. Получить и независимо сохранить signer CA PEM и TSA CA PEM. Проверить, что
   это разные цепочки и что в них нет private material.
5. Перевести только PFX в single-line Base64; PEM оставить обычным PEM-текстом.
6. Добавить шесть secrets в `update-channel-signing`, не раскрывая значения.
7. Выполнить read-only проверки имён secrets, environment protection rules,
   required reviewers и branch/tag restrictions.
8. Выполнить pre-flight на Windows: сертификат, private key/PFX password,
   publisher match, Authenticode signing, RFC 3161 timestamp, signer chain,
   TSA chain и Windows `signtool verify /pa`.
9. Проверить release evidence: exact SHA, зелёный CI, test machines, owner,
   operator, independent reviewer, change window и письменное approval.
10. Повторить pilot evidence checklist из `docs/runbook-pilot-release.md`.
11. Только после всех PASS и письменного GO рассматривать контролируемый запуск
    release workflow. До этого workflow dispatch запрещён.

## Запрещённые действия до GO

- Не запускать `workflow_dispatch` production release.
- Не создавать tag или GitHub Release.
- Не публиковать installer/package.
- Не генерировать самодельные Authenticode values.
- Не использовать test fixture как production evidence.
- Не читать, печатать или сохранять secret values, private keys, PFX password
  или полное содержимое PEM в отчётах.
- Не откатывать пользовательскую правку `agents.md`.

## Проверка следующего агента

Новый агент должен сначала:

1. Прочитать этот файл, `agents.md` и `docs/runbook-pilot-release.md`.
2. Выполнить `git status --short`, проверить текущий SHA и не трогать чужие
   изменения.
3. Сверить workflow-контракт с таблицей secrets выше.
4. Явно вывести в отчёте: что проверено, что `MISSING/BLOCKED`, следующий шаг и
   почему решение остаётся `NO-GO`.
5. Не считать наличие secret name доказательством наличия или валидности secret
   value.

## Последняя известная проверка

На момент создания handoff:

- production publishing: не запускался;
- production Authenticode PFX: отсутствует в доступном контексте;
- publisher/TSA/roots: не подтверждены;
- repository implementation: содержит fail-closed production checks, а текущий
  checkout также содержит Phase 15 (офлайн-лицензия) и Phase 16 (текстовый MVP
  шаблонов документов);
- статус: `NO-GO`, pending owner-provided certificate evidence.

## Инструкция владельца для нового чата

Владелец хочет, чтобы новый агент не восстанавливал контекст по истории, а сразу
понимал остаток работы. Требования владельца:

1. Не считать реализованные функции доказательством готовности пилота.
2. Сначала проверить фактический checkout, `git status`, ветку и SHA; не затирать
   незакоммиченные изменения и не откатывать `agents.md`, `PRODUCT_SPEC.md`,
   `ROADMAP.md` или другие пользовательские правки.
3. Не выдумывать результаты. Любой недоступный secret, production certificate,
   Windows machine или owner decision отмечать как `MISSING`/`BLOCKED`.
4. Не запускать production workflow, signing, tag, GitHub Release или публикацию
   installer без письменного GO.
5. Перед каждым отчётом писать простыми словами: что проверено, что не проверено,
   следующий шаг и почему решение остаётся `NO-GO`.
6. Всегда валидировать изменённые документы перечитыванием, `git diff --check` и
   `git status --short`.

## Короткая карта оставшейся работы

### A. Функциональные критерии

- [ ] Подтвердить динамический preview/consequence для правил: что произойдёт,
  для кого, с каким текстом и когда; учесть тихие часы, права и outbox safety.
- [ ] Подтвердить, что readiness и diagnostics находятся в административной зоне,
  либо оформить явное принятие текущего размещения.
- [ ] Решить F2/F3/F4 Phase 16: `update_channel_manage`, retention/удаление ПДн,
  strict admin-only; если это не блокирует пилот — зафиксировать scope и риск.

### B. Проверки

- [ ] Backend CI и полный backend suite.
- [ ] PostgreSQL integration и migration cycle from base.
- [ ] Compose smoke и frontend lint/typecheck/Vitest/build.
- [ ] Windows engine/installer jobs.
- [ ] Exact-SHA evidence, а не результаты другого checkout.

### C. Production release

- [ ] Реальные PFX/password/publisher/TSA/signer roots/TSA roots.
- [ ] Проверка Ed25519 channel secrets без раскрытия значений.
- [ ] Pre-flight signing, timestamp, signer chain, TSA chain и `signtool verify /pa`.
- [ ] Release candidate, независимая проверка подписей, SHA256SUMS и trust store.

### D. Pilot operations

- [ ] Чистая Windows 10/11: install, first login, loopback и readiness.
- [ ] Миграции, backup, restore drill, update, rollback, resume.
- [ ] Доказать сохранность данных после update/rollback/reinstall.
- [ ] Заполнить 15 строк go/no-go checklist в runbook и получить письменный GO.

## Что считается готовым и что пока не входит

Phase 15 — проверяемая офлайн-лицензия установки. Phase 16 — текстовые шаблоны,
версии, публикация, генерация и скачивание HTML/текста. В текущий scope Phase 16
не входят загрузка/хранение бинарных файлов, PDF/DOCX и отправка документа
кандидату. Эти границы нельзя трактовать как незакрытый production blocker, если
владелец явно принимает текстовый MVP для пилота.

Методические материалы из внешнего каталога перечислены в
`docs/hr-methodical-materials.md`; они не импортируются автоматически и не
являются юридически проверенным production-контентом.