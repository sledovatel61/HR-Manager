# План реализации направления №6 — Duotone Fintech

Концепт вынесен в статический прототип `design/redesign-agent3/`; боевые компоненты и API не менялись. Будущий PR должен реализовать победивший визуальный язык как restyle существующих компонентов, а не переносить эту демо-разметку в приложение.

## A. Semantic-токены

| Семантический токен | Светлая тема | Тёмная тема |
|---|---|---|
| `--surface-canvas / --surface-app / --surface-raised / --surface-sunken` | `#F4F1E8 / #FAF8F1 / #FFFDF7 / #EFECDF` | `#111C17 / #17241E / #1C2B24 / #131F19` |
| `--surface-sidebar` | `#183B33` | `#0C1712` |
| `--surface-hover / --surface-pressed` | `#EAE7DC / #DEDACB` | белый `6% / 10%` |
| `--surface-selected` | `#E2EBE3` | фирменный зелёный `18%` |
| `--text-primary / --text-secondary / --text-tertiary` | `#1A3029 / #4F6258 / #5D6B61` | `#F3F1E8 / #C4CEC4 / #9EABA0` |
| `--text-disabled / --text-inverse / --text-on-accent` | `#8A958C / #FFFDF7 / #FFFDF7` | `#718075 / #112018 / #0D1A13` |
| `--text-link / --text-sidebar` | `#235947 / белый 91%` | `#A8D2B3 / #EFF4ED` |
| `--border-subtle / --border-default / --border-strong` | `#E7E3D8 / #D8D4C7 / #B9B8AB` | белый `9% / 14% / 24%` |
| `--accent-default / --accent-hover / --accent-pressed` | `#235747 / #1C493B / #15382E` | `#91BD9E / #A7CFB1 / #C0DEC7` |
| `--accent-subtle / --accent-subtle-border` | `#E4ECE5 / #C8D8C9` | фирменный зелёный `14% / 30%` |
| `--status-info-{fg,bg,border}` | `#31596B / #E7EFF1 / #C8D9DE` | `#A6C9D7 / синий 20% / синий 36%` |
| `--status-success-{fg,bg,border}` | `#315A41 / #E7EEE6 / #CBDCC9` | `#A8D4B1 / зелёный 19% / зелёный 34%` |
| `--status-warning-{fg,bg,border}` | `#79591B / #F3EDDD / #E5D6AD` | `#E8CE8B / охра 20% / охра 38%` |
| `--status-danger-{fg,bg,border}` | `#883F37 / #F3E7E4 / #E5C8C2` | `#E4AAA0 / красный 20% / красный 37%` |
| `--status-neutral-{fg,bg,border}` | `#52635B / #ECECE6 / #D9DAD2` | `#C2CBC2 / белый 8% / белый 14%` |
| `--status-violet-{fg,bg,border}` | `#665176 / #EFEBF2 / #DAD0E3` | `#CBB4DD / фиолетовый 20% / фиолетовый 36%` |
| `--status-teal-{fg,bg,border}` | `#285A53 / #E5EFEB / #C1D9D0` | `#A4D3C4 / бирюзовый 20% / бирюзовый 36%` |
| `--status-indigo-{fg,bg,border}` | `#344F6B / #E8EDF2 / #CCD8E3` | `#B3C3DD / индиго 21% / индиго 36%` |
| `--focus-ring-color` | `#426E57` | `#B3D6BB` |

Шаги отступов, радиусы, размеры текста/контролов, длительности движения и `--sidebar-width` зеркально объявлены в `style.css`. Для боевой версии оставить системный стек без внешних шрифтов; serif-display из прототипа здесь деградирует до Georgia/Times, а переключатель «Системный» позволяет проверить его отдельно.

## B. CSS-файлы для restyle

- `frontend/src/design-system/tokens.css` — значения semantic-переменных в `:root` и `[data-theme="dark"]`, включая режимы плотности.
- `frontend/src/design-system/global.css` и `frontend/src/app-shell/workspace.css` — типографика, фон, навигационная оболочка, focus-ring и responsive-поведение.
- `frontend/src/design-system/components/{button,field,statusChip,tabs,toast,drawer,modal,stateViews}.css` — состояния основных контролов, чипов, drawer/modal, toast и skeleton.
- `frontend/src/features/candidates/{candidates,drawer,kanban}.css` — таблица, карточка кандидата и канбан.
- `frontend/src/features/analytics/analytics.css`, `frontend/src/features/calendar/calendar.css`, `frontend/src/features/schedule/schedule.css` — аналитика, календарь и график выхода.
- `frontend/src/features/documents/documents.css`, `frontend/src/features/document-templates/documentTemplates.css`, `frontend/src/features/library/library.css`, `frontend/src/features/notifications/notifications.css` — документы, библиотека и уведомления.

## C. Риски и границы

- Не менять API, бизнес-логику, маршруты, права доступа, валидацию и модели данных; в прототипе все данные демонстрационные.
- Не переносить HTML/JS прототипа в production. Реальные таблицы, формы, drag-and-drop и пустые/ошибочные состояния должны сохранить существующие контракты и клавиатурные альтернативы.
- Проверить контраст всех semantic-пар в обеих темах, `:focus-visible`, reduced motion, масштабирование 200% и узкие экраны; статус всегда дублируется подписью, а не только цветом.
- Внешние шрифты есть только в preview. В production не добавлять CDN/runtime-запросы и сохранить узнаваемую иерархию на системном стеке.
- Поддержать `comfortable/compact`, существующие размеры sidebar и нативный системный ввод дат/полей без новых зависимостей.

## D. Оценка

**Да, помещается в один PR.** Ориентир — 3–5 рабочих дней на restyle токенов и оболочки, затем таблицы/воронка/аналитика/формы и один проход по a11y + responsive QA. Изменения остаются в текущем React/CSS-стеке без новых runtime-зависимостей.
