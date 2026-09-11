# Runbook: HR Manager Windows Pilot — эксплуатация одним оператором

> **Статус:** Phase 14, `HR-Manager` @ `pilot`. Документация для единственного оператора пилота
> (без удалённого запуска). Любому работнику — только владение собственными данными для
> исполнения обязанностей (группы/календарь/kanban). Loopback-модель: приложение доступно
> только на `127.0.0.1`.

## 1. Предпуск (Preflight) — за 5 минут

### Требования пилота
| Компонент | Требование |
|---|---|
| ОС | Windows 10 22H2 или Windows 11 23H2 (64-bit) |
| Docker | Docker Desktop 4.24+ (Engine ≥ 24, Compose v2 ≥ 2.24) |
| CPU/RAM/Диск | 2 vCPU / 4 ГБ RAM / 20 ГБ свободного (`≥2 ГБ` для rollback) |
| Сеть | исходящий HTTPS для канала обновлений (allow-list в `channel.json`), необязательно SMTP/Telegram |
| Браузер | Edge/Chrome актуальный, доступ к `http://127.0.0.1:${HRM_PILOT_PORT}` |

### Проверка за 30 секунд (один оператор)
```powershell
# Версии должны быть ≥ требуемых
docker --version          # ≥ 24
docker compose version    # ≥ v2.24
docker info | Select-String -Pattern \"Server Version\"

# Права: PowerShell в обычном режиме (не обязательно Admin, кроме установки службы)
$PSVersionTable.PSVersion  # ≥ 5.1
```

### Где логи и диагностика (сразу)
| Вопрос | Команда |
|---|---|
| Куда пишет? | `%LOCALAPPDATA%\\HRManager\\logs\\*.log` (ротация 10 МБ × 5 файлов, UTF-8) |
| Диагностика? | `hrm status` / `hrm status --json` — секреты отредактированы (`***`, key `pilot-test-key` + fingerprint) |
| Здоровье? | `http://127.0.0.1:${HRM_PILOT_PORT}/api/ops/status` + `.../backup-health` |
| Бэкапы? | `hrm backup-now` + `hrm backup-state` (последний `ok`, возраст < 24 ч) |
| Готовность? | `GET /readiness/pilot` → `ready` / `ready_with_warnings` / `blocked` (admin, см. § 4) |

---

## 2. Установка (Clean Install) — один оператор

```powershell
# 1) Скачать Setup
#    https://github.com/<owner>/HR-Manager/releases — HR-Manager-Setup-0.13.0.exe (+ .signed если Authenticode)
# 2) Проверить подпись (если подписано) и хеш
(Get-FileHash .\\HR-Manager-Setup-0.13.0.exe -Algorithm SHA256).Hash
#    сравнить с release-manifest.json → installer_exe.sha256
#    + Authenticode: правый клик → Свойства → Цифровые подписи (publisher=HR Manager, timestamp)
#    или: signtool verify /pa /all HR-Manager-Setup-0.13.0.exe

# 3) Запустить установщик (Next → Install). Принимает:
#    - InstallDir (по умолч. %LOCALAPPDATA%\\HRManager\\App)
#    - StateDir   (по умолч. %LOCALAPPDATA%\\HRManager) — пароли/pilot.env/канал
#    - Staging    (по умолч. %LOCALAPPDATA%\\HRManagerStaging) — НЕ внутри StateDir и не внутри backup
#    - Порт       (по умолч. авто-выбор из 8080..8095)

# 4) Что происходит под капотом (детерминированно):
#    - CopySnapshot → InstallDir
#    - InitializeStateDir → StateDir\\pilot.env (случайные пароли 1 раз, масс-80 фикс)
#    - Trust store: если installer содержит trust_store.json → channel.json (strict validation)
#    - docker compose up -d --build (из исходников)

# 5) Проверка post-install
hrm status
hrm backup-now            # первый зашифрованный backup (AES-256-GCM, ключ в StateDir)
curl -s http://127.0.0.1:8080/api/health | jq
docker compose -f %LOCALAPPDATA%\\HRManager\\App\\infra\\docker-compose.yml -f %LOCALAPPDATA%\\HRManager\\App\\infra\\compose.pilot.yml ps
```

**Детерминированность:** сборка `dist/channel/hr-manager-windows-*.zip` не зависит от времени (zip deterministic, `zipinfo -v` / `sha256sum`); публикация канала использует pinned `Inno Setup 6.7.3` (`SHA256 9c73c3…732`).

**Trust store distribution:** публикуется только публичный набор `{key_id:{key,revoked}}` (base64 32 байта, без private/PEM). Встроен в пакет как `trust_store.json` и сверяется с `UPDATE_CHANNEL_PUBLIC_KEYS` сервера; `--require-production` отклонит единственный `pilot-test-key` в продукте. Ротация — двухключевое окно (старый + новый), затем revoke.

**Authenticode:** опционально, fail closed когда требуется. Сертификат хранится в GitHub Environment `update-channel-signing` (`AUTHENTICODE_CERTIFICATE_BASE64`, `AUTHENTICODE_PASSWORD`, `HRM_REQUIRE_AUTHENTICODE_SIGNING=1`), никогда в CLI/логах. Верификация: `signtool verify /pa /all`. После подписи SHA256 пересчитывается. Для PR-без-секретов: эфемерный самоподписанный `.signed` маркер (только для теста контракта).

---

## 3. Ежедневная эксплуатация (1 оператор)

### Запуск / остановка / логи
```powershell
hrm start                 # compose up -d
hrm stop                  # compose down (данные сохранены)
hrm logs                  # хвост логов всех сервисов
hrm logs --follow backend # follow одного сервиса
hrm restart backend
hrm ps                    # docker ps с фильтром проекта
```

### Обновление (по подписанному каналу)
```powershell
hrm update                # 1) health-gate → 2) load manifest (HTTPS + allow-list) → 3) verify Ed25519 →
                          # 4) download → 5) SHA256+size → 6) apply → 7) health-gate → 8) rollback при падении
# Опционально: + Authenticode verify если installer подписан
hrm update --preview      # канал preview (отдельный URL, если настроен)
hrm update --rollback     # принудительный откат к предыдущей версии (если rollback доступен)

# Резервный путь: если канал offline — hrm update вернёт channel_offline (warning), приложение остаётся на текущей версии
```

### Бэкап и restore drill
```powershell
hrm backup-now            # зашифрованный pg_dump → pilot_backups volume, проверка freshness (default 24h)
hrm backup-state          # показывает last_backup (ok/stale/failed) и last_drill
hrm restore-drill         # расшифровывает последний backup в изолированную БД, сверяет восстановление
# Рекомендация: restore drill еженедельно (readiness проверяет drill_ok и drill_age < 168h)
```

### Миграции
```powershell
hrm migrate               # приводит БД к head (идемпотентно)
hrm migrate --check       # только проверка (exit 1 при drift)
```

---

## 4. Наблюдаемость (что и где смотреть)

### `GET /readiness/pilot` (server-owned, admin, scope `pilot_full_access|update_channel_manage`)
```bash
# Запрос от admin (cookie + CSRF):
curl -H "X-CSRF-Token: $CSRF" --cookie "hrm_session=$SESSION" https://app.onrender.com/readiness/pilot | jq
# Ответ (admin, redacted):
# { verdict: "ready"|"ready_with_warnings"|"blocked", generated_at, checks: [{code,status("pass"|"warning"|"fail"),message_ru,next_action_ru,details}]}
# Наблюдай: verdict=ready → пилот готов; ready_with_warnings → можно, но устрани (smtp/telegram/offline); blocked → действует, но фатальных нет в пилоте без данных
# Details не содержат секретов (только key_id/fingerprint/sha_short/age_hours)
```

### `hrm status` (Windows, redacted)
```powershell
hrm status                # docker/app/database/migration/worker/backup/version/smtp/telegram/channel/trust_store
hrm status --json         # JSON (redacted)
# Примеры строк (секреты → ***):
#   docker      : ok
#   app         : ready
#   database    : ok
#   migration   : ok (в базе abc123, ожидается abc123)
#   worker      : ok
#   backup      : ok            # failed/missing/stale → действуй (см. runbook)
#   version     : match (установлено a1b2c3, в работе a1b2c3)
#   channel     : configured    # not_configured/offline → warning (не фатал)
#   trust_store : configured (revoked 0)
#     key pilot-release-2026: a1b2c3d4e5f6 revoked=False
```

### Эндпоинты наблюдаемости (без секретов)
| URL | Что |
|---|---|
| `/health` | `{status, uptime}` |
| `/readyz` | readiness probe |
| `/api/ops/status` | database/migrations/worker/backup/release_sha (200 или 503 при деградации) |
| `/api/ops/backup-health` | `fresh/age_hours/last_backup_at` (200 fresh, 503 stale) |
| `/metrics` | Prometheus (если включён) |
| `/readiness/pilot` | расширенная диагностика пилота (admin only, выше) |

### Dashboard (frontend)
* **Раздел «Готовность пилота»** (hash `#/readiness`, доступен manager/admin): список проверок с `pass/warning/fail`, `message_ru` и `next_action_ru`. Offline/SMTP/Telegram — жёлтый `warning`, не блок.

---

## 5. Откат и неполадки (Fail closed)

### Автоматический rollback (сам)
* Рекомендация канала устанавливает `update_state.json` (attempted/at). Windows-движок скачивает → проверяет подпись → SHA256/размер → применяет → `health-gate` (Docker/DB/migrations/worker). При `fail` → откат к предыдущей версии + восстановление backup, сигнатура `rollback`.

### Ручной откат
```powershell
hrm update --rollback     # откат к N-1
hrm logs                  # причина падения (без секретов — ***)
docker compose logs backend --tail 100
```

### Прерывание / Resume
* Скачивание пакета — в `%LOCALAPPDATA%\\HRManagerStaging`. Частичный `.part` не считается релизом.
* Повтор `hrm update` после обрыва — reuse существующего файла только если размер+SHA256 совпадают; иначе атомарная замена.
* Неполные файлы автоматически удаляются при следующей попытке.

### Недостаток места / старый backup
* Readiness `free_space: fail` при `<1 ГБ` → освободи (удали старые образы `docker system prune`, ротируй `pilot_backups`).
* `backup: stale/failed/missing` → `hrm backup-now`, проверь `BACKUP_ENC_KEY`, наличие `pg_dump`, права тома.
* Readiness `rollback: fail` → безопасный rollback недоступен → устраняй backup+staging до обновления.

### Канал
* `channel_offline` → канал недоступен (сеть/TLS) — warning, не блок. Приложение продолжает. Попробуй позже / проверь `UPDATE_CHANNEL_URL`, `channel.allowed_hosts`.
* `bad_signature/unknown_key/revoked_key` → подпись отклонена (fail closed, до распаковки). Обновление не применяется. Проверь `UPDATE_CHANNEL_PUBLIC_KEYS` на сервере и `channel.json` на клиенте.
* `bad_url` / host не входит в политику → отклонено (только `https` + allow-list).

---

## 6. Деинсталляция и переустановка (без потери данных)

```powershell
# Без удаления данных (рекомендуется для пилота: объёмов не трогаем)
hrm uninstall              # compose down, шифрованные тома сохранены (StateDir + pilot_backups)

# Переустановка (данные восстанавливаются)
hrm install                # или повторный запуск Setup.exe → укажи те же InstallDir/StateDir

# Полная очистка (только по требованию, с подтверждением)
hrm uninstall --purge      # удаляет StateDir и backup volume — БЕЗВОЗВРАТНО
```

**Гарантия:** `uninstall` без `--purge` сохраняет `%LOCALAPPDATA%\\HRManager` (secrets, channel.json, backup_state) и volume `pilot_backups`. Повторная установка находит их и продолжает с того же состояния. `hrm backup-state` после переустановки покажет прежний `last_backup`.

---

## 7. Безопасность (что нельзя)

* Хост **не открывает** DB/backend наружу: в `compose.pilot.yml` только `127.0.0.1:${HRM_PILOT_PORT}:8080`, остальные `ports: !reset []`. Проверка: `docker compose config` не должен показывать `0.0.0.0:5432` / `8000`.
* Секреты **никогда** не в логах/артефактах: логи → `Protect-HrmOutput` (`***`), trust store → только `key_id`+`fingerprint`, `private` никогда. Проверка: `grep -R -i private dist/channel/ installer/output/` → 0.
* `HRM_STATE_DIR` и `HRM_STAGING_DIR` — разные каталоги, staging вне backup volume (раздельные ACL).
* Подписные секреты — только в GitHub Environment `update-channel-signing`, изолированы от PR/fork (нет `pull_request` триггера).

---

## 8. Доказательства (Evidence) для отчёта

Собираются idempotent-скриптом `infra/pilot/drill.py` (репродуцируемо, без PII):

```bash
python infra/pilot/drill.py          # полный прогон (см. § 2–6, fail closed)
python infra/pilot/drill.py --quick  # быстро
# Вывод: == DRILL PASSED == (versions, trust fingerprints, data 3 candidates, backup decrypt, channel verify, rollback)
```

Артефакты drill (опционально): `dist/channel/SHA256SUMS` (после подписи), `hrm status --json`, `/readiness/pilot` (redacted).

---

## 9. Контакты и эскалация (пилот)

| Ситуация | Делай |
|---|---|
| `hrm status` → `backup: failed` или `app: degraded` | `hrm logs` → собери `diagnostics.json` → перезапусти `hrm restart` |
| Канал отозван (`revoked_key`) | обнови `channel.json` новым публичным ключом (двухключевое окно), не публикуй private |
| Место < 1 ГБ | `docker system prune -a` (осторожно) + ротация backup |
| Потеря доступа к admin | восстанови пароль через `setup-owner` (только локально, loopback) |

> **Правило пилота:** при любом сомнении — не теряй данные. Используй `hrm backup-now` перед рискованными действиями и держи `HMAC`/`SECRET_KEY` вне репозитория (только `StateDir`).
