# Phase 13 — итоговая локальная приёмка

Дата проверки: 2026-09-11. Проверен HEAD
`ac4e7ec302915e36ec614893ebd4559020cea903` PR #23 до owner-коммита переноса
workflow.

## Вердикт

Phase 13 технически принята. Проверенный workflow из
`review-artifacts/update-channel.yml` перенесён владельцем byte-exact в
`.github/workflows/update-channel.yml`. Закрытый production signing key и иные
секреты в репозиторий не добавлялись.

## Подтверждённые результаты

- Backend unit suite в чистом Linux-контейнере Python 3.12:
  **595 passed, 105 deselected**.
- Ruff check и Ruff format check: успешно.
- Frontend ESLint, TypeScript, Vitest (**152 passed**), production build и
  audit: успешно.
- Windows engine tests и installer smoke: успешно.
- Docker Compose config validation: успешно.
- Workflow artifact/patch verification, включая реальный `git apply` и
  byte-exact сравнение: успешно.
- Installer `0.13.0`: silent install/uninstall успешно; размер
  **2 481 649 байт**, SHA-256
  `e3061636188ecb53a65a9e8455d7debc05ca2b33adc025a0879edbb743c75749`.
- `git diff --check`: успешно; рабочее дерево после проверок было чистым.
- GitHub CI точного SHA: все десять check runs (две матрицы по пяти jobs)
  завершились успешно, включая backend, PostgreSQL integration, frontend,
  Windows installer/engine и Compose smoke.

Нативный backend suite на Windows блокируется Unix-only модулем `fcntl`;
нормативный suite выполнен в Linux/Python 3.12. Первый Docker-запуск из Windows
worktree дал два ложных release-сбоя из-за Windows-пути в `.git`; повтор в
чистом Linux git repository прошёл полностью.

## Не выполнено и не выдано за passed

- локальные PostgreSQL integration-тесты;
- локальный живой Compose stack;
- ручной браузерный UI smoke;
- полный тест frontend на Node `22.22.3` (локально использован Node `24.14.0`,
  все проверки прошли, но npm сообщил `EBADENGINE` для `jsdom`);
- production release с настоящим ключом и ручной Windows end-to-end upgrade.

Эти пункты не отменяют техническую приёмку Phase 13: PostgreSQL/Compose и
Windows контуры подтверждены CI точного SHA. Production secrets, environment
protection, защита тегов и реальный выпуск относятся к owner/Phase 14
эксплуатационной приёмке.

## Owner actions перед первым production-выпуском

1. Создать GitHub environment `update-channel-signing`, разрешённый только для
   защищённого `main`, с required reviewers.
2. Внести secrets `UPDATE_CHANNEL_SIGNING_KEY`, `UPDATE_CHANNEL_KEY_ID` и
   `UPDATE_CHANNEL_PUBLIC_KEYS`; закрытый ключ хранить вне git и клиентских
   артефактов.
3. Ограничить создание SemVer-тегов правилом `v*` и запретить обход защиты.
4. Выполнить первый release только после Phase 14 drill и независимой проверки
   подписи/хешей опубликованных immutable assets.

## Handoff

Следующая работа описана в
[`prompts/PHASE_14_PROMPT.md`](../prompts/PHASE_14_PROMPT.md). Агент обязан
начать от актуального `origin/main`, содержащего merge Phase 13, и не должен
удалять ветку/PR/отчёты предыдущего этапа: они остаются аудируемым контекстом.