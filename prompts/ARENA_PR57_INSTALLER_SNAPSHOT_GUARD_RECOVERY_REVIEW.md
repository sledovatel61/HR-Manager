# Ревью: патч snapshot-guard recovery (PR #57, head `b72cc61`)

**Вердикт: патч качественный, guard НЕ ослаблен (а усилен). Но acceptance НЕ выполнен:
pwsh-проверка красная (338 passed / 1 failed). Статус остаётся draft / NO-GO.**

Дата ревью: 2026-10-16. Проверял: клон ветки PR, CI-аннотации через API, статическое
чтение диффа, прогон Python-контракта в песочнице.

---

## 1. Что проверено и подтверждено

### 1.1 Guard не ослаблен — подтверждено чтением и прогоном

Новая логика `Invoke-HrmPreviousSnapshotDecision` (`infra/windows/engine/Snapshot.psm1`):

- **Д4 закрыт.** `install_dir_mismatch` — отдельная причина, решается **до** любого
  `Resolve-HrmPreviousSnapshot` (контракт `test_directory_check_precedes_snapshot_reuse`
  утверждает именно это упорядочивание). Оба пути (`record.install_dir`, `{app}`) уходят
  в диагностику. Смешение каталогов больше не маскируется под `reused`/`files_replaced`.
- **Д1/Д2 закрыты.** 9 различающих полей (`file_sha`, `new_release_sha`, `record_sha`,
  `record_install_dir`, `target_install_dir`, `record_pilot_created`, `message`,
  `guard_branch`, `new_build_digest`) проходят helper → Inno-журнал → `setup-snapshot.json`.
  CLI печатает конкретное `$result.message` (через `Redact-HrmText`), а не обобщённое
  `Format-HrmSnapshotReason`. Inno выбирает сообщение по **reason**.
- **Д3 закрыт.** `installer/build.ps1` отказывает при dirty checkout **до** downloads/staging,
  отказывает при `HRM_RELEASE_SHA`, не равном HEAD, перепроверяет чистоту и HEAD после
  staging, и считает `build_digest` по **финальным** staged-байтам (после инъекции
  лицензионного ключа и trust store, до компиляции). `release.json` исключён из digest
  (иначе самоссылка).
- **Исключение повтора узкое.** `retryIncomplete` требует **явного** `pilot_created -is [bool]
  -and -not $pilot_created`, непустого `recordDir`, `installedSha -eq fileSha` **и**
  совпадения `build_digest`. Это ровно «Вариант Б» из промпта, не более.
- **«Тот же коммит, другие байты» продолжает** (`$differentBuild`) — это «Вариант А»,
  предпочтительный по промпту. При этом снимок всё равно создаётся и проверяется.
- **`New-HrmVerifiedSnapshotFromDir` усилен:** сверка source-release identity до копирования,
  сверка скопированного дерева против манифеста **исходника** (не самосогласованности),
  повторная сверка digest исходника после копирования, и `ExpectedBuildDigest`. Любая
  невязка → отказ (`copy_verification_failed`), а не молчаливое продолжение.
- **Нет пути «продолжить без проверенного снимка».** Ветка same-SHA создаёт снимок через
  `New-HrmVerifiedSnapshotFromDir` и возвращает отказ, если `verified` не истина.
  `release.json` / `installed.json` не фальсифицируются.
- **Добавлены новые причины отказа** (`build_digest_mismatch`, `install_dir_mismatch`) —
  отказов стало больше, не меньше.

### 1.2 Регрессионные тесты — настоящие, не тавтологии

**Python-контракт (`infra/windows/tests/test_snapshot_contract.py`, 8 тестов) — прогнал
лично в песочнице:**

```
PATCH (b72cc61): 8 tests, 0 failed   → PASS
BASE  (97df390): 8 tests, 8 failed   → FAIL
```

Red/green доказан. Тесты адресные: упорядочивание directory-check до reuse, границы
retry-исключения, каноничность digest и исключение `release.json`, dirty-checkout до
downloads и digest после инъекции ключа, сверка копии против исходника, `Reason` вместо
`Status` в Inno, экранирование/UTF-8 JSON.

**Pascal-регресс (`4ebda29`)** на мёртвую ветку `if Status = 'files_replaced'` —
корректен: половина на runtime (helper действительно пишет `status=failed`,
`reason=files_replaced`), половина статическая (цепочка сообщений эксклюзивная, ключ по
`Reason`, сырая строка `Причина:` только в generic-fallback). На старом `installer.iss`
этот тест красный — требование «сначала тест, воспроизводящий дефект» выполнено.

### 1.3 Сопутствующие реальные баг-фиксы в `installer.iss`

- `UTF8Encode(Json)` перед `SaveStringToFile` — раньше `setup-snapshot.json` писался в
  ANSI и русские сообщения были нечитаемы (mojibake).
- `EscapeJson` теперь экранирует `\r`, `\n`, `\t` — раньше путь/сообщение с переводом
  строки ломало JSON.
- Снятие BOM (Ord 65279) при чтении helper-TXT — **обязательный компаньон** к переходу
  helper на UTF-8 **с** BOM: без него первый ключ читался бы как `\ufeffstatus` и
  `status=` не находился никогда.
- `SnapshotDetailValues[6]` (`message`) добавляется в диалог пользователя.
- Тексты `SnapshotStop` / `SnapshotFilesReplaced` больше не советуют бессмысленный повтор.

### 1.4 Hygiene не ослаблена

Дифф `.github/workflows/ci.yml` — **чисто аддитивный**: новый джоб
`windows-engine-tests-pwsh` запускает тот же `run-tests.ps1` под pwsh. Джоб 5.1 не тронут,
`.gitignore` не тронут, лицензионные ворота не тронуты, проверка на секреты в failure-list
не тронута. Новая проверка **вскрыла** латентный дефект — ровно то, что должна делать
hygiene-проверка.

### 1.5 Документация честная

`docs/installer-snapshot-recovery.md` прямым текстом говорит: это исправление guard-а,
**не** доказательство живого recovery; незакоммиченные правки `Bootstrap.psm1`/`Secrets.psm1`
в checkout отсутствуют и не реконструировались по догадке; до полной проверки статус
draft/NO-GO. Пути могут содержать имя пользователя → диагностические файлы приватные.
SHA/пути/сообщения из локальных файлов владельца в git не выводятся.

### 1.6 CLI `Compare-HrmSnapshotInputs.ps1` — действительно read-only

`#requires -Version 5.1`, `Set-StrictMode 2.0`, `$ErrorActionPreference = "Stop"`.
Отказ создавать каталог отчёта (должен быть создан с ограниченными ACL заранее), отказ
перезаписывать существующий файл, отказ писать отчёт внутрь InstallDir/StateDir/source,
запись через `FileMode.CreateNew` + `FileShare.None`. Читает ровно три JSON метаданных.
Не вызывает Docker, SQL, snapshot-adoption и генерацию секретов. В консоль — только
предикаты, без SHA/путей/токенов. Ошибки не печатают содержимое источников.

---

## 2. Блокер: pwsh-проверка красная — acceptance #5 НЕ выполнен

**Факт (CI-аннотация, не заявление):** `Windows engine tests (PowerShell 7 / pwsh)`,
host `Core 7.6.6`, **338 passed / 1 failed**:

```
первый запуск: контейнеров нет — стек собирается и поднимается:
до установки контейнеров быть не должно (ожидалось 'absent', получено 'unknown')
  at stack.tests.ps1: line 24
```

Промпт требует: «Полный движковый набор ≥ 321 на Windows PowerShell 5.1 **и** pwsh».
На 5.1 — **339/339**, наборы включают `snapshot-recovery`. На pwsh — 338/1. **Требование
не выполнено.**

### 2.1 Корень локализован — и это продакшн-дефект, не артефакт теста

`Get-HrmStackState` (`infra/windows/engine/Compose.psm1:103`) возвращает `unknown` только
когда `Get-HrmComposeContainers` вернул `$null`. В `Get-HrmComposeContainers`
(`Compose.psm1:60-78`) `$null` возвращается лишь при `$ps.ExitCode -ne 0` либо при срабатывании
**внешнего** `catch { return $null }`.

Мок (`test-harness.ps1`, ветка `compose ps`) при пустом стеке отдаёт
`ExitCode = 0`, `Stdout = "[]"`. Значит внешний `catch` срабатывает: первый
`ConvertFrom-Json` бросает, fallback по строкам на `"[]"` бросает снова.

Локализация подтверждена **исключением**, а не догадкой:

1. Мок на pwsh работает: 14 остальных кейсов `stack.tests.ps1` и весь `db-password.tests.ps1`
   (31 кейс, использующие `compose ps`/`up`/`exec` через тот же мок) — зелены.
2. Единственный тест, подающий на `Get-HrmComposeContainers` пустой `[]` и assert-ящий
   `absent` — `stack.tests.ps1:24`. Он же единственный провал.
3. В `db-password.tests.ps1` состояние `absent` достигается непустым списком контейнеров
   (отсутствует контейнер `db`), поэтому путь пустого списка там не покрыт и провала не даёт.
4. `ExitCode` от мока = 0, поэтому остаётся только внешний `catch`.

**Это реальный пользовательский дефект, а не придирка теста:** на машине с pwsh 7 и
действительно пустым стеком (чистая машина, первый запуск) состояние стека определяется как
`unknown` — «Не удалось получить состояние контейнеров» — вместо `absent` —
«Контейнеры ещё не созданы». Дальше по этой ветке идут решения о сборке/восстановлении,
то есть неверная классификация входа первого запуска.

### 2.2 Почему executor'овский вопрос снят неверно

Executor считает, что менять `Compose.psm1` «не разрешено». **В промпте такого запрета нет.**
Список запретов (`ARENA_PR57_INSTALLER_SNAPSHOT_GUARD_RECOVERY_PROMPT.md`, раздел «Запреты»):

- не обходить snapshot-guard, не переписывать `installed.json`/метаданные снимка;
- не удалять тома Postgres/backups, не сбрасывать БД, не менять пароль владельца в обход
  штатного первого запуска;
- не печатать секреты, `pilot.env`, пароли, полный environment; значения токенов и SHA
  в отчёты и промпты не включать;
- **не ослаблять hygiene-проверку, не менять `.gitignore`, не трогать лицензионные ворота**;
- не мержить PR #57 и не выпускать релиз.

`Compose.psm1` в этом списке отсутствует. При этом acceptance #5 требует зелёного pwsh.
Значит правильный ответ на вопрос исполнителя — **чинить**, а не убирать проверку и не
оставлять её красной:

- **Убрать проверку** = ослабление hygiene = прямо запрещено.
- **Оставить красной** = acceptance #5 не выполнен, патч неполон; и дефект остаётся живым
  для пользователей pwsh.
- **Починить** = единственный вариант, удовлетворяющий и запретам, и acceptance.

---

## 3. Что осталось невыполненным

### 3.1 Требует правки кода (исполнитель)

1. **Узкий фикс pwsh-дефекта** — см. отдельный промпт
   `ARENA_PR57_SNAPSHOT_PWSH_EMPTY_STACK_PROMPT.md`. Границы: только различение
   «контейнеров нет» и «состояние неопределимо»; `Remove-HrmPilotDataVolume`, allowlist
   удаления тома, топология, порт-проверки и прочие части `Compose.psm1` не трогать.
   Обязателен регресс-тест, красный до фикса, на **обоих** хостах (5.1 и pwsh).
2. **Закоммитить правки `Bootstrap.psm1` / `Secrets.psm1`** (кодовый diff, без StateDir,
   env, токенов и PII) + их тесты. Без этого `release_sha` снова не идентифицирует байты.

### 3.2 Требует человека на Windows-машине владельца (вне sandbox)

3. Read-only диагностика `Compare-HrmSnapshotInputs.ps1` (три SHA + `install_dir`).
4. Пересборка из **точного** закоммиченного коммита; SHA256 нового `Setup.exe` в отчёт.
5. Recovery на 8081: Setup → проверка, что изменён только exchange token при висящей
   установке → owner claim → установка пароля → вход. Коды возврата, `setup-snapshot.json`,
   `setup-run.json`, `supervisor.json`.
6. Genuinely clean install: **новый StateDir И изолированный Compose-проект/тома**.
   Новый StateDir alone не доказывает изоляцию БД.
7. Проверить, что `secrets.json` побайтово не изменился, тома `hr-manager-pilot_pilot_pgdata`
   и `hr-manager-pilot_pilot_backups` на месте, защиты snapshot не обойдены.

---

## 4. Что ревьюером проверено лично (чтобы не принимать на веру)

| Проверка | Как | Результат |
|---|---|---|
| Состав диффа | `git diff 97df390..b72cc61 --stat` | 9 файлов, +893/−57 — совпадает с отчётом |
| База — предок | `git merge-base --is-ancestor` | да, fast-forward |
| Guard не ослаблен | чтение `Invoke-HrmPreviousSnapshotDecision`, `New-HrmVerifiedSnapshotFromDir` | усилен, новых отказов больше |
| Python-контракт | прогон в песочнице на патче и на базе | 8/8 PASS vs 8/8 FAIL — red/green |
| Наборы тестов | `git show b72cc61:...` по всем файлам | `snapshot-recovery.tests.ps1` = 20 кейсов, подключён в `run-tests.ps1` |
| CI 5.1 | `gh api .../check-runs` + annotations | 339/339, host `Desktop 5.1.26100.33438`, наборы incl. `snapshot-recovery` |
| CI pwsh | `gh api .../check-runs` + annotations | 338 passed / 1 failed, host `Core 7.6.6`, `stack.tests.ps1:24` |
| Локализация pwsh | чтение `Compose.psm1`, `stack.tests.ps1`, мока `test-harness.ps1`, сверка с `db-password.tests.ps1` | `Get-HrmComposeContainers`, путь пустого `[]` |
| Hygiene | дифф `.github/workflows/ci.yml` | чисто аддитивный, ослабления нет |
| Документация | чтение `docs/installer-snapshot-recovery.md` | честная, границы названы |
| CLI | чтение `Compare-HrmSnapshotInputs.ps1` | read-only, утечки в консоль нет |

**Не проверено лично и не может быть проверено в песочнице:** `pwsh` в sandbox отсутствует
(установить нечем), поэтому точная семантическая причина расхождения 5.1/pwsh на `"[]"`
подтверждается только эмпирически на машине с pwsh; живые сценарии A–G не выполнялись.

---

## 5. Итог

- **Качество патча: высокое.** Причина найдена и устранена по содержанию (build_digest +
  install_dir + полная диагностика), guard усилен, мёртвая ветка Inno починена с настоящим
  регрессом, документация честная, hygiene не ослаблена.
- **Acceptance: НЕ выполнен** — pwsh 338/1 при требовании ≥321 на обоих хостах.
- **Статус: draft / NO-GO сохраняется.** Мерж и релиз запрещены.
- **Следующий шаг:** промпт `ARENA_PR57_SNAPSHOT_PWSH_EMPTY_STACK_PROMPT.md` (узкий фикс
  pwsh-дефекта + регресс на оба хоста + коммит Bootstrap/Secrets), затем человеческие
  пункты 3.2.
