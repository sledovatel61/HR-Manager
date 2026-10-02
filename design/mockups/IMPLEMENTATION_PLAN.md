# План реализации победителя (один PR)

Три мокапа лежат рядом с этим файлом — каждый **в одном HTML-файле**, без сборщиков и
зависимостей (inline CSS + inline JS + inline SVG). Открываются двойным кликом, работают
офлайн (шрифт Inter подгружается с CDN только как «украшение» — есть переключатель
«Системные шрифты», дизайн деградирует корректно).

| Файл | Направление | Характер |
| --- | --- | --- |
| `calm-precision.html` | №1 Calm Precision | светлый data-first, 1px-бордеры, один акцент |
| `warm-soft.html` | №2 Warm Soft | тёплый off-white, пастель, rounded-2xl, много воздуха |
| `bento-showcase.html` | №4 Bento Showcase | bento-сетка, frosted glass, mesh-градиент, крупные числа |

**Что уже есть в каждом мокапе:** 8 экранов (Моя очередь, Кандидаты, Воронка, Календарь,
Аналитика, Списки документов, Шаблоны и материалы, Настройки) + мастер «Новый список
документов»; карточка-кандидата drawer с 5 вкладками; командная палитра (Ctrl/Cmd+K);
скелетоны и stagger-появление; тосты; drag-and-drop в воронке; анимированные счётчики KPI
и спарклайны; переключатели **темы**, **плотности** и **системных шрифтов**; tabular-nums;
видимый focus-ring. Горячие клавиши: `Ctrl/Cmd+K` — палитра, `Ctrl/Cmd+\` — свернуть
сайдбар, `Esc` — закрыть палитру/drawer.

---

## А. Таблица токенов (semantic → новое значение)

Имена переменных зеркалят `frontend/src/design-system/tokens.css`, поэтому PR сводится к
подмене значений в `:root` / `[data-theme="dark"]` / `[data-density]`.

### №1 Calm Precision

| Токен | light | dark |
| --- | --- | --- |
| `--surface-canvas` | `#f7f8fa` | `#0c0d10` |
| `--surface-app` | `#ffffff` | `#131418` |
| `--surface-raised` | `#ffffff` | `#171a1f` |
| `--surface-sunken` | `#f2f4f7` | `#0f1014` |
| `--surface-sidebar` | `#fcfcfd` | `#0a0b0e` |
| `--surface-hover` / `--surface-pressed` | `#f1f3f6` / `#e7eaf0` | `#1b1e24` / `#21252c` |
| `--surface-selected` | `#eef0fe` | `rgba(110,121,240,.16)` |
| `--text-primary` / `--secondary` / `--tertiary` | `#101828` / `#475467` / `#667085` | `#eceef2` / `#a5adba` / `#7d8797` |
| `--text-sidebar` / `--text-sidebar-muted` | `#475467` / `#98a2b3` | `#a5adba` / `#6b7484` |
| `--border-subtle` / `--default` / `--strong` | `#edeff2` / `#e2e5ea` / `#cfd4dc` | `#1e2128` / `#262a32` / `#353b45` |
| `--accent-default` / `--hover` / `--pressed` | `#5e6ad2` / `#505bbf` / `#414ca2` | `#6e79f0` / `#8a94ff` / `#aeb5ff` |
| `--accent-subtle` / `--subtle-border` | `#eef0fe` / `#dce0fc` | `rgba(110,121,240,.14)` / `rgba(110,121,240,.32)` |
| `--text-on-accent` | `#ffffff` | `#0c0d10` (инверсия) |
| `--status-success-fg` / `-bg` | `#067647` / `#ecfdf3` | `#6ce9a6` / `rgba(6,118,71,.18)` |
| `--status-warning-fg` / `-bg` | `#b54708` / `#fffaeb` | `#fdb022` / `rgba(181,71,8,.18)` |
| `--status-danger-fg` / `-bg` | `#b42318` / `#fef3f2` | `#fda29b` / `rgba(180,35,24,.18)` |
| `--status-info-fg` / `-bg` | `#1d4ed8` / `#eff4ff` | `#7aa7ff` / `rgba(37,99,235,.16)` |
| `--status-indigo-fg` / `-bg` | `#3538cd` / `#eef4ff` | `#a3a8ff` / `rgba(53,56,205,.2)` |
| `--status-violet-fg` / `-bg` | `#6941c6` / `#f4f3ff` | `#c0aaf7` / `rgba(105,65,198,.18)` |
| `--status-teal-fg` / `-bg` | `#107569` / `#edfdf8` | `#5fd0bd` / `rgba(16,117,105,.18)` |
| `--status-neutral-fg` / `-bg` | `#475467` / `#f2f4f7` | `#a5adba` / `rgba(255,255,255,.06)` |
| `--radius-xs…xl` | `3/4/6/8/10px` | без изменений |
| `--shadow-1…4` | нейтральные `rgba(16,24,40,.04…16)` | `rgba(0,0,0,.4…0.6)` |

### №2 Warm Soft

| Токен | light | dark |
| --- | --- | --- |
| `--surface-canvas` | `#faf7f2` | `#1c1917` |
| `--surface-app` | `#ffffff` | `#242120` |
| `--surface-raised` | `#ffffff` | `#2b2725` |
| `--surface-sunken` | `#f7f2ea` | `#211e1d` |
| `--surface-sidebar` | `#f5efe6` | `#171514` |
| `--text-primary` / `--secondary` / `--tertiary` | `#2e2a26` / `#6b625a` / `#756c62` | `#f7f1ea` / `#c3b8ac` / `#9d9287` |
| `--border-subtle` / `--default` / `--strong` | `#f1eae0` / `#e9e1d5` / `#d8ccb9` | `#302b29` / `#3a3432` / `#4b4340` |
| `--accent-default` / `--hover` / `--pressed` | `#b8502f` / `#9c4227` / `#7c3517` | `#e1875f` / `#eda084` / `#f4b79f` |
| `--accent-subtle` / `--subtle-border` | `#fdf1ea` / `#f6ddce` | `rgba(225,135,95,.15)` / `rgba(225,135,95,.34)` |
| `--text-on-accent` | `#ffffff` | `#2a1206` (инверсия) |
| `--status-success-fg` / `-bg` | `#3f6b4f` / `#eaf5ee` | `#8ecfa4` / `rgba(63,107,79,.22)` |
| `--status-warning-fg` / `-bg` | `#8f6116` / `#fbf1de` | `#e8bd6a` / `rgba(143,97,22,.22)` |
| `--status-danger-fg` / `-bg` | `#a33a34` / `#fbeeec` | `#f0a29c` / `rgba(163,58,52,.22)` |
| `--status-info-fg` / `-bg` | `#2f6b8f` / `#e7f2f8` | `#8fc7e8` / `rgba(47,107,143,.2)` |
| `--status-indigo-fg` / `-bg` | `#46508f` / `#ecedf8` | `#adb6ea` / `rgba(70,80,143,.24)` |
| `--radius-xs…xl` | `6/8/12/16/22px` | без изменений |
| `--shadow-*` | тёплые `rgba(80,50,20,.05…16)` | `rgba(0,0,0,.4…0.6)` |

Дополнительно: `--sidebar-width: 264px`, `--topbar-height: 64px`, `--text-size-*`
на 1px больше базовых, `--line-height-normal: 1.55`.

### №4 Bento Showcase

| Токен | light | dark |
| --- | --- | --- |
| `--surface-canvas` | `#f4f3fb` | `#0a0713` |
| `--surface-app` | `rgba(255,255,255,.78)` | `rgba(255,255,255,.06)` |
| `--surface-raised` | `rgba(255,255,255,.86)` | `rgba(255,255,255,.09)` |
| `--surface-sunken` | `rgba(255,255,255,.52)` | `rgba(255,255,255,.04)` |
| `--surface-sidebar` | `rgba(255,255,255,.62)` | `rgba(255,255,255,.04)` |
| `--text-primary` / `--secondary` / `--tertiary` | `#14121f` / `#554f6b` / `#6c6585` | `#f6f4ff` / `#c3bce0` / `#9a92bb` |
| `--border-subtle` / `--default` / `--strong` | `rgba(20,18,31,.07/.11/.18)` | `rgba(255,255,255,.09/.14/.24)` |
| `--accent-default` / `--hover` / `--pressed` | `#6d5ef8` / `#5a48e8` / `#4634c4` | `#6d5ef8` / `#5f4ff0` / `#4a3fd0` |
| `--accent-gradient` | `linear-gradient(135deg,#5b46e6,#9333ea 55%,#db2777)` | `linear-gradient(135deg,#8b7aff,#c084fc 55%,#f472b6)` |
| `--accent-gradient-soft` | `linear-gradient(135deg,#6d5ef8,#9333ea)` | то же |
| `--radius-xs…xl` | `8/10/14/20/26px` | без изменений |
| `--shadow-*` | фиолетовые `rgba(49,22,108,…)` | `rgba(0,0,0,…)` |

Плюс три служебных токена, которых нет в проде: `--surface-glass` (подложка карточек),
`--accent-gradient` (только для KPI-чисел/заголовков), `--easing-spring: cubic-bezier(.34,1.36,.64,1)`.

### Плотность (общая для всех направлений)

| Токен | comfortable | compact |
| --- | --- | --- |
| `--row-height` | 48px (Warm 54, Bento 52) | 36–40px |
| `--control-height-md` / `-sm` | 36/30px | 32/26px |
| `--table-cell-padding-y` | 12px | 7px |

---

## Б. Файлы под restyle

Минимальный набор (всё остальное подхватит токены):

1. `frontend/src/design-system/tokens.css` — **новые значения** semantic-токенов (light/dark/density).
2. `frontend/src/design-system/global.css` — базовый фон, типографика, focus-ring.
3. `frontend/src/app-shell/workspace.css` — сайдбар (ширина, активный пункт, бейджи), топбар.
4. `frontend/src/design-system/components/` — `button.css`, `field.css`, `statusChip.css`,
   `avatar.css`, `drawer.css`, `modal.css`, `tabs.css`, `toast.css`, `stateViews.css`
   (скелетоны).
5. `frontend/src/features/candidates/` — `candidates.css` (таблица), `kanban.css` (воронка +
   drag-affordance), `drawer.css` (карточка кандидата).
6. `frontend/src/features/analytics/analytics.css` — KPI-плитки, графики, воронка конверсий.
7. `frontend/src/features/calendar/calendar.css` — сетка недели, события.
8. `frontend/src/features/documents/documents.css`, `library/library.css` — списки, плитки,
   мастер (wizard).
9. `frontend/src/styles.css` — точечные правки общих отступов.

Для Bento дополнительно: один новый файл `design-system/components/glassPanel.css` (backdrop-filter,
mesh-подложка) — единственный новый CSS в PR.

---

## В. Риски и что НЕ меняем

**Риски**

- *Glassmorphism* (Bento): `backdrop-filter` дорогой на слабых машинах и ломается при
  вложенных скроллах → ограничить стекло карточками дашборда и overlay, таблицы/формы
  оставить плотными; обязательный fallback `background: var(--surface-app)` при
  `prefers-reduced-transparency`.
- Градиентный текст (Bento, KPI и H1) — проверять контраст на концах градиента; для
  значений ≤20px градиент не применять.
- Тёплая палитра (Warm) снижает контраст «серого» текста → третичный текст затемнён до
  `#756c62` (5.15:1), менять цвет «вручную» в компонентах нельзя.
- Сайдбар 264px (Warm) и 256px (Bento) съедают ширину таблиц → проверить 1280px.
- Инверсия `--text-on-accent` в тёмной теме (Calm/Warm): переиспользуется в чипах, тумблерах
  и бейджах — пройтись по всем местам использования.

**Не меняем (явно вне зоны PR)**

- Функционал, роутинг, состав полей и API — ноль изменений.
- Системный стек шрифтов в проде: новые веб-шрифты не подключаем (Inter в мокапе — только превью, есть
  переключатель).
- a11y: контраст ≥ AA проверен по парам «текст/фон», «статус-чип», «акцент/текст на акценте»
  (14 проверок на тему, все зелёные); focus-ring, aria-атрибуты и семантика остаются.
- Действия с данными в мокапе — заглушки; в проде ничего не отправляется.

## Г. Оценка: влезает ли в один PR

Да. Один PR = **подмена значений токенов + restyle ~15 CSS-файлов** в рамках существующей
системы. Разбивка по объёму (оценка):

- токены и global — 0.5 дня;
- компоненты design-system — 1–1.5 дня;
- экраны (кандидаты, воронка, аналитика, календарь, документы/мастер) — 2–3 дня;
- «вау»-слой (командная палитра, скелетоны, тосты, анимации счётчиков) — 1–2 дня
  (палитра — новый компонент, самый большой кусок);
- ревью a11y + density + тёмная тема — 1 день.

Итого **~5–7 рабочих дней** на одного фронтендера, один PR, без новых runtime-зависимостей.
Bento Showcase добавляет ~1 день (стекло + mesh + spring-анимации) и несёт наибольший риск
«устаревания» — компенсируется читабельностью (плотные подложки под текстом).
