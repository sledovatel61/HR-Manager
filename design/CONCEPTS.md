# Три визуальных направления и рекомендация

**Дата:** 2026-09-02
**Продукт:** HR Manager

---

## Концепция A — «Quiet Ledger»

### Идея
Премиальный спокойный enterprise: интерфейс как аккуратная бухгалтерская книга подбора. Минимум декора, максимум читаемости и доверия.

### Настроение
Сдержанное, надёжное, «банк / юрфирма / enterprise HR». Тишина и ясность.

### Цветовая палитра
- Фон: `#F7F6F3` (warm paper), поверхности `#FFFFFF`
- Текст: `#1A1A1A` / `#5C5C5C`
- Accent: `#1F4E79` (deep navy)
- Success/Warn/Danger: приглушённые forest / amber / brick
- Borders: `#E4E2DC`

### Типографика
- UI: IBM Plex Sans / Source Sans 3
- Tabular nums для дат и KPI
- Размеры: 13–14px body, 11px meta, 20–24px page title

### Навигация
Левый sidebar узкий (220px), текстовые пункты, без иконок-гигантов. Top bar только user + search.

### Плотность
Comfortable → medium. Таблицы 48px row.

### Таблицы
Тонкие hairline borders, без zebra или очень мягкий; sticky header на paper-tone.

### Карточка кандидата
Двухколоночный layout: слева identity + fields, справа timeline на paper background.

### Статусы
Outline chips, navy/gray variants, text label always present.

### Аналитика
Классические bar/line, мало цвета, подписи осей, «report» aesthetic.

### Анимация
Почти нет: 120ms fade для panel. Respect reduced-motion.

### Сильные стороны
Доверие, читаемость 8+ часов, отличный print/export feel, accessibility-friendly contrast.

### Риски
Может выглядеть «устаревшим» для молодых HR; слабый wow; легко спутать с generic admin.

### Пригодность daily
**Отлично** — основной критерий закрыт.

---

## Концепция B — «Editorial Pipeline»

### Идея
Современный editorial / data-rich интерфейс: типографика как в качественном медиа, данные — как в хорошем BI. Вдохновение: Attio + Notion + Stripe tables.

### Настроение
Умный, современный, «продуктовый», чуть журнальный.

### Цветовая палитра
- Фон: `#FAFAF9`, surface `#FFF`
- Текст: `#0C0A09` / `#78716C`
- Accent: `#0D9488` (teal) + `#44403C` charcoal
- Soft pastel status washes (5–8% opacity)
- Hairline `#E7E5E4`

### Типографика
- Display: tight tracking titles (Inter / Geist)
- Body 14px, strong hierarchy H1 28px
- Mono для ID/телефонов

### Навигация
Top nav + contextual secondary tabs. Sidebar только для admin/settings. Больше горизонтального воздуха.

### Плотность
Medium-low: больше whitespace, «editorial breathing».

### Таблицы
Крупные primary cells (имя), secondary meta muted; row hover lift 1px shadow soft.

### Карточка
Full-bleed header с именем крупно; tabs: Обзор / Timeline / События / Документы.

### Статусы
Soft filled pills с иконкой-точкой.

### Аналитика
Карточки-инсайты с one-sentence summary + sparkline; funnel как horizontal stepped bars.

### Анимация
Мягкие 200ms ease-out transitions; skeleton shimmer slow.

### Сильные стороны
Запоминающийся, приятный, сильный first impression; хорошо для руководителя.

### Риски
Whitespace съедает data density; HR с 200 кандидатами может раздражаться скроллом; top-nav хуже scale при 10+ разделах.

### Пригодность daily
**Хорошо** для руководителя/аналитики; **средне** для high-volume HR operations.

---

## Концепция C — «Signal Desk»

### Идея
Динамичный command-center recruiting operations: скорость Linear + pipeline ATS + спокойный enterprise chrome. Интерфейс ощущается как «пульт смены», а не лендинг и не бухгалтерия.

### Настроение
Сфокусированное, быстрое, уверенное. «Я контролирую очередь».

### Цветовая палитра
- App bg: `#F4F5F7` cool gray
- Surface: `#FFFFFF`
- Elevated: `#FFFFFF` + shadow-sm
- Text primary: `#111827`, secondary `#6B7280`
- **Brand indigo:** `#4F46E5` (primary actions, focus)
- **Signal teal:** `#0F766E` (positive / hired path)
- Status map:
  - new: slate
  - contact/call: blue
  - interview: violet
  - offer: amber
  - hired/probation: teal
  - rejected/left: rose
- Borders: `#E5E7EB`, strong `#D1D5DB`

### Типографика
- Inter / system-ui stack (production-friendly)
- 13px compact UI, 14px comfortable
- Semibold labels, regular body
- Tabular lining nums

### Навигация
**Icon+label left rail** (240px collapsible to 64px icons).
Top: global search trigger, command hint `⌘K`, notifications, density, user menu.
Role-aware items (Users/Audit only admin; Analytics emphasized for manager).

### Плотность
**Dual:** Comfortable (default) / Compact (power). Switch в header.

### Таблицы
Compact rows 40px, hover bg, selected row indigo-50, sticky name col, inline status select, owner avatar+name, quick actions on focus/hover with keyboard parity.

### Карточка кандидата
**Split view:** list/kanban остаётся слева (optional) или full page с back.
Header: name, status chip, owner, primary CTA (Позвонить / Запланировать / Передать).
Main: timeline-first. Side: properties panel.

### Статусы
Chip = color dot + label; kanban column accent top border.

### Аналитика
KPI strip (4 metrics) + funnel visualization + HR comparison table — не плиточный «bingo».

### Анимация
120–180ms; command palette scale+fade; toast slide; kanban card subtle lift. All gated by `prefers-reduced-motion`.

### Сильные стороны
Wow без китча; daily speed; keyboard-first; role scalability; современный B2B SaaS look.

### Риски
Сложнее в реализации; опасность перегрузить shortcuts; indigo может «кричать» если злоупотреблять.

### Пригодность daily
**Отлично** для HR operations; **отлично** для руководителя с analytics mode.

---

## Сравнение

| Критерий | A Quiet Ledger | B Editorial | C Signal Desk |
|---|---|---|---|
| Daily HR speed | ★★★★★ | ★★★ | ★★★★★ |
| Wow / memorability | ★★ | ★★★★ | ★★★★★ |
| Data density | ★★★★ | ★★★ | ★★★★★ |
| Analytics clarity | ★★★★ | ★★★★★ | ★★★★ |
| A11y baseline | ★★★★★ | ★★★★ | ★★★★ |
| Implementation risk | Low | Medium | Medium |
| Fit to PRODUCT_SPEC | ★★★★ | ★★★ | ★★★★★ |
| Distinct from bootstrap admin | ★★★ | ★★★★★ | ★★★★★ |

---

## Рекомендация: **Signal Desk (C)** с элементами A

### Почему
1. Продукт — **операционный инструмент на много часов**, не showcase. C оптимизирован под очередь, таблицу, передачу, события.
2. Требование **wow** закрывается command palette, timeline-first card, funnel viz, density switch — функциональный wow.
3. Из A берём: restrained elevation, strong contrast, no decorative noise, calm neutrals.
4. Из B берём: качественную типографическую иерархию заголовков карточки и analytics insight lines.
5. Роадмап (кандидаты → UI HR → календарь → аналитика) совпадает с primary surfaces C.

### Гибридные правила (рекомендуемый стиль)
- Palette и chrome — **Signal Desk**
- Motion и elevation — ближе к **Quiet Ledger** (сдержанно)
- Analytics copy — **Editorial** one-liners под KPI
- Никакого glassmorphism / neon

### Rationale short
> HR Manager должен ощущаться как **точный инструмент смены рекрутинга**: быстро найти, сменить статус, запланировать, передать, увидеть воронку — с характером современного SaaS, без потери enterprise-дисциплины.
