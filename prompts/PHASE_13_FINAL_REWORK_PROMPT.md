# Phase 13 — финальная адресная доработка PR #23

Продолжи работу в существующем PR #23:

- репозиторий: `sledovatel61/HR-Manager`;
- ветка: `arena/01a084e4-hr-manager`;
- ожидаемый стартовый кодовый SHA: `8c7bf54218039c5fe2446ebbd5f498df0a943d2f`
  или более новый SHA, содержащий этот коммит;
- не создавай новый PR, не меняй base, не делай merge, rebase или force-push.

Сначала выполни fetch и fast-forward ветки, подтверди чистое дерево и стартовый
SHA. Прочитай текущие:

- `review-artifacts/update-channel.yml`;
- `review-artifacts/update-channel.patch`;
- `backend/tests/test_release_pipeline.py`;
- `docs/phase-13-report-arena.md`.

## Единственное замечание к коду

Значение `workflow_dispatch.inputs.notes_ru` передаётся в shell через `env`, но
затем небезопасно записывается в `$GITHUB_OUTPUT`:

```bash
notes="$INPUT_NOTES"
echo "notes=$notes" >> "$GITHUB_OUTPUT"
```

Dispatch input может содержать CR/LF. Дополнительная строка вида
`sha=<другой SHA>` способна стать отдельным workflow output и нарушить связь
проверенного checkout SHA с последующими шагами публикации.

### Что исправить

1. До любой записи `notes_ru` в `$GITHUB_OUTPUT` валидируй значение и fail
   closed отклоняй `\r` и `\n`. Контракт input уже говорит «одна строка».
2. Не заменяй это удалением `notes_ru`: русские release notes должны
   сохраниться.
3. Не вставляй `${{ inputs.* }}` непосредственно в `run:`. Вход по-прежнему
   должен поступать только через `env`.
4. Добавь regression-тест, который подтверждает:
   - CR и LF в `notes_ru` отклоняются до записи в `$GITHUB_OUTPUT`;
   - пользователь не может добавить output `sha`, `tag` или другой ключ через
     многострочные notes;
   - валидная однострочная русская заметка сохраняется.
5. Обнови одновременно:
   - `review-artifacts/update-channel.yml`;
   - `review-artifacts/update-channel.patch`;
   - `backend/tests/test_release_pipeline.py`;
   - отчёт/handoff, если формулировки изменились.
6. Убедись, что patch остаётся точной копией artifact workflow и проходит:

```bash
git apply --check review-artifacts/update-channel.patch
git diff --check
```

Не ограничивай тест простым поиском комментария. Тест должен проверять реальное
наличие fail-closed логики для CR/LF в release step до записи notes в
`$GITHUB_OUTPUT`. Если выберешь безопасное кодирование вместо запрета переносов,
покрой round-trip и невозможность подмены output отдельным исполняемым тестом.

## Проверки и отправка

Запусти целевые workflow/release tests, Ruff и все доступные проверки. Windows
ошибку импорта `fcntl` не обходи изменением production-кода: backend suite
подтверждается Linux CI. Закоммить и push изменения в ту же ветку, затем дождись
5/5 зелёных jobs на новом exact HEAD.

Если GitHub App снова не разрешит добавить
`.github/workflows/update-channel.yml`, не объявляй PR merge-ready. Оставь
проверенный artifact/patch и сообщи владельцу точный финальный SHA. Владелец
отдельно применит patch учётной записью с правом workflows, настроит environment
и secrets, проверит новый CI и выполнит ручную Windows-приёмку.

В финале сообщи кратко: final SHA, commit, результаты целевых тестов, ссылку на
CI exact HEAD и остался ли только owner-action по установке workflow.