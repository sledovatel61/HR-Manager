# Runtime screenshots — follow-up к PR #50

**Не production-данные.** Все кандидаты/события с префиксом `ТЕСТ` созданы изолированной fixture. Это настоящие screenshots Chromium 153 текущего frontend с реальным FastAPI API, cookie login/CSRF и in-memory SQLite (`APP_ENV=test`), без mock responses. Исходные снимки пользователя находятся в соседнем `после PR50/` и не изменены.

[Полный отчёт и команды воспроизведения](../../docs/PR50_FOLLOWUP_ACCEPTANCE.md) · [12 наборов измерений](measurements.json) · [DnD/select/автопрокрутка](interactions.json)

Viewport: desktop **1440×1000**, narrow **390×844**. Comfortable density, reduced motion. Чек-лист показан в честном пустом состоянии; файл DOCX действительно загружен и скачан через API. Все снимки — одного проверяемого набора изменений, а не ретушь пользовательских references.

| Роль / тема / ширина | Список | Drawer | Чек-лист | Файлы | Kanban начало | Kanban конец | Уведомления (5) | График |
|---|---|---|---|---|---|---|---|---|
| hr / light / 1440 | [PNG](hr-light-1440-candidates.png) | [PNG](hr-light-1440-drawer.png) | [PNG](hr-light-1440-checklist.png) | [PNG](hr-light-1440-files.png) | [PNG](hr-light-1440-kanban-start.png) | [PNG](hr-light-1440-kanban-end.png) | [PNG](hr-light-1440-notifications.png) | [PNG](hr-light-1440-schedule.png) |
| hr / light / 390 | [PNG](hr-light-390-candidates.png) | [PNG](hr-light-390-drawer.png) | [PNG](hr-light-390-checklist.png) | [PNG](hr-light-390-files.png) | [PNG](hr-light-390-kanban-start.png) | [PNG](hr-light-390-kanban-end.png) | [PNG](hr-light-390-notifications.png) | [PNG](hr-light-390-schedule.png) |
| hr / dark / 1440 | [PNG](hr-dark-1440-candidates.png) | [PNG](hr-dark-1440-drawer.png) | [PNG](hr-dark-1440-checklist.png) | [PNG](hr-dark-1440-files.png) | [PNG](hr-dark-1440-kanban-start.png) | [PNG](hr-dark-1440-kanban-end.png) | [PNG](hr-dark-1440-notifications.png) | [PNG](hr-dark-1440-schedule.png) |
| hr / dark / 390 | [PNG](hr-dark-390-candidates.png) | [PNG](hr-dark-390-drawer.png) | [PNG](hr-dark-390-checklist.png) | [PNG](hr-dark-390-files.png) | [PNG](hr-dark-390-kanban-start.png) | [PNG](hr-dark-390-kanban-end.png) | [PNG](hr-dark-390-notifications.png) | [PNG](hr-dark-390-schedule.png) |
| admin / light / 1440 | [PNG](admin-light-1440-candidates.png) | [PNG](admin-light-1440-drawer.png) | [PNG](admin-light-1440-checklist.png) | [PNG](admin-light-1440-files.png) | [PNG](admin-light-1440-kanban-start.png) | [PNG](admin-light-1440-kanban-end.png) | [PNG](admin-light-1440-notifications.png) | [PNG](admin-light-1440-schedule.png) |
| admin / light / 390 | [PNG](admin-light-390-candidates.png) | [PNG](admin-light-390-drawer.png) | [PNG](admin-light-390-checklist.png) | [PNG](admin-light-390-files.png) | [PNG](admin-light-390-kanban-start.png) | [PNG](admin-light-390-kanban-end.png) | [PNG](admin-light-390-notifications.png) | [PNG](admin-light-390-schedule.png) |
| admin / dark / 1440 | [PNG](admin-dark-1440-candidates.png) | [PNG](admin-dark-1440-drawer.png) | [PNG](admin-dark-1440-checklist.png) | [PNG](admin-dark-1440-files.png) | [PNG](admin-dark-1440-kanban-start.png) | [PNG](admin-dark-1440-kanban-end.png) | [PNG](admin-dark-1440-notifications.png) | [PNG](admin-dark-1440-schedule.png) |
| admin / dark / 390 | [PNG](admin-dark-390-candidates.png) | [PNG](admin-dark-390-drawer.png) | [PNG](admin-dark-390-checklist.png) | [PNG](admin-dark-390-files.png) | [PNG](admin-dark-390-kanban-start.png) | [PNG](admin-dark-390-kanban-end.png) | [PNG](admin-dark-390-notifications.png) | [PNG](admin-dark-390-schedule.png) |
| manager / light / 1440 | [PNG](manager-light-1440-candidates.png) | [PNG](manager-light-1440-drawer.png) | [PNG](manager-light-1440-checklist.png) | [PNG](manager-light-1440-files.png) | [PNG](manager-light-1440-kanban-start.png) | [PNG](manager-light-1440-kanban-end.png) | [PNG](manager-light-1440-notifications.png) | [PNG](manager-light-1440-schedule.png) |
| manager / light / 390 | [PNG](manager-light-390-candidates.png) | [PNG](manager-light-390-drawer.png) | [PNG](manager-light-390-checklist.png) | [PNG](manager-light-390-files.png) | [PNG](manager-light-390-kanban-start.png) | [PNG](manager-light-390-kanban-end.png) | [PNG](manager-light-390-notifications.png) | [PNG](manager-light-390-schedule.png) |
| manager / dark / 1440 | [PNG](manager-dark-1440-candidates.png) | [PNG](manager-dark-1440-drawer.png) | [PNG](manager-dark-1440-checklist.png) | [PNG](manager-dark-1440-files.png) | [PNG](manager-dark-1440-kanban-start.png) | [PNG](manager-dark-1440-kanban-end.png) | [PNG](manager-dark-1440-notifications.png) | [PNG](manager-dark-1440-schedule.png) |
| manager / dark / 390 | [PNG](manager-dark-390-candidates.png) | [PNG](manager-dark-390-drawer.png) | [PNG](manager-dark-390-checklist.png) | [PNG](manager-dark-390-files.png) | [PNG](manager-dark-390-kanban-start.png) | [PNG](manager-dark-390-kanban-end.png) | [PNG](manager-dark-390-notifications.png) | [PNG](manager-dark-390-schedule.png) |

## После «Прочитать все»

Реальный `POST /notifications/mark-all-read` → 204, GET списка/счётчика → badge **0**, `is-unread` отсутствует.

- [hr, dark, 1440](hr-dark-1440-notifications-read.png)
- [admin, dark, 1440](admin-dark-1440-notifications-read.png)
- [manager, dark, 1440](manager-dark-1440-notifications-read.png)

Всего 99 PNG. Локальный браузерный прогон не подменяет PostgreSQL/Windows acceptance или удалённый CI.
