# Phase 12 — локальная приёмка Windows update/rollback и uninstall

Дата проверки: 2026-09-10. Проверка выполнена на реальной Windows-машине с
Docker Desktop для release candidate `0.13.0`.

## Результат

Phase 12 локально принята и может быть закрыта.

- Доверенное обновление прошло успешно.
- Преднамеренно сломанное обновление завершилось ошибкой и автоматически
  восстановило прежний рабочий релиз.
- Прежние Docker-образы закрепляются тегами `:previous`, поэтому BuildKit не
  удаляет rollback target до завершения проверки нового релиза.
- Installer `0.13.0` пересобран; интерактивный install/uninstall smoke прошёл.
- Uninstall удалил каталог приложения и контейнеры, сохранив каталог состояния,
  PostgreSQL volume, backup volume и шесть зашифрованных backup-файлов.
- После uninstall контейнеров HR Manager не осталось.
- Исправлены ACL файлов состояния, очистка first-run token/artifacts, backup
  mount для diagnostics и удаление обновлённых файлов установщика.

## Артефакт

- Файл: `HR-Manager-Setup-0.13.0.exe`
- SHA256:
  `a9670b3921bd218f27cd571d7eba21775ba951c697d41b06f5cd798650e56a05`
- Хеш exe совпал с `installer/release-manifest.json`.
- Бинарный installer и пользовательские данные в git не добавлялись.

## Проверки

- PowerShell engine/static tests: **29 passed**.
- Frontend Vitest: **144 passed**.
- Frontend lint: успешно.
- Frontend production build: успешно.
- `git diff --check`: успешно.
- Untracked-файлов перед фиксацией не было.

Нативный запуск профильных backend-тестов в этой Windows-сессии блокировался
Unix-only модулем `fcntl`. Backend в acceptance hardening не изменялся; это
ограничение не выдавалось за успешный прогон. Backend/PostgreSQL проверки
следует повторно подтвердить Linux CI на итоговом SHA.

## Handoff

Принятый baseline опубликован в ветке
`phase12/windows-acceptance-final`. Следующий этап описан в
[`prompts/PHASE_13_PROMPT.md`](../prompts/PHASE_13_PROMPT.md).