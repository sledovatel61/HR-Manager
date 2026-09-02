# Review artifacts — реализация агента №2 (Этап 1, после remediation)

## Зачем этот каталог

Учётная запись GitHub App, через которую публиковалась ветка
`arena/phase-1-agent-2`, не имеет разрешения `workflows`, поэтому GitHub
отклоняет любой push, который создаёт или изменяет файлы в
`.github/workflows/`. В публикуемой ветке каталога `.github/workflows/` нет —
CI-конфигурация лежит здесь, в обычном (разрешённом для push) файле.

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
| `ci.agent-2.yml` | полная CI-конфигурация реализации агента №2 (после remediation): backend (ruff check + format, mypy, pytest, проверки production preflight), frontend (eslint, tsc, vitest, build, npm audit), integration (pytest против PostgreSQL 16 + конвейер миграций), stack (валидация dev/prod Compose-конфигураций, `up --build --wait`, health 200, деградация 503, очистка `if: always()`) |

Копия синхронизирована с `.github/workflows/ci.yml` побайтово
(`diff` подтверждён при remediation).
