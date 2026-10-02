# План внедрения: Bento Showcase, палитра «Лазурь» (`bento-arctic.html`)

**Статус:** выполняется. Ветка `arena/01a0fb7d-hr-manager`, PR → `main`.
**Источник визуала:** `design/mockups/bento-arctic.html` (акцент `#0b6bcb`).
**Оценка пригодности:** `design-review/BENTO_SHOWCASE_REVIEW.md`.
**Промпт внедрения:** `prompts/BENTO_ARCTIC_IMPLEMENTATION_PROMPT.md`.
**Требования заказчика:** `design/mockups/IMPLEMENTATION_BACKLOG.md`.

> Предыдущая версия этого файла описывала направления №5 Bold Editorial,
> Clay Tactile и Blueprint CAD. Заказчик выбрал Bento («этот мокап лучший из
> 12»), поэтому план переписан под победителя. Старые направления остаются в
> истории git.

---

## 0. Что уже сделано

| Шаг | Коммит | Содержание |
| --- | --- | --- |
| 1 | `510699d` | Палитра «Лазурь» перенесена в `frontend/src/design-system/tokens.css` |
| 2 | `ef7b042` | Новый файл `design-system/components/bentoSurface.css` (mesh, стекло, spring, `--cat-*`) |
| 3 | `56051c1` | Материал Bento в компонентах design-system |
| 4 | `1c6213f` | Шелл и экраны; 29 несуществующих токенов заменены на актуальные |
| 5 | `b335f29` | Требование заказчика: фильтр «Должность» (бэкенд + список + воронка) |
| 6 | `efcec0b` | Требование заказчика: экран «Моя очередь» |
| 7 | `e0a9e2e` | Фильтр «Должность»: вместо SQL `lower()` — нормализованная колонка + миграция 0021 (см. §8) |
| 8 | `9ec2f70` | Экраны из §8.8 (календарь, документы, библиотека) и sticky-сайдбар (§6.2) |

---

## 1. Палитра «Лазурь» (фактические значения после переноса)

### Сырые шкалы

| Токен | Значение |
| --- | --- |
| `--palette-slate-25…950` | `#fbfdff #f2f7fd #e5eef9 #d8e5f5 #c5d9ef #9fbddc #6f95bb #4e6b85 #3a5570 #24425c #14293c #0b1f33 #061423` |
| `--palette-accent-50/100/200/300/400/500/600/700/900` | `#e8f2fd #cadffa #bae6fd #7dd3fc #38bdf8 #0b6bcb #0369a1 #075985 #08365c` |

`--palette-indigo-*`, `teal`, `amber`, `red`, `violet`, `cyan`, `rose` **не
тронуты**: они обслуживают статусы. Акцент построен только из
`--palette-accent-*` — иначе будущий компонент с `var(--palette-indigo-500)`
получил бы индиго вместо акцента темы (P1 из ревью, §2.4).

### Семантика

| Токен | light | dark |
| --- | --- | --- |
| `--surface-canvas` | `#f2f7fd` | `#061423` |
| `--surface-app` | `rgba(255,255,255,.78)` | `rgba(255,255,255,.06)` |
| `--surface-raised` | `rgba(255,255,255,.86)` | `rgba(255,255,255,.09)` |
| `--surface-sunken` | `rgba(11,31,51,.035)` | `rgba(255,255,255,.03)` |
| `--surface-glass` | `rgba(255,255,255,.72)` | `rgba(255,255,255,.055)` |
| `--surface-sidebar` | `var(--surface-glass)` | `rgba(255,255,255,.05)` |
| `--text-primary` | `#0b1f33` | `#e9f2fb` |
| `--text-secondary` | `#3a5570` | `#a6c2dc` |
| `--text-tertiary` | `#4e6b85` | `#9dbfda` |
| `--text-link` | `#0369a1` | `#7dd3fc` |
| `--text-on-accent` | `#ffffff` | `#06263a` |
| `--text-sidebar` | `var(--text-secondary)` | `var(--text-secondary)` |
| `--border-subtle/-default/-strong` | `rgba(11,31,51,.09/.14/.24)` | `rgba(255,255,255,.09/.14/.24)` |
| `--accent-default/-hover/-pressed` | `#0b6bcb / #0369a1 / #075985` | `#38bdf8 / #7dd3fc / #bae6fd` |
| `--accent-gradient` | `linear-gradient(135deg,#075985,#0b6bcb 55%,#0284c7)` | `linear-gradient(135deg,#7dd3fc,#38bdf8 55%,#22d3ee)` |
| `--accent-gradient-soft` | `linear-gradient(135deg,#075985,#0b6bcb)` | `linear-gradient(135deg,#38bdf8,#0ea5e9)` |
| `--shadow-1…4` | тонированные `rgba(8,45,80,…)` | `rgba(0,0,0,…)` |
| `--easing-spring` | `cubic-bezier(.34,1.56,.64,1)` | без изменений |

### Новые токены: разделение

**В `tokens.css`** (семантика, нужна компонентам): `--surface-glass`,
`--accent-gradient`, `--accent-gradient-soft`, `--glass-blur`,
`--glass-saturate`, `--easing-spring`, `--palette-accent-*`.

**В `bentoSurface.css`** (материал направления): `--blob-1…3`,
`--mesh-opacity`, `--cat-*×6 тонов ×2`, `--card-radius/-padding/-shadow/
-hover-shadow/-hover-transform`, `--btn-radius/-shadow/-primary-shadow`,
`--chip-radius`, `--search-radius`, `--topbar-bg`, `--drawer-bg`, `--toast-bg`,
`--table-head-bg`, `--skeleton-bg`, `--spark-color`, `--nav-*`,
`--brand-mark-bg/-fg`, `--kpi-cols`, `--kpi-value-size`, `--dash-gap`.

**Не перенесены сознательно** (в проде нет соответствующих элементов, иначе
это были бы мёртвые переменные): `--avatar-1…6` (цвета аватара задаёт
`Avatar.tsx` из статусных токенов), `--select-arrow` (нативный `<select>`),
`--score-fill`, `--seg-on-*` (сегментированного контрола в проде нет),
`--palette-radius`, `--palette-bg`, `--d`.

**Имя подложки:** план №1–2–4 называл её `--surface-glass`, мокап использует
`--card-bg`. Выбрано `--surface-glass` — оно продолжает нейминг
`--surface-*`. Соответствие: `--card-bg` (мокап) = `--surface-glass` (прод).

---

## 2. Имена классов: решение по ревью §2.1

Мокап остаётся источником визуала и не переименовывается. Правки шли в
**боевой** CSS под те имена, которые реально рендерит React:

| Прод (className) | Что сделано |
| --- | --- |
| `.field`, `.field-label`, `.field-hint`, `.field-error` | переписан `field.css` |
| `.text-input` (+ `.is-invalid`), `.select-input` | переписан `field.css`, состояние ошибки покрыто |
| `.tab-item` + `.is-active`, `.tab-count` | переписан `tabs.css` |
| `.status-chip status-chip-{tone} status-chip-{size}` | переписан `statusChip.css` (радиус `--chip-radius`) |
| `.avatar avatar-{sm,md,lg}` | размеры сохранены; `.avatar-sm` = 24px не сломан |
| `.btn btn-{variant} btn-{size}`, `.btn-spinner`, `.btn-label` | переписан `button.css`; `.btn-label` — обычный span, отдельного правила не требует |
| `.icon-btn icon-btn-{size} icon-btn-{variant}` + `.is-active` | переписан `button.css` |
| `.kpi-strip`, `.kpi-card`, `.kpi-card-warning` | рерайт сетки в `analytics.css` (§6.1 промпта) |
| `.tabs` | оставлен как есть ( underline-паттерн) |
| `.toast-title` | **не существует в проде** — в `Toast.tsx` рендерится `.toast-message`; правило не добавлено |
| `.avatar-md` | задаётся `avatar.css` (32px) |

---

## 3. Конфликты (ревью §2.2) — решение по каждому

| Класс | Решение |
| --- | --- |
| `.table-wrap` | правило не затёрто: фон, рамка и скругление сохранены, добавлен материал карточки (`--card-radius`, `--card-shadow`) |
| `.avatar` | базовые размеры не заданы; `.avatar-sm/md/lg` работают как раньше, добавлена только мягкая тень |
| `.field` | `gap: var(--space-1)` (4px) — не изменён |
| `.kpi-label` | размер `sm` → `xs`, цвет `secondary` → `tertiary` (осознанно: в Bento подпись — слабый элемент под крупным числом) |
| `.kpi-value` | `--text-size-xl` → `var(--kpi-value-size)` = 30px |
| `.muted` | `#8a94a8` → `var(--text-tertiary)` (серый не в тон палитре) |
| `.sidebar` | стала стеклянной; `gap: var(--space-5)` сохранён, `height: 100vh` и `position: sticky` **не переносились** (в проде своя компоновка шелла) |
| `.toast` | стекло + `--toast-bg`, радиус `lg`; дочерних `.toast-ico` в проде нет |
| `.btn` | рамка `1px solid transparent` сохранена, добавлены радиус и мягкая тень; высоты — из `--control-height-*` |
| `.btn-primary` | градиент `--accent-gradient-soft` (оба конца ≥ 5.2:1 с белым), при hover/active — плоский акцент |

---

## 4. Стекло (ревью §2.3)

* `backdrop-filter` — только на overlay-панелях: `.sidebar`, `.topbar`,
  `.drawer-panel`, `.modal-panel`, `.toast`. **На `.btn`/`.icon-btn` нет.**
* `@media (prefers-reduced-transparency: reduce)` — стекло выключено, панели
  становятся плотными, mesh скрыт (в мокапе этого не было).
* `@media (prefers-contrast: more)` — поверхности становятся
  непрозрачными, границы усилены, mesh приглушён.
* `@media (prefers-reduced-motion: reduce)` — глобальное правило из
  `global.css` сохранено; анимации KPI и карточек выключены дополнительно.
* mesh рисуется на `.workspace::before` **без `filter: blur()`**: три
  радиальных градиента вместо трёх размытых пятен — визуально эквивалентно,
  но без полноэкранного compositing-слоя.

---

## 5. Новые классы (появились в продакшене)

| Класс | Файл | Зачем |
| --- | --- | --- |
| `.filter-chips`, `.filter-chip`, `.filter-chip-label`, `.filter-chip-remove` | `features/candidates/candidates.css` | чипы активных фильтров (требование заказчика §7.1) |
| `.queue-*` (18 классов) | `features/queue/queue.css` | экран «Моя очередь» (§7.2) |

Новых классов в design-system нет: mesh и анимации подключены к
существующим `.workspace` и `.kpi-card`/`.queue-kpi`.

---

## 6. Проверки (§9 промпта)

| Проверка | До | После |
| --- | --- | --- |
| `npx vitest run` | 31 файл / 336 тестов, 0 падений | **32 файла / 340 тестов, 0 падений** (+4 новых) |
| `npx tsc -b` | чисто | чисто |
| `npx eslint .` | чисто | чисто |
| `npm run build` | CSS 85 086 b (gzip 14.81), JS 474 562 b (gzip 131.72) | CSS **99 555 b** (gzip 16.77), JS **482 629 b** (gzip 133.87) |
| Контраст AA (18 пар × 2 темы, учёт композитинга и mesh) | — | **0 проблем**; худший случай light 4.91:1, dark 4.53:1 |
| `pytest` (весь бэкенд) | 1102 теста, 0 падений | **1102 passed, 134 skipped** (+2 новых на фильтр «Должность») |
| `ruff` / `mypy` (бэкенд) | чисто | чисто (73 файла) |

**Размер CSS вырос на 17,0 % (gzip +13,2 %)** — выше порога 15 % из §9.
Причины: новый `bentoSurface.css` (mesh, стекло, `--cat-*`, media-запросы
доступности — ~7 КБ), `queue.css` (~4 КБ), расширенные правила компонентов
(покрытие недостающих вариантов кнопок/полей/чипов) и материал карточек на
календаре, документах и библиотеке. JS вырос на 1,7 % (экран «Моя очередь» +
фильтр).

---

## 7. §6.2 — сайдбар: решение

Мокап (`bento-arctic.html:297`) задаёт `height: 100vh` + `position: sticky`,
`:941` уводит сайдбар за экран на `<900px`. В продовой компоновке **sticky
принят**, because:

* на `<=1024px` сайдбар не уезжает за экран, а сворачивается в колонку иконок
  (`workspace.css` responsive-блок) — мобильная адаптация не ломается;
* `align-self: flex-start` обязателен: без него flex растягивает элемент по
  высоте контента и sticky не срабатывает;
* `overflow-y: auto` страхует низкие окна (меню «Настройки» прижато к низу
  через `margin-top: auto`).

Топбар был sticky и до внедрения — изменений нет.

---

## 8. Фильтр «Должность»: почему не SQL `lower()`

Первая реализация (`b335f29`) сравнивала `lower(position)` средствами SQL.
`lower()` в SQLite не трогает не-ASCII, в PostgreSQL — то же самое при локаль
C, поэтому по-кириллице фильтр не находил ничего: выбираешь «Монтажник РЭА» —
ноль строк. Тесты не ловили, потому что фикстуры были на латинице.

Итог: значения приводятся в Python (`casefold`) и хранятся в
`candidates.position_normalized` — это четвёртая такая колонка в проекте
рядом с `full_name/phone/email_normalized`, причина там же расписана в
docstring `normalize_full_name`. Миграция `0021_candidate_position_normalized`
заполняет старые строки в Python, поэтому перенос корректен при любой локали
БД. Подробности — в `IMPLEMENTATION_BACKLOG.md` §1.

---

## 9. Что осталось

1. Визуальная приёмка заказчиком: браузера в песочнице нет, рендер не
   проверен глазами. Для приёмки поднимается демо-стенд: vite на `5173`
   (`/api` проксируется на `8000`), SQLite `backend/preview.db` с 12
   карточками и 7 должностями, вход `hr` / `Hr1234567`. Смотреть: светлую и
   тёмную темы, 1280px, `prefers-contrast`/`prefers-reduced-transparency`,
   «Моя очередь», фильтр «Должность» на списке и на воронке, календарь,
   документы, библиотеку.
2. По результатам приёмки — докалибровать `--mesh-opacity` и радиусы.
3. Перенести сводку «Моя очередь» в календарь/уведомления, если заказчик
   захочет видеть те же числа там.
