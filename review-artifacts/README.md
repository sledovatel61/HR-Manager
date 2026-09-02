# Review artifacts — реализации AI-агентов

## Статус workflow на GitHub

GitHub App (`arena-ai-coding-agent[bot]`), через который публикуются ветки
`arena/*`, **не имеет разрешения `workflows`**: GitHub отклоняет любой push,
содержащий изменения файлов в `.github/workflows/`. Поэтому опубликованные
ветки агентов не содержат изменений CI, и GitHub Actions по этим веткам
**не запускается с обновлённым workflow**. Это ограничение платформы, а не
кода репозитория.

**Важно:** файлы в `review-artifacts` НЕ исполняются GitHub Actions — это
точные копии рабочих workflow, которые должен перенести владелец
репозитория.

## Агент №2 (этап 1)

| Файл | Назначение |
|---|---|
| `ci.agent-2.yml` | полная CI-конфигурация этапа 1 (4 job: backend, frontend, integration, stack) |
| `ci.agent-2.patch` | git-патч (format-patch), добавляющий `.github/workflows/ci.yml` |

Инструкция по переносу — в истории этого файла (вариант A: `git am`,
вариант B: копирование файла).

## Агент №4 (этап 2 — идентификация и безопасность)

| Файл | Назначение |
|---|---|
| `ci.agent-4.yml` | полная CI-конфигурация для этапа 2: то же, что `.github/workflows/ci.yml` в локальном коммите агента (preflight backend-job теперь требует `BOOTSTRAP_ADMIN_PASSWORD`; stack-валидация prod-оверлея экспортирует эту переменную) |
| `ci.agent-4.patch` | патч-диф относительно состояния этапа 1 (`git apply review-artifacts/ci.agent-4.patch`) |

Перенос владельцем (однократно, на ветке агента):

```bash
git checkout -b arena/phase-2-agent-4-workflow origin/arena/01a061ab-hr-manager
git apply review-artifacts/ci.agent-4.patch   # либо:
# cp review-artifacts/ci.agent-4.yml .github/workflows/ci.yml
git add .github/workflows/ci.yml
git commit -m "ci: publish agent-4 workflow (identity phase)"
git push -u origin arena/phase-2-agent-4-workflow
```

После этого открыть Pull Request, чтобы GitHub Actions запустился. Проверка
идентичности: `cmp review-artifacts/ci.agent-4.yml .github/workflows/ci.yml`.
