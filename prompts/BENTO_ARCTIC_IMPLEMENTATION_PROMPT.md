# Промпт: внедрение мокаута Bento Showcase — палитра «Лазурь» (`bento-arctic.html`)

## 0. Задача

Создать **отдельную ветку и отдельный PR** и в нём привести мокап
`design/mockups/bento-arctic.html` в соответствие с боевым кодом и начать
непосредственное внедрение. Это первый реальный PR редизайна, поэтому порядок
важнее скорости.

**Что делать в первую очередь, до любых правок CSS:**

1. Создать ветку от актуального `origin/main` с именем
   `arena/<id>-hr-manager` (как в остальных задачах).
2. Сразу открыть PR из этой ветки в `main` с заголовком
   `feat(frontend): внедрение Bento Showcase (палитра «Лазурь»)` и описанием по
   шаблону §10 этого промпта. Дальше работать **в этом PR**, пуша в ту же ветку.
3. Прогнать `npx vitest run` на `main` **до** изменений и зафиксировать результат
   в описании PR как базовую линию. Без этого отличить регрессию редизайна от
   уже сломанного будет невозможно.

Ветку мокапов (`arena/01a0fb7d-hr-manager`) **не трогать** — мокап остаётся
источником истины по визуау.

---

## 1. Что выбрано и что уже проверено (не перепроверять, использовать как факты)

**Победивший мокап:** `design/mockups/bento-arctic.html` — палитра «Лазурь / Arctic
blue», акцент `#0b6bcb`.

Факты, установленные ревьюером исполнением — опираться на них, не пересчитывать:

| Факт | Значение |
| --- | --- |
| Размер / строки | 169 271 байт, 2182 строки, 1 `<style>` + 1 `<script>` |
| Контраст AA | **зелёный.** Худший случай (`--text-primary` на карточке поверх самого светлого mesh-пятна): light **15.00:1**, dark **7.56:1** |
| JS и разметка `<body>` | md5 идентичны всем четырём палитрам — правь только CSS |
| `esc()` | стоит на всех 25 вставках данных — не потерять при рерайте |
| Семантические токены | зеркалятся полностью: `surface` 13/13, `text` 17/17, `border` 5/5, `accent` 5/5, `status` 24/24, `radius`/`shadow`/`text-size` 18/18, `duration`/`easing`/`z` 15/16 |
| Токен-гигиена мокаута | 5.6 % хардкода в CSS (в боевом коде 15.4 %) — держать этот уровень |

Палитра arctic, проверенные значения (светлая тема):

```
--surface-canvas: #f2f7fd;   --surface-app: rgba(255,255,255,.78);
--surface-raised: rgba(255,255,255,.86);   --card-bg: rgba(255,255,255,.72);
--text-primary: #0b1f33;  --text-secondary: #3a5570;  --text-tertiary: #4e6b85;
--accent-default: #0b6bcb;  --accent-hover: #0369a1;  --accent-pressed: #075985;
--blob-1/2/3: #7dd3fc / #a5b4fc / #67e8f9;
--cat-success-1: #047857;  --cat-info-1: #0e7490;
```

---

## 2. Главная проблема: 96 % CSS мокаута живёт в другом пространстве имён

Сверка селекторов в позиции селектора (не substring-матчинг):

| | количество |
| --- | --- |
| селекторов в `bento-arctic.html` | **396** |
| совпадают с `frontend/src/**/*.css` | **16 (4 %)** |
| только в мокаупе | **380** |

Совпадают только:
`.avatar .btn .btn-ghost .btn-primary .drawer-body .drawer-head .field .icon-btn
.kpi-label .kpi-value .muted .sidebar .sr-only .table-wrap .toast .topbar`

При этом **бой рендерит другие имена** — это и есть содержание задачи:

| Прод (фактический `className`, файл:строка) | Мокап |
| --- | --- |
| `Field.tsx:21` → `field-label`; `:28` → `field-hint`; `:33` → `field-error` | `.field > label`, `.help` |
| `Field.tsx:46` → `text-input` (+ `.is-invalid`); `:59` → `select-input` | `.input`, `.select` |
| `Tabs.tsx:43` → `tab-item` + `is-active` | `.tab` + `.is-on` |
| `StatusChip.tsx:25,39` → `status-chip status-chip-{tone} status-chip-{size}` | `.chip.clip-{tone}`, `.chip-radius` |
| `Avatar.tsx:39` → `avatar avatar-{sm,md,lg}` | `.avatar`, `.avatar.sm/.lg`, `.av-2…6` |
| `Button.tsx:23` → `btn btn-{variant} btn-{size}` | `.btn`, `.btn.sm`, `.btn.icon` |
| `IconButton` (`Button.tsx:55`) → `icon-btn icon-btn-{size} icon-btn-{variant}` | `.icon-btn` |
| `analytics.css:72` → `.kpi-strip`; `:78` → `.kpi-card` (+ `.kpi-card-warning`) | `.dash-kpi`, `.kcard` |
| `candidates.css:76` → `.table-wrap` | `.table-wrap` + свой `.tbl` |

**Задача:** привести мокап к боевому API имён, а не наоборот. Правь **CSS мокаута**,
чтобы он описывал те классы, которые реально рендерит React. Если для какого-то
элемента боевого аналога нет вообще (командная палитра, тосты с иконкой,
скелетоны stagger, mesh-фон) — тогда и только тогда вводится новый класс, и он
**обязательно** фиксируется в плане (§10, раздел «новые классы»).

**Варианты, которые бой рендерит, а мокап не показывает вообще** — их нельзя
потерять, нужно покрыть: `.btn-secondary`, `.btn-danger`, `.btn-md`, `.btn-sm`,
`.btn-spinner`, `.btn-label`, `.icon-btn-md/sm/ghost/secondary`, `.tabs`,
`.tab-count`, `.avatar-md`, `.kpi-card-warning`, `.field-error`,
`.text-input.is-invalid`, `.toast-title`.

---

## 3. P1 — конфликтные классы: мокап затирает боевые правила

Эти 10 классов есть в обоих, но правила разные. По каждому нужно решение в плане:
либо переименовать в мокауте, либо явно переписать в проде и отметить в PR.

| Класс | Мокап (файл:строка) | Прод (файл:строка) | Что сломается |
| --- | --- | --- | --- |
| `.table-wrap` | `bento-arctic.html:561` — только `overflow-x: auto` | `candidates.css:76` — фон, рамка, `border-radius`, `overflow-x` | **контейнер таблицы теряет фон, рамку и скругление** |
| `.avatar` | `:366` — `width/height: 32px`, `border-radius: var(--avatar-radius)`, `background: var(--avatar-1)` | `avatar.css:1-13` — `.avatar-sm 24px`, `.avatar-md 32px`, `.avatar-lg 48px`, `border-radius: var(--radius-full)` | **`.avatar-sm` перестаёт работать** — мелкие аватары станут 32px |
| `.field` | `:462` — `gap: 6px` | `field.css:2` — `gap: var(--space-1)` = 4px | зазор во всех формах +50 % |
| `.kpi-label` | `:520` — `font-size: 2xs`, `text-transform`, `letter-spacing`, `color: tertiary` | `analytics.css:95` — `font-size: sm`, `color: secondary` | подписи KPI мельчают и меняют цвет |
| `.kpi-value` | `:526` — `--kpi-value-size: 40px` | `analytics.css:89` — `--text-size-xl` | высота KPI-плитки |
| `.muted` | `:284` — `color: var(--text-tertiary)` | `styles.css:313` — `color: #8a94a8` | серая подпись меняет цвет |
| `.sidebar` | `:297` — `gap: var(--space-2)`, `height: 100vh`, `position: sticky`, `z-index: var(--z-dropdown)` | `workspace.css:8-17` — `gap: var(--space-5)`, без sticky | зазор 20px → 8px, сайдбар становится sticky |
| `.toast` | `:891` — `gap: space-3`, `radius-lg`, `background: var(--toast-bg)` | `toast.css:12-16` — `gap: space-2`, `radius-md`, `background: var(--surface-raised)` | тосты меняют форму; children `.toast-ico` в проде не рендерятся |
| `.btn` | `:406` — `height: var(--control-height-md)`, `border: 1px solid var(--border-default)`, `box-shadow: var(--btn-shadow)` | `button.css:1-16` — `min-height: 24px`, `border: 1px solid transparent` | высота и рамка всех кнопок |
| `.btn-primary` | `:982` — `background: var(--accent-gradient-soft)` | `button.css:34` — `background: var(--accent-default)` (плоский) | главная кнопка становится градиентной |

Минимум, который надо сделать обязательно: **переименовать `.table-wrap` в мокауте**
(например в `.bento-table-scroll`), иначе правка гарантированно сломает вид всех
таблиц кандидатов.

---

## 4. P1 — стекло: агент нарушил собственное ограничение

В `design/_archive-agent4-1-2-4/IMPLEMENTATION_PLAN-1-2-4.md:140` написано:

> *Glassmorphism* (Bento): `backdrop-filter` дорогой на слабых машинах и ломается при
> вложенных скроллах → ограничить стекло карточками дашборда и overlay, таблицы/формы
> оставить плотными; обязательный fallback `background: var(--surface-app)` при
> `prefers-reduced-transparency`.

Фактически (`bento-arctic.html:968-971`) стекло на 16 селекторах:

```css
.sidebar, .topbar, .card, .drawer, .palette, .toast, .kcard, .cat, .seg, .search-trigger,
.input, .select, .search-field, .btn, .icon-btn, .tbl th {
  backdrop-filter: blur(18px) saturate(160%);
  -webkit-backdrop-filter: blur(18px) saturate(160%);
}
```

Уточнение по проверке: в боевом CSS реально существуют только 6 из них —
`.sidebar`, `.topbar`, `.drawer`, `.toast`, `.btn`, `.icon-btn`. Остальные
(`.input`, `.select`, `.search-field`, `.card`, `.kcard`, `.cat`, `.seg`, `.palette`,
`.tbl th`) в проде отсутствуют, стекло на них — no-op.

**Сделать:**
1. Убрать `backdrop-filter` с `.btn` и `.icon-btn` — это самый частый элемент
   интерфейса, и он же violates «формы и таблицы оставить плотными».
2. Добавить обязательный fallback, который план называет обязательным, а мокап не
   содержит (0 вхождений):
   ```css
   @media (prefers-reduced-transparency: reduce) {
     .card, .kcard, .sidebar, .topbar, .drawer, .palette, .toast {
       backdrop-filter: none;
       -webkit-backdrop-filter: none;
       background: var(--surface-app);
     }
   }
   ```
3. Добавить `@media (prefers-contrast: more)` — в мокауте **0 вхождений**. При
   повышенной контрастности полупрозрачные `--card-bg: rgba(255,255,255,.72)` и
   тёмный `rgba(255,255,255,.055)` могут схлопнуться в нечитаемость.
4. `prefers-reduced-motion: reduce` в мокауте есть (`:929`) и он глобальный — это
   корректно глушит и `floaty`, и `rise`, и `shimmer`. Не потерять при переносе.

В боевом CSS `backdrop-filter` — **0 вхождений**, в `design-prototype/` на той же
ветке — тоже **0**. После PR в проде он должен появиться только на overlay-панелях,
не на кнопках.

---

## 5. P1 — токены: три отдельные проблемы

### 5.1 33 боевых `--palette-*` не объявлены

Боевой `tokens.css:73-101` строит **все 66** семантических токенов из
`var(--palette-*)`. Мокап объявляет `--palette-white`, `--palette-slate-25…950`,
`--palette-accent-50…700`, но **не объявляет** `--palette-{indigo,red,amber,rose,
teal,violet,cyan}-*` — 33 токена.

Сейчас `var(--palette-*)` встречается в проде 66 раз, и **все — внутри `tokens.css`**.

**Сделать:** переносить значения **внутрь `tokens.css`**, заменяя боевые объявления
семантических токенов на значения палитры arctic. Не добавлять мокапный `:root`
поверх боевого — иначе `--palette-*` останутся боевыми, и будущий компонент с
`var(--palette-indigo-500)` получит индиго вместо акцента темы.

### 5.2 66 новых токенов, а не 3

`IMPLEMENTATION_PLAN-1-2-4.md:~98` заявляет «плюс три служебных токена:
`--surface-glass`, `--accent-gradient`, `--easing-spring`». Фактически в мокауте
**66** токенов, отсутствующих в боевом `tokens.css`:

`--accent-gradient`, `--accent-gradient-soft`, `--avatar-1…6`, `--avatar-fg`,
`--avatar-radius`, `--brand-mark-bg/fg`, `--btn-primary-shadow`, `--btn-radius`,
`--btn-shadow`, `--cal-lines`, `--card-bg`, `--card-border`, `--card-hover-shadow`,
`--card-hover-transform`, `--card-padding`, `--card-radius`, `--card-shadow`,
`--card-title-tracking`, `--chart-1/2`, `--chart-grid`, `--chart-height`,
`--chip-radius`, `--dash-gap`, `--drawer-bg`, `--easing-spring`, `--hour-h`,
`--kpi-cols`, `--kpi-label-tracking`, `--kpi-label-transform`, `--kpi-value-size`,
`--nav-active-bg/fg/shadow`, `--nav-badge-bg/fg`, `--nav-icon`, `--nav-marker`,
`--nav-radius`, `--page-title-tracking`, `--palette-accent-50…700`, `--palette-bg`,
`--palette-radius`, `--score-fill`, `--search-radius`, `--seg-on-bg/fg/shadow`,
`--select-arrow`, `--skeleton-bg`, `--spark-color`, `--table-head-bg`, `--toast-bg`,
`--topbar-bg`, `--d`.

Плюс в arctic, в отличие от оригинала: `--blob-1/2/3` (mesh-пятна) и `--cat-*`
(12 категориальных цветов графиков: `indigo, violet, success, info, warning,
neutral` × 2 тона).

**Сделать:** перечислить все новые токены в плане явно, разделив на
(а) «материал направления» — новый CSS-файл `design-system/components/bentoSurface.css`;
(б) токены, которые должны жить в `tokens.css`.

### 5.3 Имена не совпадают с планом

План называет подложку карточек `--surface-glass`, мокап использует `--card-bg`.
В плане это расхождение нужно устранить — выбрать одно имя и привести к нему и мокап,
и план, и код.

---

## 6. P2 — что нельзя перенести «подменой токенов»

### 6.1 KPI-полоса: 12-колоночная сетка

`bento-arctic.html:151` — `--kpi-cols: repeat(12, minmax(0, 1fr))`, отдельные плитки
занимают `span 4` (`:987`). В боевом `analytics.css:74` —
`repeat(auto-fill, minmax(150px, 1fr))`.

Это не токен, это layout. Нужен рерайт KPI-полосы в `analytics.css`, а не подмена
значения. Отдельно проверить поведение на 1280px.

### 6.2 Сайдбар и топбар

`bento-arctic.html:297` добавляет `height: 100vh`, `position: sticky`,
`z-index: var(--z-dropdown)`, `gap: var(--space-2)`. В боевом
`workspace.css:8-17` — `gap: var(--space-5)`, без sticky. Решить, нужен ли sticky
в продовом шелле (там своя компоновка), и не сломать ли это мобильную адаптацию
(`:941` — сайдбар уезжает за экран на `<900px`).

---

## 7. P2/P3 — требования заказчика (из `IMPLEMENTATION_BACKLOG.md`, подтверждены)

Эти требования подтверждены заказчиком и входят в объём работ.

### 7.1 Фильтр «Должность» в «Кандидатах»

* Поле в тулбаре: `frontend/src/features/candidates/CandidatesListPage.tsx`, блок
  `.list-toolbar-row.list-filters` (`:121`), рядом с «Этап»/«Источник»/
  «Ответственный»/«Сортировка»; использовать общий хелпер `SelectInput`
  (импорт на `:10`).
* Логика: `frontend/src/features/candidates/useCandidatesList.ts` — добавить
  `position` в состояние фильтров и в выборку.
* Канбан: `frontend/src/features/candidates/KanbanPage.tsx` — тот же фильтр на
  колонки воронки.
* Стили: `frontend/src/features/candidates/candidates.css` — селект по ширине как
  у «Источника».
* Поведение: Combine с остальными фильтрами, сброс кнопкой «Сбросить», значение
  видно в «активных чипах» над таблицей, пустое значение = «Все должности».

**Исправление ошибки в бэклоге:** там написано «Список брать из уже существующего
справочника вакансий, не заводить новый». **Справочника вакансий в проекте нет** —
`grep -rln 'vacanc' frontend/src` даёт 0 совпадений. Должность у кандидата —
свободный текст: `CandidateFormModal.tsx:35` (`position: string`), `:52`
(`useState("")`), `:84`, `:201`.

Поэтому: **не изобретать новый справочник молча.** Предложить в PR один из двух
вариантов и явно обозначить выбор:
(а) фильтр по свободному тексту `position` — без справочника, значение из
существующих данных;
(б) завести минимальный справочник должностей — отдельной задачей, не в этом PR.
Ревьюер должен увидеть, какой вариант выбран и почему.

### 7.2 Вкладка «Моя очередь»

* Экран `#/queue` в мокауте — персональный дашборд HR: задачи на сегодня,
  просроченные, ближайшие события, KPI недели, мини-воронка.
* `frontend/src/app-shell/Workspace.tsx:76-79` уже маршрутизирует HR в секцию
  `queue`; `:212` — `activeSection === "queue"`. Секцию нужно наполнить реальными
  данными, а не держать заглушкой.
* Метрики: «мои задачи», «просрочено», «выходы на неделе», «новые отклики».

Данные брать из существующих endpoint'ов. Новых полей в API не заводить без
отдельного согласования.

---

## 8. Порядок работ (делать последовательно, каждый шаг — отдельный коммит)

1. **Подготовка.** Базовая линия `npx vitest run` на `main`. Снять размер текущей
   сборки (`npm run build` → `dist/assets/*.css`, `*.js`) как точку отсчёта.
2. **Токены.** Перенос значений палитры arctic в `frontend/src/design-system/tokens.css`
   (замена боевых объявлений, §5.1). Новые токены — по §5.2, с явным разделением
   «в `tokens.css`» / «в `bentoSurface.css`». Привести имя `--card-bg` /
   `--surface-glass` к одному (§5.3).
3. **Имена.** Привести CSS мокаута к боевому API имён (§2). Покрыть варианты,
   которых нет в мокауте (`.btn-secondary`, `.btn-danger`, `.btn-md/sm`,
   `.icon-btn-*`, `.tabs`, `.tab-count`, `.avatar-md`, `.kpi-card-warning`,
   `.field-error`, `.text-input.is-invalid`, `.toast-title`).
4. **Конфликты.** Развести 10 классов из §3. Обязательно переименовать `.table-wrap`.
5. **Стекло и доступность.** §4 целиком: убрать с кнопок, добавить
   `prefers-reduced-transparency` и `prefers-contrast`, не потерять глобальный
   `prefers-reduced-motion`.
6. **Компоненты design-system.** `button.css`, `field.css`, `statusChip.css`,
   `avatar.css`, `drawer.css`, `modal.css`, `tabs.css`, `toast.css`,
   `stateViews.css` (скелетоны).
7. **Шелл.** `app-shell/workspace.css` — сайдбар и топбар (§6.2).
8. **Экраны.** `features/candidates/candidates.css`, `kanban.css`, `drawer.css`;
   `features/analytics/analytics.css` (включая рерайт KPI-полосы, §6.1);
   `features/calendar/calendar.css`; `features/documents/documents.css`;
   `features/library/library.css`; `styles.css`.
9. **Материал направления.** Один новый файл
   `frontend/src/design-system/components/bentoSurface.css` — mesh-подложка
   (`--blob-1/2/3`), стекло, spring-анимации, `--cat-*` для графиков.
10. **Требования заказчика.** §7.1 и §7.2, с явным выбором варианта по справочнику
    должностей.
11. **Проверка.** §9.

---

## 9. Обязательная проверка перед сдачей PR

* `npx vitest run` — сравнить с базовой линией из шага 1. Любое новое падение —
    либо починить, либо явно объяснить в PR, почему оно ожидаемо.
* `npx tsc -b`, `npx eslint .`, `npm run build` — чисто.
* Контраст: пересчитать по фактическим значениям токенов после переноса, включая
    композитинг `--card-bg` поверх mesh-пятна. Ожидание ≥ 4.5:1 для обычного текста.
    Эталон для сверки: `bento-arctic.html` даёт 15.00:1 (light) и 7.56:1 (dark) —
    после внедрения значения должны быть в том же диапазоне.
* Размер сборки: сравнить с точкой отсчёта. `backdrop-filter` на overlay и
    `filter: blur(90px)` на mesh — новые расходы; если рост > 15 %, написать об
    этом в PR явно.
* Новые имена классов — перечислены в описании PR.
* Боевой код: функционал, роутинг, состав полей, API — **ноль изменений**, кроме
    §7 (фильтр «Должность» и наполнение «Моей очереди»).
* Веб-шрифты: Inter остаётся только в мокауте. В прод — системный стек.

---

## 10. Описание PR (шаблон)

Заполнить и держать актуальным по мере работы:

```
## Что сделано
- палитра «Лазурь» перенесена в tokens.css (замена значений, не добавление :root поверх)
- N новых токенов: список
- новый файл: design-system/components/bentoSurface.css
- приведено к боевому API имён: N классов (список)
- конфликты разрешены: 10 классов (по каждому — что выбрано)
- стекло: убрано с .btn/.icon-btn, добавлены prefers-reduced-transparency и prefers-contrast
- требования заказчика: фильтр «Должность» (вариант: ...), экран «Моя очередь»

## Базовая линия и результат
- vitest до: X passed / Y failed
- vitest после: X passed / Y failed (расхождения: ...)
- размер сборки до/после: ...

## Новые классы (которых не было в проде)
- список с обоснованием

## Что сознательно не делаем
- функционал/роутинг/API/поля — не трогали
- веб-шрифты в прод — не подключаем
```

---

## 11. Если что-то непонятно или кажется неверным

Не пропускай молча. Напиши прямо в PR: что именно в этом промпте противоречит
коду, со ссылкой на файл и строку. Лучше остановка и вопрос, чем PR, который
ломает таблицы кандидатов.
