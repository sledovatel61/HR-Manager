# План реализации победителя (один PR)

Рядом с этим файлом — три мокапа, которых **нет у других агентов**: направление №5 из
промпта и две авторские темы, собранные после ресерча трендов 2026 и открытых китов.
Каждый мокап — **один самодостаточный HTML-файл** (inline CSS + JS + SVG, без сборщиков и
зависимостей). Открываются двойным кликом, работают офлайн; шрифт Inter подгружается с CDN
только как «украшение» — есть переключатель «Системные шрифты», дизайн деградирует корректно.

| Файл | Направление | Откуда идея | Характер |
| --- | --- | --- | --- |
| `bold-editorial.html` | **№5 Bold Editorial** | промпт §4.5 + практика нео-брутализма[5](https://retroui.dev/) | 2px-бордеры, жёсткие тени-смещения, плоские насыщенные цвета, крупные заголовки |
| `clay-tactile.html` | **Clay Tactile** (авторская) | тренд 2026 «3D tactile & material» / claymorphism[2](https://pixso.net/articles/7-ui-ux-design-trends/)[1](https://uidesignprompts.com/prompts/claymorphism)[4](https://www.typeui.sh/design-skills/claymorphism) | «вылепленные» поверхности, экструзия, толстая цветная «губа» у кнопок, продавливание при нажатии |
| `blueprint-cad.html` | **Blueprint CAD** (авторская) | инженерные эстетики: Blueprint/CAD, Braun–Dieter Rams, btop Meters[2](https://github.com/LeviTrillfonix/retro-design-system) + «тихий хром и таблицы вместо стен графиков»[2](https://adminlte.io/blog/saas-dashboard-design-examples/) | миллиметровка на канвасе, хайрлайн-панели, моно-лейблы, угловые засечки рамки, штамп листа |

> Первая попытка (№1 Calm Precision, №2 Warm Soft, №4 Bento Showcase) убрана в
> `design/_archive-agent4-1-2-4/` — те направления уже сделали другие агенты.
> План по ним: `design/_archive-agent4-1-2-4/IMPLEMENTATION_PLAN-1-2-4.md`.

## Почему именно такие две авторские темы

- **Clay Tactile** — единственный крупный тренд 2026, которого нет в шестёрке направлений
  промпта: «3D tactile & material design» с clay-экструзией — мягкая, «человечная»
  материальность[2](https://pixso.net/articles/7-ui-ux-design-trends/). Ключевая механика —
  **нулевой блюр и сплощённая тень-смещение** (`0 6px 0 0`) плюс продавливание при нажатии[1](https://uidesignprompts.com/prompts/claymorphism).
  Это не «ещё одно тёплое мягкое» (№2): Warm Soft — плоское и воздушное, Clay — объёмное,
  насыщенное, с крупными пилюльными контролами и физикой нажатия.
- **Blueprint CAD** — ход, которого нет ни у одного из шести направлений: интерфейс как
  **инженерный чертёж**. Для HR-Manager это не случайная эстетика, а попадание в контекст
  (завод: ЧПУ, РЭА, цеха, график выхода). Миллиметровка, моно-лейблы, хайрлайны, засечки
  рамки и штамп листа дают максимальную запоминаемость при почти нулевой декоративности —
  то есть не конфликтуют с плотными таблицами[2](https://adminlte.io/blog/saas-dashboard-design-examples/).

От Liquid Glass (главный тренд 2026[3](https://www.uxstudioteam.com/ux-blog/ui-trends-2019)) и
Bento-grid[4](https://theplusaddons.com/blog/web-design-trends-2026/) отказался сознательно:
стекло уже занято направлением №4, два стеклянных мокапа в одной подборке выглядят дублями.

## Что уже есть в каждом мокапе

8 экранов (Моя очередь, Кандидаты, Воронка, Календарь, Аналитика, Списки документов, Шаблоны
и материалы, Настройки) + мастер «Новый список документов»; карточка-кандидата drawer с 5
вкладками; командная палитра (`Ctrl/Cmd+K`); скелетоны со stagger-появлением; тосты;
drag-and-drop в воронке; анимированные счётчики KPI и спарклайны; переключатели **темы**,
**плотности** и **системных шрифтов**; tabular-nums; видимый focus-ring.
Горячие клавиши: `Ctrl/Cmd+K` — палитра, `Ctrl/Cmd+\` — свернуть сайдбар, `Esc` — закрыть.

## А. Таблица токенов (semantic → новое значение)

Имена зеркалят `frontend/src/design-system/tokens.css`, поэтому PR = подмена значений в
`:root` / `[data-theme="dark"]` / `[data-density]`.

### №5 Bold Editorial

| Токен | light | dark |
| --- | --- | --- |
| `--surface-canvas` | `#f4f1e8` | `#101010` |
| `--surface-app` / `--surface-raised` | `#ffffff` | `#1a1a1a` |
| `--surface-sunken` | `#efe9db` | `#141414` |
| `--surface-sidebar` | `#ffffff` | `#161616` |
| `--surface-selected` | `#ffe36b` | `#3d2f00` |
| `--text-primary` / `--secondary` / `--tertiary` | `#111111` / `#3f3f46` / `#52525b` | `#fafafa` / `#d4d4d8` / `#a1a1aa` |
| `--border-subtle` / `--default` / `--strong` | `rgba(17,17,17,.22)` / `#111111` / `#111111` | `rgba(255,255,255,.26)` / `#ffffff` / `#ffffff` |
| `--accent-default` / `--hover` / `--pressed` | `#d92d20` / `#b91c1c` / `#8f1414` | `#ffd400` / `#ffe14d` / `#fff08a` |
| `--accent-subtle` | `#fde8e6` | `#3d2f00` |
| `--text-on-accent` | `#ffffff` | `#111111` (инверсия) |
| `--status-success-fg` / `-bg` | `#065f46` / `#86e3b8` | `#86e3b8` / `#07503c` |
| `--status-warning-fg` / `-bg` | `#6b3d05` / `#ffd166` | `#ffd166` / `#5e3a05` |
| `--status-danger-fg` / `-bg` | `#7a1414` / `#ff9d94` | `#ff9d94` / `#6d1414` |
| `--status-info-fg` / `-bg` | `#12386e` / `#a9c9f5` | `#a9c9f5` / `#123a72` |
| `--status-indigo-fg` / `-bg` | `#241f8f` / `#b3b0f7` | `#b3b0f7` / `#211d80` |
| `--status-violet-fg` / `-bg` | `#40247a` / `#cdb4f5` | `#cdb4f5` / `#3b2274` |
| `--status-teal-fg` / `-bg` | `#0a4f45` / `#86ded0` | `#86ded0` / `#08453d` |
| `--status-neutral-fg` / `-bg` | `#2b2b31` / `#e2dccd` | `#d4d4d8` / `#2b2b2b` |
| `--radius-xs…xl` | `0/2/3/4/6px` | без изменений |
| `--shadow-1…4` | `2/3/5/8px 2/3/5/8px 0 #111111` | то же, но `#ffffff` |

Дополнительно: `--font-weight-bold: 800`, `--text-size-3xl: 42px`, маркерная подложка под H1
(`linear-gradient(180deg, transparent 62%, #ffd400 62%)`), толщина рамок 2–3px во всех
компонентах.

### Clay Tactile

| Токен | light | dark |
| --- | --- | --- |
| `--surface-canvas` | `#f2f5fa` | `#141a24` |
| `--surface-app` / `--raised` | `#ffffff` | `#1e2634` |
| `--surface-sunken` | `#e9eef7` | `#18202c` |
| `--surface-sidebar` | `#eaeff8` | `#161d28` |
| `--text-primary` / `--secondary` / `--tertiary` | `#1f2937` / `#475569` / `#5b6b80` | `#eaf0f8` / `#a9b8cc` / `#8a9ab0` |
| `--border-subtle` / `--default` / `--strong` | `rgba(31,41,55,.08/.13/.22)` | `rgba(255,255,255,.07/.12/.2)` |
| `--accent-default` / `--hover` / `--pressed` | `#6c5ce7` / `#5b4bd6` / `#4c3fc0` | `#6d5ce7` / `#5b4bd6` / `#4c3fc0` |
| `--text-on-accent` | `#ffffff` | `#ffffff` |
| `--status-success-fg` / `-bg` | `#166534` / `#dcfce7` | `#86e3b0` / `rgba(22,101,52,.3)` |
| `--status-warning-fg` / `-bg` | `#92400e` / `#fef3c7` | `#fcd177` / `rgba(146,64,14,.3)` |
| `--status-danger-fg` / `-bg` | `#991b1b` / `#fee2e2` | `#ffa9a4` / `rgba(153,27,27,.32)` |
| `--status-info-fg` / `-bg` | `#1e40af` / `#dbeafe` | `#a8c4ff` / `rgba(30,64,175,.28)` |
| `--radius-xs…xl` | `10/12/16/22/28px` | без изменений |
| `--shadow-*` | мягкая тень + `inset 0 2px 0 rgba(255,255,255,.85)` | тень + `inset 0 2px 0 rgba(255,255,255,.07)` |

Новые служебные токены (только для этой темы):
`--clay-lip` (цвет «губы» кнопки: `#dbe2ee` / `#131a25`),
`--btn-shadow: 0 4px 0 var(--clay-lip), 0 10px 18px -10px rgba(...), inset 0 2px 0 rgba(255,255,255,.8)`;
нажатие — `translateY(4px)` и схлопывание губы.

### Blueprint CAD

| Токен | light (ватман) | dark (синька) |
| --- | --- | --- |
| `--surface-canvas` | `#f4f9ff` | `#08243d` |
| `--surface-app` / `--raised` | `#ffffff` | `#0e3055` |
| `--surface-sunken` | `#eaf2fb` | `#0a2946` |
| `--surface-sidebar` | `#eaf2fb` | `#0a2946` |
| `--text-primary` / `--secondary` / `--tertiary` | `#0b2a4a` / `#33526e` / `#4e6b85` | `#e8f1fa` / `#a9c7e4` / `#7fa5c7` |
| `--border-subtle` / `--default` / `--strong` | `#cfe2f5` / `#9cc3e8` / `#2e6fa8` | `rgba(127,179,255,.16)` / `#2e6fa8` / `#7fb3ff` |
| `--accent-default` / `--hover` / `--pressed` | `#1d4ed8` / `#1740b8` / `#11308c` | `#7fb3ff` / `#9cc7ff` / `#bcd9ff` |
| `--text-on-accent` | `#ffffff` | `#06203a` (инверсия) |
| `--status-success-fg` / `-bg` | `#10603f` / `#d7f0e4` | `#7fdcae` / `rgba(16,96,63,.3)` |
| `--status-warning-fg` / `-bg` | `#8a4b00` / `#fdebd2` | `#ffc178` / `rgba(138,75,0,.32)` |
| `--status-danger-fg` / `-bg` | `#9b1c1c` / `#fbdddd` | `#ff9d97` / `rgba(155,28,28,.32)` |
| `--status-info-fg` / `-bg` | `#14459e` / `#dce7fb` | `#a8c7ff` / `rgba(30,64,175,.28)` |
| `--radius-xs…xl` | `0/2/2/3/4px` | без изменений |
| `--shadow-*` | почти нет: `0 1px 0 rgba(11,42,74,.04…)` | `rgba(0,0,0,.3…8)` |

Новые служебные токены: `--bp-grid` / `--bp-grid-strong` (сетка 24/120px),
`--bp-mark` (оранжевая «красная правка»: `#c2410c` / `#ffb454`).
Моно-лейблы (`--font-mono`, 10px, uppercase, `.08em`) применяются к `.eyebrow`, `.kpi-label`,
`.nav-title`, `.card-sub`, `.chip`, `.tbl th`, `.seg button`, `.tab`, `.feed-time`,
`.axis-*`, `.bar-name`, `.counter`, `.toolbar-note`.

### Плотность (общая)

| Токен | comfortable | compact |
| --- | --- | --- |
| `--row-height` | 46–54px | 34–42px |
| `--control-height-md` / `-sm` | 36–42 / 30–36px | 32–36 / 26–30px |
| `--table-cell-padding-y` | 11–14px | 7–9px |

## Б. Файлы под restyle

1. `frontend/src/design-system/tokens.css` — новые значения semantic-токенов (light/dark/density).
2. `frontend/src/design-system/global.css` — фон, типографика, focus-ring.
3. `frontend/src/app-shell/workspace.css` — сайдбар и топбар (в Blueprint — «штамп» топбара).
4. `frontend/src/design-system/components/` — `button.css` (+ «губа»/жёсткая тень по направлению),
   `field.css`, `statusChip.css`, `avatar.css`, `drawer.css`, `modal.css`, `tabs.css`,
   `toast.css`, `stateViews.css`.
5. `frontend/src/features/candidates/` — `candidates.css`, `kanban.css`, `drawer.css`.
6. `frontend/src/features/analytics/analytics.css` — KPI-плитки, графики, воронка.
7. `frontend/src/features/calendar/calendar.css`, `features/documents/documents.css`,
   `features/library/library.css`.
8. `frontend/src/styles.css` — точечные правки отступов.

Новые CSS-файлы: один на направление — `design-system/components/claySurface.css`
(экструзия/губа/пресс) или `blueprintFrame.css` (сетка, засечки, штамп). Больше одного
файла на направление не требуется.

## В. Риски и что НЕ меняем

- **Bold Editorial.** Полноэкранный нео-брутализм «остывает» и конфликтует с плотными
  данными[4](https://theplusaddons.com/blog/web-design-trends-2026/): рамки 2px и жёсткие тени
  не применять к ячейкам таблиц (только к панелям и CTA), в таблицах — хайрлайны. Тёмная тема
  с белыми рамками требует ревью на OLED-экранах (гало).
- **Clay Tactile.** Claymorphism плохо переносит плотные формы и таблицы[4](https://theplusaddons.com/blog/web-design-trends-2026/):
  экструзию даём карточками дашборда, кнопкам и чипам; поля ввода — всегда «вдавленные»
  (inset), чтобы не терять границы. Тени дорогие по перерисовке — не более двух слоёв на элемент.
- **Blueprint CAD.** Моно-лейблы 10px допустимы только для подписей, не для значений; штамп
  листа и засечки — декоративные `::before/::after`, не должны попадать в DOM-текст (иначе
  скринридеры зачитают мусор). Сетка-миллиметровка отключается в `prefers-reduced-motion`/
  `prefers-contrast: more`.
- **Общее.** Инверсия `--text-on-accent` в тёмных темах (Bold/Blueprint) переиспользуется в
  чипах, тумблерах и бейджах — пройтись по всем местам. Сайдбар 264–272px (Bold/Clay) съедает
  ширину таблиц — проверить 1280px.

**Не меняем:** функционал, роутинг, состав полей, API; веб-шрифты (новые не подключаем, только
системный стек); a11y-семантика и aria-атрибуты; контраст ≥ AA (проверено: 14 пар «текст/фон»,
«чип», «акцент/текст на акценте» на каждую тему × 2 темы × 3 файла — всё зелёное); данные в
мокапах — статичные заглушки.

## Г. Оценка: влезает ли в один PR

Да: подмена значений токенов + restyle ~15 CSS-файлов + один новый файл под «материал»
направления. Оценка на одного фронтендера:

- токены и global — 0.5 дня;
- компоненты design-system — 1–1.5 дня;
- экраны (кандидаты, воронка, аналитика, календарь, документы/мастер) — 2–3 дня;
- «вау»-слой (командная палитра, скелетоны, тосты, счётчики) — 1–2 дня;
- материал направления (экструзия/пресс у Clay, сетка и засечки у Blueprint, жёсткие тени у
  Bold) — +0.5–1 день;
- ревью a11y, плотность, тёмная тема — 1 день.

Итого **~5–8 рабочих дней**, один PR, без новых runtime-зависимостей. Дороже всех —
палитра команд (новый компонент) и Clay (точная настройка теней под светлую/тёмную темы).
