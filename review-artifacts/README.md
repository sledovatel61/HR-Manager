# Review artifacts — реализация агента №2 (Этап 1)

## Зачем этот каталог

Учётная запись GitHub App, через которую публиковалась эта ветка, не имеет
разрешения `workflows`, поэтому GitHub отклоняет любой push, который создаёт
или изменяет файлы в `.github/workflows/`. Именно поэтому в ветке
`arena/phase-1-agent-2` **нет** каталога `.github/workflows/` — CI-конфигурация
лежит здесь, в обычном (разрешённом для push) файле.

## Как включить CI после выбора реализации

После того как данная реализация будет выбрана основной, выполните
**без каких-либо изменений содержимого**:

```bash
mkdir -p .github/workflows
cp review-artifacts/ci.agent-2.yml .github/workflows/ci.yml
```

То есть файл `review-artifacts/ci.agent-2.yml` переносится в
`.github/workflows/ci.yml` в неизменном виде (побайтово), после чего коммит
выполняется аккаунтом, у которого есть разрешение `workflows`.

## Содержимое

| Файл | Назначение |
|---|---|
| `ci.agent-2.yml` | полная CI-конфигурация реализации агента №2: 4 задачи — backend (ruff, mypy, pytest), frontend (eslint, tsc, vitest, build), integration (pytest против PostgreSQL 16), stack (compose smoke: `up --build`, health 200, деградация 503) |
