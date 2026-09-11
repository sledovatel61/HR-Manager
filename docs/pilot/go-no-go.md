# Go / No-Go Checklist: HR Manager Windows Pilot

> **Назначение:** оператор принимает решение о запуске пилота (Phase 14) на конкретной машине.
> Чеклист применяется к **одной** пилот-хост машине, за него отвечает один оператор.
> Источники сигналов — `GET /readiness/pilot` (server-owned, admin) + `hrm status` (хост).
> Документация Runbook: `docs/runbook.md`. Drill: `infra/pilot/drill.py`.

**Как пользоваться:** проверь каждый блок последовательно (верх → вниз). `☐` → поставь `☑` когда зелёно.
Итог — `Go` только если секция **Blockers** пуста и **Warnings** — сознательно приняты. Печать → подпись → хранение.

---

## 1. Идентификация релиза (фиксируется)

- [ ] Machine / Hostname: `_____________________________`
- [ ] OS: Windows 10 22H2 / 11 23H2 (64-bit) — `winver` / `systeminfo`
- [ ] Installer: `HR-Manager-Setup-<version>.exe` — SHA256 сверен с `release-manifest.json` (`installer_exe.sha256`)
- [ ] Version / SHA: `0.__.__` / `____12hex____` (совпадают в `release.json`, `/api/ops/status.release_sha`, `hrm status --json.installed_release_sha`)
- [ ] Дата/время проверки (UTC): `_____________________________`
- [ ] Оператор: `_____________________________`

---

## 2. Preflight — обязательные требования хоста (fail closed если не выполнено)

| № | Проверка | Сигнал | Команда | Критерий |
|---|----------|--------|---------|----------|
| P1 | Windows версия | `ok/fail` | `systeminfo \| findstr /B /C:\"OS Name\"` | 10 22H2 или 11 23H2 |
| P2 | Docker Engine | `ok` | `docker --version` + `docker info` | `docker info` без ошибки, Engine ≥ 24 |
| P3 | Compose | `ok` | `docker compose version` | `≥ v2.24` |
| P4 | CPU/RAM/Диск | `ok/warning/fail` | `hrm status` / `GET /readiness/pilot → free_space` | `free_space: pass` (≥2 ГБ желательно, ≥1 ГБ обязательно) |
| P5 | Loopback | `pass/fail` | `docker compose -f infra/docker-compose.yml -f infra/compose.pilot.yml config` | только `127.0.0.1:${HRM_PILOT_PORT}:8080`, DB/backend `ports: !reset []` (нет `5432/8000`) |
| P6 | Порт свободен | `pass/fail` | `netstat -ano \| findstr LISTENING` | выбранный порт (напр. 8080) слушает только `127.0.0.1` после `hrm start` |

**No-Go если:** любой `fail` в P1–P6.

---

## 3. Инсталляция — чистота и детерминированность

- [ ] `HR-Manager-Setup-*.exe` скачан из `GitHub Release` (immutable) + проверены:
  - SHA256 установщика совпал (`Get-FileHash` vs `release-manifest.json`)
  - Authenticode (если требуется `HRM_REQUIRE_AUTHENTICODE_SIGNING=1`): `signtool verify /pa /all` или свойства → подпись `HR Manager` + timestamp. Для ephemeral-test: наличие `.signed` маркера.
  - Package детерминирован: `dist/channel/hr-manager-windows-*.zip` пересобирается с тем же SHA (`zipinfo -v`, `sha256sum`), `infra/release/build_package.py --help` → reproducible.
- [ ] InstallDir / StateDir / Staging — три разных каталога (Staging не внутри StateDir и не внутри backup volume), ACL раздельны (check readiness `staging: pass`).
- [ ] `hrm status` после install: `installed: yes` + `docker: ok` + `app: ready|starting`.

**No-Go если:** установщик не верифицирован (SHA или Authenticode когда требуется), staging внутри backup/secrets, `app: stopped/degraded` без причины.

---

## 4. Готовность (Readiness) — server-owned (админ, scope, CSRF, audit)

> Источник истины: `GET /readiness/pilot` (выполняется на сервере, без доверия клиенту).

```bash
curl -H \"X-CSRF-Token: $CSRF\" --cookie \"hrm_session=$SESSION\" https://app.onrender.com/readiness/pilot | jq
hrm status --json | jq '.trust_store, .channel, .backup, .version'
```

### 4a. Blockers (`status=fail` → **No-Go**)
- [ ] `database: pass` — `check_database` → `ok` (иначе `blocked`)
- [ ] `migration: ok` — `current_revision == expected_revision` (иначе `drift` → `blocked`)
- [ ] `release_trust: pass` — trust store валиден (1+ активный ключ, формат base64 32B, без private, не sole `pilot-test-key` в production), версия SemVer, SHA 40hex (иначе `blocked`)
- [ ] `staging: pass` — staging отделён от секретов/backup (иначе `blocked`)
- [ ] `rollback: pass` — есть проверенный `last_backup: ok` и staging writable (иначе `blocked` — безопасный rollback невозможен)
- [ ] `backup: pass|warning` — `fail` только если отсутствует/ошибке; `warning` (stale/drill) — см. §4b
- [ ] `free_space: pass` — ≥1 ГБ (иначе `blocked`), `≥2 ГБ` желательно

### 4b. Warnings (можно **Go с оговорками**, устрани за 7 дней)
- [ ] `offline` (channel unavailable) — `channel: warning` → не блок, приложение работает на текущей версии
- [ ] `smtp: warning` / `telegram: warning` — email/telegram не настроены → только in-app уведомления (ожидаемо для пилота)
- [ ] `backup: warning` — `stale` (>24h) или `restore drill` не выполнялся/устарел (>168h) → запусти `hrm backup-now` + `hrm restore-drill`
- [ ] `free_space: warning` — <2 ГБ но ≥1 ГБ → спланируй очистку
- [ ] `windows_docker: warning` — `APP_DEBUG=true` или env ≠ pilot/production → выключи debug, проверь `APP_ENV=pilot`

**Итог readiness verdict:**
- `ready` → **Go**
- `ready_with_warnings` → **Go с оговорками** (зафиксируй в журнале, к какому сроку устранишь каждый `warning`)
- `blocked` → **No-Go** (устрани каждый `fail`, повтори readiness)

---

## 5. Данные и доверие (Security)

- [ ] Trust store (`channel.json` / `trust_store.json`):
  - Только `key_id`+`fingerprint` (12hex) в диагностике, секреты отсутствуют (`secrets_redaction: pass`, `grep -R -i private` → 0).
  - Схема строгая: `key_id` `^[A-Za-z0-9._-]{1,64}$`, `key` base64 32B, `revoked` bool, лишние поля отклонены, private отклонён (fail closed).
  - Ротация: возможно двухключевое окно (2+ ключа, старый `revoked:false` + новый), затем отзыв старого.
- [ ] Loopback-модель: `pilot.env` — `HRM_PILOT_PORT` только `127.0.0.1`, `BACKUP_ENC_KEY` в StateDir, DB пароли не публикуются.
- [ ] Обновление канала: Ed25519 detached подпись обязательна (все manifest); Authenticode — опционально, fail closed когда требуется (`HRM_REQUIRE_AUTHENTICODE_SIGNING=1` → без подписи pipeline падает, installer подписывается и SHA256 пересчитывается, затем `signtool verify /pa /all`).

**No-Go если:** в логах/артефактах найден `private`, trust store содержит sole `pilot-test-key` в production, channel без подписи.

---

## 6. Резерв и восстановление (доказательство)

- [ ] `hrm backup-now` → `ok` → `hrm backup-state` показывает `last_backup: ok` и возраст <24h
- [ ] `hrm restore-drill` (или `python infra/pilot/drill.py`) → `ok` и `drill_age <168h` (readiness `backup: pass`)
- [ ] Backup volume `pilot_backups` отделён от `HRM_STAGING_DIR`; `backup_dir` имеет ≥2 ГБ свободно (readiness `free_space`)
- [ ] Rollback возможен: readiness `rollback: pass` (staging writable + backup `ok`)

**No-Go если:** `backup: fail` или `rollback: fail`.

---

## 7. Обновление и обратимость (проверено drill)

- [ ] `infra/pilot/drill.py` (репродуцируемо, идемпотентно) → `== DRILL PASSED ==`:
  - clean install → синтетика (3 кандидата) → encrypted backup/restore → signed update → данные сохранены
  - отклонены: `corrupted manifest` (bad_signature), `host не в allow-list` (bad_url), `Authenticode` (ephemeral) → все fail closed
  - bitый пакет → rollback → resume (переиспользует только валидный staging) → uninstall без purge → reinstall → данные сохранены
- [ ] Вручную (если drill ещё не гоняли): `hrm update` → applied → `hrm status` версия `match`; тест corrupted: подменить `update-channel.json.signature.value` → `hrm update` отклонён.

**No-Go если:** drill падает (exit 2) или rollback не восстановил данные.

---

## 8. Решение

### Go ✅ — пилот запускаем
*Условия:* §2 все `pass`, §4 verdict `ready` или `ready_with_warnings` с принятием рисков, §5-7 `Go`, blockers пусто.

Действия после Go:
1. Заполни журнал пилота (дата, версии, trust fingerprints, backup `at`).
2. Запусти `hrm start` и ежедневный мониторинг `hrm status` + `GET /readiness/pilot`.
3. Еженедельно: `hrm backup-now` + `hrm restore-drill`; `hrm update` когда канал доступен.

### No-Go ❌ — пилот НЕ запускаем
*Условие:* любой `fail` в §2/§4a/§5/§6/§7.

Действия:
1. Зафиксируй `fail` коды и `message_ru`/`next_action_ru` из readiness (`details` без секретов).
2. Устрани по runbook (`docs/runbook.md` §5) → повтори checklist.
3. Не удаляй backup volume до повторной проверки.

### Conditional Go ⚠️ — запуск с ограничениями
*Условие:* только `warnings` (offline кана�� / smtp+telegram not_configured / backup stale <48h).
Можно запустить, но повесь задачи: `_______________` (срок `_______`), `_______________` (срок `_______`).

---

## 9. Подпись

| Роль | Имя | Дата (UTC) | Решение |
|------|-----|------------|---------|
| Оператор пилота | | | Go / No-Go / Conditional |
| Владелец продукта (review) | | | Review: _____ |

> **Примечание:** секреты (Ed25519 private, PFX, `BACKUP_ENC_KEY`, `SECRET_KEY`) никогда не попадают в этот чеклист — только `key_id`/`fingerprint`/`sha_short`.
