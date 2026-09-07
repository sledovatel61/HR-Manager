# Backup и восстановление (этап 7)

Документирует контур резервного копирования HR Manager: формат,
шифрование, расписания, retention, restore drill, RPO/RTO, ротацию ключей,
алерты и команды администратора.

## Инвариант безопасности

Backup содержит персональные данные и **является секретным активом**:

- публикуется только зашифрованным (формат `HRMBCK1`, AES-256-GCM);
- никогда не попадает в git, Docker-образы, CI-артефакты, логи или volume
  приложения — только в выделенный volume `backups` (контейнерный путь
  `/var/backups/hr-manager`);
- plaintext-стадия (дамп до шифрования) существует только внутри
  временного каталога с правами `0700`/файлов `0600` и удаляется сразу
  после публикации;
- ключ шифрования передаётся только через переменные окружения; потеря
  ключа = **невозможность восстановления** всех зашифрованных копий
  (см. «Ротация ключей»).

## Формат, имена и критерий успеха

- Дамп: `pg_dump` в **custom-формате** (`-Fc`, сжатие zlib) — совместим с
  `pg_restore` той же major-версии PostgreSQL (стек закрепляет PG 16;
  тулинг backup-образа собран из исходников ровно `16.15`).
- Шифрование: собственный контейнерный формат `HRMBCK1` — магическая
  сигнатура + JSON-заголовок (key id, версия формата, метаданные) +
  записи AES-256-GCM по 1 МиБ; заголовок входит в AAD каждой записи
  (подмена key id/метаданных = ошибка аутентификации). Рядом публикуется
  отсоединённый SHA-256 (`<файл>.sha256`).
- Имя файла: `hr-manager-YYYYMMDDTHHMMSSZ-<8 hex>.pgdump.enc` (UTC).
  Парсер распознаёт только этот шаблон — посторонние/частичные файлы
  никогда не считаются backup.
- **Критерий успеха** (только всё сразу, иначе exit != 0):
  1. `pg_dump` завершился с кодом 0 и дамп непустой;
  2. дамп зашифрован (проверка ключа на 32 байта ДО запуска);
  3. зашифрованный файл прошёл обратную проверку: аутентифицированное
     дешифрование в /dev/null + SHA-256 + отсоединённый checksum;
  4. файл атомарно опубликован (`os.replace`) в `BACKUP_DIR`;
  5. состояние записано в `BACKUP_STATE_FILE`.
- Частичный файл: staging-файлы лежат в отдельном каталоге и никогда не
  публикуются; при любом сбое staging удаляется, published-копии не
  трогаются.
- Lock: `flock` на `BACKUP_DIR/.backup.lock` — параллельный backup/drill
  невозможен (второй процесс падает с кодом 4, без порчи данных).
- Минимум свободного места: `BACKUP_MIN_FREE_MB` (по умолчанию 512 МиБ) —
  проверка `shutil.disk_usage` до дампа.

## Расписания и timezone

- **Все расписания — UTC.** Контейнеры получают `TZ=UTC`, логи
  проставляют метки `date -u`. Локальное время оператора не используется.
- Ежедневный backup: `BACKUP_SCHEDULE_UTC` (по умолчанию `02:00`, формат
  `HH:MM`).
- Restore drill: раз в `BACKUP_DRILL_INTERVAL_HOURS` (по умолчанию 168 ч =
  еженедельно), после успешного backup-окна.
- Retry/backoff: `BACKUP_RETRY_ATTEMPTS=3`,
  `BACKUP_RETRY_BACKOFF_SECONDS=300` (линейный backoff: 300 с, 600 с,
  900 с). Один request-id на всё окно — попытки связываются в аудите.
- `BACKUP_ON_START=1` (dev-стек) — немедленный backup при старте; в
  production `0` (только по расписанию/вручную).

## Retention

- `BACKUP_RETENTION_DAYS=7` (минимум 7; конфигурация с меньшим значением
  отклоняется) — удаляются только файлы старше политики **и** никогда не
  удаляются самые новые `BACKUP_MIN_COPIES=2` файла.
- Очистка выполняется только **после успешного** backup текущего запуска;
  при ошибке запуска ни одна копия не удаляется.
- Ручной запуск: `docker compose ... run --rm backup prune` (требует
  `--yes`).

## Шифрование и ротация ключей

- `BACKUP_KEY_ID` + `BACKUP_ENC_KEY` (base64, ровно 32 байта) — primary.
- `BACKUP_LEGACY_KEYS` — JSON `{key_id: base64}` ключей прошлых поколений;
  используются только для проверки/восстановления старых копий.
- **Ротация:** сгенерировать новый ключ, назначить его primary, старый
  поместить в `BACKUP_LEGACY_KEYS`. Старые копии проверяются и
  восстанавливаются по key id из заголовка. Ключ можно убрать из legacy
  только после того, как истекла политика хранения всех копий, им
  зашифрованных.
- Потеря ключа без копии = невозможность восстановления (AES-256-GCM,
  аутентифицированный; брутфорс не является планом восстановления).
- Production-guard: `Settings` и `infra/scripts/check_env.sh` (при
  `BACKUP_ENABLED=true`) отклоняют отсутствующие, короткие и
  development-only ключи.

## Restore drill (не имитация)

`BACKUP_DRILL_INTERVAL_HOURS` раз (или вручную `drill`) самый свежий backup
проходит полный цикл в **отдельной** базе `BACKUP_DRILL_DB_NAME`:

1. проверка checksum + аутентифицированное дешифрование;
2. `pg_restore` в новую базу (через superuser-соединение
   `BACKUP_DRILL_ADMIN_URL`; база создаётся на время drill и удаляется
   после);
3. миграции `alembic upgrade head` на восстановленной базе + сверка
   revision с ожидаемым head;
4. проверка наличия всех ключевых таблиц (`users`, `candidates`,
   `audit_log`, `events`, `candidate_terminations`, `analytics_facts`) и
   непустого `users`;
5. запуск приложения на свободном порту с `DATABASE_URL` на drill-базу и
   ожидание `/health == 200`;
6. cleanup: drill-база удаляется, временные файлы уничтожаются;
   production-база не затрагивается.

Результат (успех/провал, файл, таблицы, миграции, health) пишется в
`BACKUP_STATE_FILE` (`last_drill`) и в audit log.

## Проверка свежести и целостности

- `/ops/backup-health` — 200 только если: есть опубликованный backup,
  его возраст ≤ `BACKUP_MAX_AGE_HOURS` (26 ч), checksum сходится; иначе
  503 с причиной (не «фальшивый 200»).
- `/ops/status` — `backup.available/ok/size_bytes/age_seconds`,
  `backup.last_drill_ok`, `migrations.current_revision/expected_revision`,
  `release_sha`.
- `python -m app.cli backup-check [--deep]` — CLI-аналог; `--deep`
  дополнительно дешифрует в /dev/null (проверка ключа и целостности
  записей).

## Ручной backup перед опасной операцией

```bash
# внутри backup-контейнера (docker compose run):
docker compose -f infra/docker-compose.yml -f infra/compose.prod.yml \
  run --rm backup oneshot        # reason из BACKUP_REASON
# или с причиной и корреляцией:
docker compose ... run --rm backup sh -lc \
  'BACKUP_REASON="перед обновлением vX" python -m app.cli backup-now --as-scheduler --reason "перед обновлением vX"'
# через API администратора (audit + 202/409):
POST /admin/ops/backup {"reason": "...", "request_id": "..."}
```

Любой сбой завершается ненулевым кодом: `4` lock, `5` pg_dump, `6` ключ,
`7` проверка, `9` drill, `11` нездоровый backup. Причина и request-id
попадают в audit log; содержимое дампов, строки подключения и ключи — нет.

## RPO / RTO

| Метрика | Значение | Комментарий |
|---|---|---|
| RPO | ≤ 24 ч + время одного backup-окна (порядка минут) | ежедневный полный логический backup в 02:00 UTC; ручной backup перед операциями сокращает RPO до минут |
| RTO | ≤ 4 ч (типовой случай), зависит от размера базы | restore drill регулярно измеряет фактическое время полного цикла (pg_restore + миграции + health); цель — подтверждать ≤ 4 ч на реальных данных |
| Макс. потеря данных при потере ключа | 100 % зашифрованных копий | AES-256-GCM: без ключа восстановление невозможно |

## Алерты (severity / dedup / cooldown / действия)

| Алерт | Severity | Дедупликация | Cooldown | Действие дежурного |
|---|---|---|---|---|
| `/health` != 200 или БД недоступна | critical | по имени сервиса | 5 мин | проверить `docker compose ps`, логи backend/db; восстановить БД по плану RTO |
| backup просрочен (`/ops/backup-health` 503, age > порога) | critical | по причине (stale/missing/checksum) | 30 мин | запустить `backup oneshot`; проверить диск (`BACKUP_MIN_FREE_MB`), ключи, логи `backup`-сервиса |
| checksum/decrypt-проверка провалена | critical | по имени файла | 15 мин | НЕ удалять копии; проверить целостность носителя; взять предыдущую копию |
| restore drill провален | high | по ошибке (тип исключения) | 6 ч | разобрать логи drill; повторить `drill`; проверить `BACKUP_DRILL_ADMIN_URL` |
| миграция не совпадает с head (`/ops/status`) | high | по ревизии | 30 мин | прогнать `infra/scripts/migrate.sh check`; НЕ запускать автоматический downgrade |
| release SHA не совпал после деплоя | critical | по SHA | 15 мин | deploy.sh уже откатился автоматически; проверить логи деплоя |
| ошибки/латентность HTTP (Prometheus `/ops/metrics`) | warning/high | по route-лейблу | 10 мин | разбор по метрикам; без query/cookie/заголовков — только route |
| срок действия TLS-сертификата < 30 дней | high | по домену | 24 ч | продлить сертификат (см. `infra/nginx/README.md`) |

Экспорт метрик: `/ops/metrics` (Prometheus text format) — счётчики запросов
и длительность по **шаблону маршрута** (query-строки, cookies, заголовки и
тела не попадают в метрики). Стек ограничен Docker Compose — тяжёлая
observability-платформа не добавляется; точка интеграции с внешним
Prometheus/Alertmanager документирована (scrape `/ops/metrics`).

## Команды администратора (кратко)

```bash
# внутри контейнера backup:
backup-scheduler            # цикл по расписанию (ENTRYPOINT по умолчанию)
backup-scheduler oneshot    # немедленный backup с retry
backup-scheduler check      # глубокий backup-check
backup-scheduler drill      # restore drill сейчас
backup-scheduler list       # список недавних backup из state-файла
backup-scheduler prune      # retention вручную (--yes)

# CLI на хосте/в backend-контейнере:
python -m app.cli backup-now --actor <admin> --reason "..." --request-id "..."

# миграции (только до переключения трафика):
infra/scripts/migrate.sh up|check|current|history   # downgrade запрещён
```

## Ключи и переменные окружения

| Переменная | Назначение | Default |
|---|---|---|
| `BACKUP_DIR` | каталог зашифрованных копий | `/var/backups/hr-manager` |
| `BACKUP_STATE_FILE` | JSON-состояние (last_backup/last_drill/last_check/recent) | `/var/backups/hr-manager/state.json` |
| `BACKUP_RETENTION_DAYS` / `BACKUP_MIN_COPIES` / `BACKUP_MAX_AGE_HOURS` | политика хранения/свежести | 7 / 2 / 26 |
| `BACKUP_KEY_ID` / `BACKUP_ENC_KEY` / `BACKUP_LEGACY_KEYS` | ключи шифрования | — (обязательны) |
| `BACKUP_DRILL_ADMIN_URL` / `BACKUP_DRILL_DB_NAME` | drill-контур | — / `hr_manager_restore_drill` |
| `BACKUP_SCHEDULE_UTC` / `BACKUP_DRILL_INTERVAL_HOURS` | расписания (UTC) | `02:00` / `168` |
| `BACKUP_MIN_FREE_MB` | минимум места до дампа | 512 |
