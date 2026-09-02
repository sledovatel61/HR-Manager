# Review artifacts — реализация агента №2 (Этап 1, финальная доработка)

## Статус workflow на GitHub

GitHub App (`arena-ai-coding-agent[bot]`), через который публикуется ветка
`arena/phase-1-agent-2`, **не имеет разрешения `workflows`**: GitHub отклоняет
push, содержащий файлы в `.github/workflows/`. Поэтому в опубликованной ветке
каталога `.github/workflows/` нет, и GitHub Actions по этой ветке **не
запускается**. Это ограничение платформы, а не код репозитория.

**Важно:** файл в `review-artifacts` НЕ исполняется GitHub Actions — он
является точной копией рабочего workflow и должен быть перенесён владельцем.

## Действия для владельца репозитория (однократно)

Учётной записи с правами на запись в репозиторий:

```bash
git clone https://github.com/sledovatel61/HR-Manager.git
cd HR-Manager
git checkout -b arena/phase-1-agent-2-workflow origin/arena/phase-1-agent-2

# Вариант A — применить патч (добавляет .github/workflows/ci.yml):
git am --3way review-artifacts/ci.agent-2.patch

# Вариант B — простое копирование (эквивалентно):
# mkdir -p .github/workflows
# cp review-artifacts/ci.agent-2.yml .github/workflows/ci.yml
# git add .github/workflows/ci.yml && git commit -m "ci: publish agent-2 workflow"

git push -u origin arena/phase-1-agent-2-workflow
```

После этого:
1. Открыть Pull Request из `arena/phase-1-agent-2-workflow` (или обновить
   существующий PR #1), чтобы GitHub Actions запустился по ветке/PR.
2. Убедиться в зелёном прогоне job: `backend`, `frontend`, `integration`,
   `stack`.

Содержимое `.github/workflows/ci.yml` **не изменялось** при переносе —
проверка: `cmp review-artifacts/ci.agent-2.yml .github/workflows/ci.yml`.

## Содержимое каталога

| Файл | Назначение |
|---|---|
| `ci.agent-2.yml` | полная CI-конфигурация (4 job, включая compose stack smoke и конвейер миграций) — побайтовая копия `.github/workflows/ci.yml` |
| `ci.agent-2.patch` | git-патч (format-patch, применим через `git am`), добавляющий `.github/workflows/ci.yml` |
