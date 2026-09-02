# review-artifacts — материалы для сравнения реализаций агентов

Каталог содержит артефакты, которые GitHub App не может опубликовать
внутри `.github/workflows/` (у интеграции нет permission `workflows`,
любой push с изменением workflow-файлов отклоняется сервером).

## ci.agent-3.yml

Это **точная, без изменений** копия CI-конфигурации реализации агента №3
(Этап 1 — технический каркас). Байт-в-байт совпадает с проверенной
версией; контрольная сумма на момент публикации:

```
sha256: cecb71eb8238c976d0f375c8e8a3063471357a518f85ba2f0d2de86a18817007
```

### После выбора реализации (действие мейнтейнера)

Файл `review-artifacts/ci.agent-3.yml` должен быть перенесён
**без какого-либо изменения содержимого** в:

```
.github/workflows/ci.yml
```

Перенос выполняется коммитом через обычный git push токеном с правом
`workflows` (например, локальный push мейнтейнера) или через веб-интерфейс
GitHub:

```bash
mkdir -p .github/workflows
cp review-artifacts/ci.agent-3.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "ci: enable workflow for selected implementation"
```

До переноса CI не будет запускаться на GitHub — это ожидаемо; все команды
из workflow (`ruff`, `mypy`, `pytest`, `eslint`, `tsc`, `vitest`,
build, `docker compose up --build --wait` + `curl /health`) проверены
локально и задокументированы в `docs/reports/PHASE_1_REPORT.md`.
