# Независимое ревью PR #29 (ветка `arena/01a0c3d1-hr-manager`)

Ревью выполнено в отдельном worktree `/home/user/agent2-review` (detached),
без изменения ветки агента 2, без merge и без перезаписи истории.

## 1. Фактические данные, подтверждённые напрямую

| Параметр | Значение |
|---|---|
| Ветка агента 2 | `arena/01a0c3d1-hr-manager` |
| Локальный HEAD | `6f2c1b23ea774d5ab8184154384b8b66ea184421` |
| Удалённый head (ls-remote) | `6f2c1b23ea774d5ab8184154384b8b66ea184421` — совпадает |
| CI-ран | `35607388460` |
| Статус рана | `completed / success`, `headSha` совпадает с HEAD |
| Джобы | 6/6 `success`, включая `Windows engine tests + installer smoke` |
| Общий предок с моей веткой | `f19067f` |
| Объём диффа | 16 файлов, +1121 / −208 |

**Заявление агента 2 о CI подтверждено: ран действительно на его SHA и зелёный
по всем шести джобам.**

## 2. Что в PR #29 корректно (перепроверено, не на слово)

- `trust_store.py`: агент **добавил** `assert_no_revoked_keys` — усиление
  production/release-контракта, не ослабление.
- `Test-HrmUntrustedRootOnly` принимает только `NotTrusted` либо `UnknownError`
  с конкретным сообщением про untrusted root. Ослабление test-режима точно
  локализовано, «просто игнорировать ошибку» там нет.
- `publish_channel.py`: изменения сводятся к BOM-терпимому чтению JSON
  (`utf-8-sig`). Production-политика выпуска не тронута.
- `ci.yml`: шаги подписи и smoke-теста не пропускаются, exit-код `sign.ps1`
  проверяется явно.

## 3. Критический дефект: самоcсылочные доверенные якоря

### Где

`installer/sign.ps1` @ `6f2c1b2`, строки 352–368:

```powershell
# 3. Публичная цепочка для независимой проверки вне Windows.
$chainCerts = @($signature.SignerCertificate)
if ($signature.TimeStamperCertificate) { $chainCerts += $signature.TimeStamperCertificate }
Export-HrmCertificatePem -Certificates $chainCerts -Path $RootsPath
...
$verifyArgs = @($verifier, "verify", "--file", $SetupExe, "--trust-roots", $RootsPath,
    "--expected-publisher", $publisher, "--json-out", $VerificationPath)
```

Корень доверия извлекается **из той самой подписи, которую проверяют**.

### Почему это ломает проверку

`infra/release/authenticode.py`, `_verify_chain`:

```python
current = leaf
while True:
    if current.public_bytes(Encoding.DER) in root_ders:
        return          # цепочка «доведена до доверенного корня»
```

Если DER подписанта есть среди корней, функция возвращает успех немедленно.
Утверждение «цепочка доводится до закреплённого корня» становится тавтологией.

### Proof of concept (выполнен, не умозрителен)

Подпись постороннего самоподписанного издателя `CN=EVIL Corp (attacker)`
(такая же форма, какую создаёт `New-SelfSignedCertificate` в CI test-режиме),
trust roots — сертификат подписанта из этого же файла:

```
=== ПОДПИСЬ ПОСТОРОННЕГО ИЗДАТЕЛЯ, trust roots взяты из неё же ===
exit code: 0
stdout   : Authenticode валиден: CN=EVIL Corp (attacker)
  signed                  : True
  chain_verified          : True
  timestamp_present       : True
  timestamp_chain_verified: True
  signer_subject          : CN=EVIL Corp (attacker)

=== Контроль: trust roots из внешнего источника ===
exit code: 1
stderr   : ОШИБКА[untrusted_root]: цепочка сертификатов не доводится до доверенного корня:
           CN=EVIL Corp (attacker)
```

PoC шёл с `--require-timestamp`, то есть **и метка времени принимается от
произвольной «TSA»**: `--timestamp-roots` не передаётся, а
`authenticode.py:893-894` по умолчанию подставляет те же `trust_roots`:

```python
timestamp_roots = load_pem_certificates(args.timestamp_roots) if args.timestamp_roots else trust_roots
```

### Почему зелёный CI этого не поймал

Все тесты, добавленные в PR #29, сами передают в `trust_roots` именно тот
сертификат, которым подписан тестовый артефакт. Они проверяют механику
проверки цепочки, но не её **семантику доверия**. Ни один тест не отвечает на
вопрос «отвергнет ли верификатор чужого издателя при корректно закреплённом
корне».

### Затронут production, а не только CI

Артефакт `installer/authenticode-roots.pem` создаётся из подписи, загружается
и затем подаётся как якорь доверия в двух местах release-джобы:

```
342:  --trust-roots dist/channel/installer/authenticode-roots.pem \
343:  --timestamp-roots dist/channel/installer/authenticode-roots.pem \
381:  --authenticode-roots dist/channel/installer/authenticode-roots.pem \   # гейт publish_channel.py
```

Параметра для внешнего якоря в `sign.ps1` нет вообще, и `update-channel.yml`
его не передаёт. Сквозная production-проверка цепочки тавтологична.

Что production всё-таки удерживает: `$windowsChainTrusted` (реальная проверка
против хранилища Windows) и сравнение издателя с `$ExpectedPublisher` из
секрета. Но заявленный в attestation слой `chain_verified: true` и
`independent_verification.passed: true` — **фиктивное assurance**, и он
записывается в release-manifest.

### Сопутствующие находки

- `--expected-publisher` берётся из `$signature.SignerCertificate` — сравнение
  сертификата с самим собой (в production есть отдельная реальная проверка,
  в test-режиме ограничения на издателя нет вовсе).
- Проверка `SignerCertificate.Thumbprint -ne $testCertificate.Thumbprint`
  находится **внутри** ветки `if (-not $signtoolVerifyOk)`. На успешном пути
  она не выполняется, то есть привязка файла к нашему ephemeral-сертификату
  не проверяется безусловно.

## 4. Важное уточнение происхождения дефекта

Дефект **не привнесён агентом 2**. Он присутствует в идентичном виде в моей
ветке (`installer/sign.ps1:353-355` @ `36329a2`) — я его и внёс. Агент 2
унаследовал его из общей базы `f19067f`. Исправление сделано в моей ветке.

## 5. Исправление (в ветке `arena/01a0c3bb-hr-manager`)

1. **`installer/sign.ps1`** — якорь доверия больше никогда не берётся из
   проверяемого файла:
   - новые параметры `-SignerRootsPath` / `-TimestampRootsPath`;
   - в production они **обязательны** и проверяются на существование до подписи
     (fail closed);
   - в test-режиме в корни пишется `$testCertificate` — сертификат, созданный
     этим запуском, известный независимо от файла;
   - верификатору явно передаётся `--timestamp-roots`: корни TSA не
     наследуются от корней издателя.
2. **`.github/workflows/update-channel.yml`** — якоря берутся из новых секретов
   `UPDATE_CHANNEL_AUTHENTICODE_SIGNER_ROOTS` /
   `UPDATE_CHANNEL_AUTHENTICODE_TIMESTAMP_ROOTS`. Release-джоба читает **свой**
   секрет, а не артефакт, созданный шагом подписи, — подписант не может сам
   выбирать себе корень доверия. Все три входа доверия переведены на секреты.
3. **`infra/windows/tests/lint-engine.py`** — структурный запрет: строка с
   `$signature.` не может одновременно содержать `Export-HrmCertificatePem`,
   `--trust-roots` или `--timestamp-roots`. Негативная проверка выполнена:
   возврат старого паттерна даёт `FAIL … sign.ps1:388` и exit 1.
4. **`backend/tests/test_release_authenticode.py`** — тест
   `test_trust_roots_must_come_from_outside_the_artifact` фиксирует обе
   половины утверждения: самоcсылочный якорь принимает чужую подпись
   (предусловие), внешний якорь отвергает её с `untrusted_root`.

## 6. Проверено

- `pytest backend/tests/test_release_authenticode.py` → **25 passed**
- `ruff check` / `ruff format --check` → чисто
- `lint-engine.py` → 18 файлов, exit 0; на восстановленном дефекте → exit 1
- YAML всех трёх workflow парсится
- `git diff --check` → exit 0

## 7. Что осталось непроверенным

- Windows-путь (`New-SelfSignedCertificate`, `signtool`) в этой среде
  недоступен — PowerShell здесь нет. Исправление `sign.ps1` проверяется
  только структурным линтером; исполнение подтвердит лишь windows-джоба CI.
- Новые секреты `UPDATE_CHANNEL_AUTHENTICODE_*_ROOTS` владельцу ещё предстоит
  создать в environment `update-channel-signing`; до их создания production-путь
  откажет fail-closed (это намеренно, но требует действия владельца).

## 8. Рекомендация по PR #29

Дефект критический для смысла assurance, но не является «дырой для внешнего
злоумышленника» в production (нужен PFX из секрета). PR #29 сам по себе ничего
не ухудшает относительно базы. Рекомендую:

- либо влить PR #29 и применить исправление якорей отдельным PR,
- либо попросить агента 2 перенести этот фикс в свою ветку.

История агента 2 не менялась, merge не выполнялся.
