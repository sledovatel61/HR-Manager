# Purge runbook: переписывание истории, ротация и финальная проверка (продолжение containment)

Ты — исполнитель remediation. Предыдущий шаг выполнен и подтверждён ревьюером: tip `main`
очищен коммитом `c8067f3` (ровно четыре удаления), очищенный `main` влит в PR #57 обычным merge,
head PR = `258e14656e02092f8a951dec1017284e2a9b34a3`, CI 8/8, Windows runner 318/318.
**Containment закрыт, инцидент — нет.** Ниже — оставшиеся этапы. Часть из них может выполнить
только владелец (живая машина, push в `main`, уведомления) — твоя задача подготовить, проверить
и передать.

---

## Факты, установленные ревьюером (используй как исходные данные, не перепроверяй заново)

**Достижимость blob.** Все четыре blob достижимы ровно из **трёх** refs (проверено
`git merge-base --is-ancestor` по всем 71 refs репозитория):

| blob | путь | коммит-первоисточник | достижим из |
|---|---|---|---|
| `38be659b4c72656f76bafc65b455ffdb6130c074` | `tests/pr57-live-bb14399-evidence.zip` | `c2b1d29` | `main`, `arena/3cadc6a9-hr-manager`, `HEAD`(→main) |
| `8be0e32ad4d84171a7ec11124f429ea203746e19` | `HR-Manager-report-2026-10-14_14-45-31.zip` | `a391fed` | те же три |
| `76043c52c2094624efe69d769d49b7dc9b077d84` | `tests/PR57-reviewer-evidence-20261015-092151.txt` | `ed0f27e` | те же три |
| `7e9a6061defbd2238574cc89a7fa4c385d44936c` | `pr57-live-start.log` | `519b690` | те же три |

Остальные 68 refs не содержат ни один из этих blob — **чужие ветки трогать не нужно и нельзя**.

**Больше нигде.** Просмотрены все 3399 blob-объектов истории на предмет магии `PGDMP`, реальной
корпоративной почты, домена организации, кириллических имён и строк-маркеров артефактов.
Совпадения только в:

- четырёх артефактах инцидента (см. таблицу);
- `backend/tests/test_integration_backup.py` — синтетические фикстуры с магией `PGDMP`, штатное
  содержимое, удалять нельзя;
- именах и фамилии пилотного владельца в `docs/PILOT_FINAL_REPORT.md`, `docs/MARIA_GUIDE.md`,
  `docs/UPDATE_GUIDE.md`, `infra/windows/README.md`, `infra/windows/tests/pilot-final.tests.ps1`,
  `docs/CURRENT_STATUS.md`, `docs/RECOVERY_GUIDE.md`, `docs/handoff-release-0.14.0.md`,
  `docs/DOCKER_RUNTIME_DECISION.md`, `docs/UX_FEEDBACK_TASK_2026-09-29.md` и старых
  `prompts/BENTO_*` — это **штатное содержимое проекта с момента пилота**, не утечка из дампа.
  Удалять нельзя.

**Дополнительный объект для purge.** Ревьюер ненадолго закоммитил промпт, содержавший конкретные
PII-значения; коммит `af103fd9c1d4075266047315b19aad8552a61a74` и его blob
`446b9b416ce5f402370f42826d3671ab9912a0e6` удалены из достижимости force-push ветки
(`ba6442d1a8ab6660177cd45a94ce3ffeca3eea27`), но объекты могли остаться в кэше GitHub —
добавь их в заявку в Support.

---

## Часть 1. Переписывание истории (подготовка — ты, push в main — владелец)

1. Свежий клон, содержащий оба ref-а:
   ```bash
   git clone --no-local https://github.com/sledovatel61/HR-Manager.git purge-work
   cd purge-work
   git fetch origin "+refs/heads/main:refs/heads/main" "+refs/heads/arena/3cadc6a9-hr-manager:refs/heads/arena/3cadc6a9-hr-manager"
   ```
   (`--no-local` обязателен: иначе filter-repo откажется работать с hardlink-клоном.)
2. **Backup до любых изменений** (владельцу): `git bundle create pre-purge-<дата>.bundle --all`
   либо клон в отдельный каталог; записать SHA обоих ref-ов до перезаписи.
3. Один прогон `git filter-repo` по всем refs клона (по умолчанию filter-repo обрабатывает все
   refs — так main и ветка PR будут переписаны согласованно, с одинаковыми SHA общей истории):
   ```bash
   git filter-repo --invert-paths \
     --path tests/pr57-live-bb14399-evidence.zip \
     --path HR-Manager-report-2026-10-14_14-45-31.zip \
     --path tests/PR57-reviewer-evidence-20261015-092151.txt \
     --path pr57-live-start.log
   ```
   Никаких других `--path`, никаких `--replace-text`, никаких `--prune-empty=always`.
4. Проверка результата **до** любого push:
   ```bash
   # 4.1 ни один из четырёх путей не встречается ни в одном ref
   for p in tests/pr57-live-bb14399-evidence.zip HR-Manager-report-2026-10-14_14-45-31.zip \
            tests/PR57-reviewer-evidence-20261015-092151.txt pr57-live-start.log; do
     git log --all --oneline -- "$p" | head -1   # ожидание: пусто
   done
   # 4.2 blob-объектов с этими SHA нет среди достижимых
   for b in 38be659b4c72656f76bafc65b455ffdb6130c074 8be0e32ad4d84171a7ec11124f429ea203746e19 \
            76043c52c2094624efe69d769d49b7dc9b077d84 7e9a6061defbd2238574cc89a7fa4c385d44936c; do
     git rev-list --objects --all | grep -c "$b"   # ожидание: 0
   done
   # 4.3 дерево main не потеряло ничего, кроме этих четырёх путей
   git diff --stat <старый-main-SHA> main   # ожидание: ровно 4 удаления
   # 4.4 ничего чувствительного не осталось в достижимых блобах
   git rev-list --objects --all | awk '{print $1}' | git cat-file --batch-check='%(objectname) %(objecttype)' \
     | awk '$2=="blob"{print $1}' | git cat-file --batch | grep -c 'PGDMP'   # ожидание: только фикстуры тестов
   ```
5. Передать владельцу **точные** команды и новые SHA (его решение — выполнять):
   ```bash
   git push --force origin <новый-main-SHA>:refs/heads/main
   ```
   Владелец обязан сверить: `git ls-tree -r --name-only origin/main | grep -E 'pr57-live|HR-Manager-report|PR57-reviewer-evidence'` → пусто, и что дерево нового `main` отличается от старого ровно четырьмя удалениями.
6. После force-push владельцем:
   - `git fetch --prune origin`, локально `git reset --hard origin/main` (старые SHA недействительны);
   - ветку PR перезаписать: `git push --force origin <новый-SHA-ветки-PР>:refs/heads/arena/3cadc6a9-hr-manager`
     (это твоя ветка — force-push допустим; предупреди, что HEAD PR и все SHA коммитов PR изменятся,
     и прежние ссылки в описании PR нужно обновить);
   - дождаться нового CI на новом head и подтвердить 8/8 и 318/318;
   - PR остаётся **draft / NO-GO**.
7. Если force-push в `main` или в ветку PR по какой-то причине невозможен — **остановиться и
   доложить**, не пробовать обходные пути (не удалять ветки, не делать mirror-push, не трогать
   чужие refs).

---

## Часть 2. Ротация (только владелец; ты готовишь сценарий и проверяешь результат)

Считать данные раскрытыми независимо от исхода Части 1:

1. Пароль роли `hr_manager` — через штатный механизм движка (секрет в `StateDir/secrets.json` +
   синхронизация роли), не редактируя БД вручную, не коммитя пароль.
2. Отзыв и перевыпуск: `bootstrap_tickets`, `bootstrap_exchanges`, `candidate_*_tokens`,
   refresh/session-токены; проверить `audit_log` и `access_grants` на уже совершённые действия.
3. Проверка и ротация SMTP-учётных данных, `HRM_BACKUP_KEY` (старые backup без прежнего ключа не
   расшифровать — документированное ограничение), лицензионного материала.
4. Уведомление владельца корпоративной почты и ответственных за персональные данные;
   фиксация инцидента раскрытия PII и решение по внутренним/юридическим процедурам.
5. **Приватная приёмка после ротации** — сценарий без публикации артефактов: соединение из backend
   как `hr_manager` (без вывода секрета), публикация порта, health, состояние томов и ревизии,
   идемпотентный повторный старт. Всё оседает в ACL-ограниченном локальном каталоге;
   **в репозиторий не коммитится ничего** — ни дампов, ни логов, ни `process-info.json`.

---

## Часть 3. Заявка в GitHub Support и финальная проверка

Текст заявки (передай владельцу; SHAs уже собраны):

```
Repository: sledovatel61/HR-Manager (public)
Request: purge cached views / unreferenced objects for the following blobs and commits.
These were briefly published and have been removed from all reachable refs by history rewrite.

Blobs:
  38be659b4c72656f76bafc65b455ffdb6130c074  tests/pr57-live-bb14399-evidence.zip
  8be0e32ad4d84171a7ec11124f429ea203746e19  HR-Manager-report-2026-10-14_14-45-31.zip
  76043c52c2094624efe69d769d49b7dc9b077d84  tests/PR57-reviewer-evidence-20261015-092151.txt
  7e9a6061defbd2238574cc89a7fa4c385d44936c  pr57-live-start.log
  446b9b416ce5f402370f42826d3671ab9912a0e6  (temporary commit content, already unreachable)

Commits:
  c2b1d29053008450148f7323aed2cd4a50b2af93
  a391fed555928974bd45ad1ad7eefd9ed91eae6c
  ed0f27e25f79830e07c6714c0a1cf5d42c388cc0
  519b6905ee92067692728bc0e169c76eb5f10f8a
  c8067f3… (containment commit, superseded by the rewrite)
  af103fd9c1d4075266047315b19aad8552a61a74 (temporary commit, already unreachable)

Reason: the artifacts contained a PostgreSQL dump of a production pilot database
with personal data and an organizational email address. History has been rewritten;
please purge cached views and any archived copies.
```

Финальная проверка (выполняешь ты, после force-push):

```bash
git fetch --prune origin "+refs/heads/*:refs/remotes/origin/*"
# 1. ни один ref не содержит перечисленных blob
for b in 38be659b… 8be0e32a… 76043c52… 7e9a6061… 446b9b41…; do
  git rev-list --objects --all | grep -c "$b"   # ожидание: 0
done
# 2. путей нет в истории
git log --all --oneline -- tests/pr57-live-bb14399-evidence.zip   # ожидание: пусто
# 3. в дереве main нет артефактов
git ls-tree -r --name-only origin/main | grep -E '\.(dump|zip|log)$|evidence'   # ожидание: пусто
# 4. hygiene-проверка лицензионного job проходит
git ls-files -ci --exclude-standard   # ожидание: пусто
# 5. GitHub API: дерево main и поиск по SHA blob
gh api repos/sledovatel61/HR-Manager/git/trees/main?recursive=1 | grep -E 'pr57-live|HR-Manager-report|PR57-reviewer'   # ожидание: пусто
```

Если после пунктов 1–5 хоть один артефакт обнаружится — инцидент не закрыт, доложить владельцу
с точными ref-ами, в которых он остался.

---

## Запреты

- **Не** мержить PR #57 и **не** выпускать релиз до завершения Частей 1–3 и ротации.
  Статус PR — **draft / NO-GO**.
- **Не** коммитить в репозиторий дампы, `pg_dump`, engine-логи, `process-info.json`, bundle-архивы
  и любые evidence; они остаются в ACL-ограниченном локальном каталоге.
- **Не** удалять и не переписывать штатное содержимое проекта: `docs/*` с именем пилотного
  владельца, `backend/tests/test_integration_backup.py`, `infra/release/testdata/*`,
  `review-artifacts/*`, `design/*`, `.env.example`, тестовые фикстуры с `C:\Users\…`.
- **Не** трогать ref-ы других сессий (68 ветвей, не содержащих blob-ов) и не делать mirror-push.
- **Не** ослаблять hygiene-проверку и **не** добавлять отрицания в `.gitignore`.
- **Не** включать конкретные PII-значения (почта, имена, телефоны, домен организации) ни в какие
  файлы репозитория, Issue, PR или комментарии — только структурные факты и SHA.
- Код, тесты, compose, префлайт и механику пароля в коммитах remediation не менять.

## Acceptance

1. В свежем клоне ни один ref не содержит четырёх путей и четырёх blob (плюс `446b9b41`);
   `git log --all -- <путь>` пуст для каждого.
2. Дерево нового `main` отличается от прежнего ровно четырьмя удалениями; иных изменений нет.
3. Ветка PR перезаписана, новый head, CI 8/8 и 318/318; описание PR обновлено (SHA, инцидент,
   статус ротации).
4. Владельцу переданы: команда force-push `main`, новый SHA, текст заявки в Support, сценарий
   приватной приёмки после ротации.
5. Ротация `hr_manager`, токенов и интеграций выполнена владельцем; приватная приёмка пройдена
   без публикации артефактов.
6. Финальная проверка (пункты 1–5 Части 3) чистая; заявка в Support отправлена.
7. PR #57 — **draft / NO-GO**; мерж и релиз не выполнялись.
