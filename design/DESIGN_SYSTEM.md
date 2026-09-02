# Design System — Signal Desk (HR Manager)

**Версия:** 0.1 · **Дата:** 2026-09-02 · **Тема по умолчанию:** Light

Токены реализованы в `design-prototype/src/styles/tokens.css`.

---

## 1. Foundations

### 1.1 Color — primitive

| Token | Light | Dark (proposed) |
|---|---|---|
| gray-0 | `#FFFFFF` | `#0B0F19` |
| gray-25 | `#F8F9FB` | `#111827` |
| gray-50 | `#F4F5F7` | `#1F2937` |
| gray-100 | `#E5E7EB` | `#374151` |
| gray-200 | `#D1D5DB` | `#4B5563` |
| gray-400 | `#9CA3AF` | `#9CA3AF` |
| gray-500 | `#6B7280` | `#D1D5DB` |
| gray-700 | `#374151` | `#E5E7EB` |
| gray-900 | `#111827` | `#F9FAFB` |
| indigo-50 | `#EEF2FF` | — |
| indigo-100 | `#E0E7FF` | — |
| indigo-500 | `#6366F1` | `#818CF8` |
| indigo-600 | `#4F46E5` | `#818CF8` |
| indigo-700 | `#4338CA` | `#A5B4FC` |
| teal-600 | `#0F766E` | `#2DD4BF` |
| teal-50 | `#F0FDFA` | — |
| blue-600 | `#2563EB` | `#60A5FA` |
| violet-600 | `#7C3AED` | `#A78BFA` |
| amber-600 | `#D97706` | `#FBBF24` |
| rose-600 | `#E11D48` | `#FB7185` |
| green-600 | `#059669` | `#34D399` |

### 1.2 Semantic colors

```
--bg-app: gray-50
--bg-surface: gray-0
--bg-sunken: gray-25
--bg-elevated: gray-0
--bg-brand-subtle: indigo-50
--bg-overlay: rgba(17,24,39,.45)

--text-primary: gray-900
--text-secondary: gray-500
--text-muted: gray-400
--text-inverse: #fff
--text-brand: indigo-700
--text-danger: rose-600
--text-success: green-600
--text-warning: amber-600

--border-subtle: gray-100
--border-default: gray-200
--border-strong: gray-400
--border-focus: indigo-600

--action-primary: indigo-600
--action-primary-hover: indigo-700
--action-danger: rose-600
```

### 1.3 Status tokens (dot + label required)

| Status | Token bg | Token fg | Dot |
|---|---|---|---|
| new | slate-50 | slate-700 | slate-500 |
| contact | blue-50 | blue-800 | blue-600 |
| reached | sky-50 | sky-800 | sky-600 |
| interview_scheduled | violet-50 | violet-800 | violet-600 |
| interview_done | purple-50 | purple-800 | purple-600 |
| offer | amber-50 | amber-900 | amber-600 |
| hired | teal-50 | teal-900 | teal-600 |
| probation | teal-50 | teal-800 | teal-500 |
| rejected | rose-50 | rose-800 | rose-600 |
| left | gray-100 | gray-700 | gray-500 |

### 1.4 Spacing scale (4px base)

`0, 1=4, 2=8, 3=12, 4=16, 5=20, 6=24, 8=32, 10=40, 12=48, 16=64`

### 1.5 Radius

| Token | Value | Use |
|---|---|---|
| radius-sm | 4px | inputs, chips |
| radius-md | 8px | buttons, cards, menus |
| radius-lg | 12px | dialogs, palette |
| radius-full | 9999px | avatars, pills |

Avoid >12px on containers (anti-pattern: «мыльный» UI).

### 1.6 Elevation

```
--shadow-xs: 0 1px 2px rgba(17,24,39,.06)
--shadow-sm: 0 1px 3px rgba(17,24,39,.08), 0 1px 2px rgba(17,24,39,.04)
--shadow-md: 0 4px 12px rgba(17,24,39,.10)
--shadow-lg: 0 12px 32px rgba(17,24,39,.16)
```

### 1.7 Typography

Font stack: `Inter, "Segoe UI", system-ui, -apple-system, sans-serif`
Mono: `ui-monospace, "SF Mono", Menlo, monospace`

| Role | Size / Line / Weight |
|---|---|
| display | 28/36/600 |
| title | 20/28/600 |
| subtitle | 16/24/600 |
| body | 14/20/400 |
| body-sm | 13/18/400 |
| caption | 12/16/400 |
| overline | 11/14/600 uppercase tracking |

Tabular nums for dates, phones, KPI.

### 1.8 Icons

Sizes: 14, 16, 20, 24. Stroke 1.75. Decorative `aria-hidden`; interactive buttons need accessible name.

### 1.9 Grid & breakpoints

- Content max 1440 content area
- Rail 240 / collapsed 64
- Gutter 24 (16 compact)
- Breakpoints: 1024, 1280, 1440

### 1.10 Motion

| Token | Value |
|---|---|
| duration-fast | 120ms |
| duration-normal | 180ms |
| duration-slow | 280ms |
| easing-standard | cubic-bezier(0.2, 0, 0, 1) |
| easing-emphasized | cubic-bezier(0.2, 0, 0, 1) |

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

### 1.11 Focus ring

```
outline: 2px solid var(--border-focus);
outline-offset: 2px;
```
Never remove without replacement. `:focus-visible` only.

### 1.12 Z-index

```
--z-rail: 30
--z-header: 40
--z-dropdown: 50
--z-drawer: 60
--z-modal: 70
--z-palette: 80
--z-toast: 90
--z-blocking: 100
```

---

## 2. Components (summary)

### Button
- Variants: primary, secondary, ghost, danger, link
- Sizes: sm (28), md (32), lg (40)
- States: default, hover, active, focus-visible, disabled, loading (spinner + aria-busy)
- Icon button: square, aria-label required

### Input / Password / Select / Combobox
- Height 32/36; label above; hint/error below
- Password: toggle show, `aria-pressed`
- Error: border danger + message linked `aria-describedby`
- Combobox: listbox APG pattern

### Date/Time
- Native `datetime-local` in prototype; production: accessible picker

### Checkbox / Radio / Switch
- 16px control; label clickable; switch has `role="switch"`

### Badge / Status chip
- Chip = dot + text; never color alone
- Sizes sm/md

### Avatar
- Initials on brand-subtle; sizes 24/32/40; title = full name

### Tooltip
- Delay 400ms; keyboard focus shows; never sole info carrier

### Dropdown menu
- roving tabindex; Escape; click-outside; aria-expanded

### Tabs / Segmented
- Tabs: arrow keys; Segmented: radiogroup pattern for view switch

### Command palette
- dialog + combobox; groups; empty state; recent

### Toast
- region aria-live polite; auto-dismiss 4s; pause on hover/focus; action optional

### Modal / Confirm
- role=dialog aria-modal; focus trap; initial focus primary or first field; return focus
- Confirm: destructive button explicit label

### Drawer
- Right 420–480px; same a11y as dialog or complementary with close

### Table
- semantic table; sticky thead; sortable buttons; row cursor; checkbox optional
- Density: comfortable 48 / compact 40
- Empty colspan message

### Pagination
- «1–25 из 128»; prev/next; page size select

### Filters / Saved views
- Chip row; panel; save dialog

### Timeline / Activity
- Vertical line; icon by type; time absolute + relative; actor

### Kanban card
- Name, vacancy, owner avatar, status implicit by column, next event meta
- Keyboard menu Move

### Calendar event
- Time + title; type color left border + label

### KPI
- Value large tabular; delta; definition tooltip; not 12 equal tiles

### Charts
- Funnel bars with values + %; bar compare HR; legend text

### Empty / Skeleton / Error / Forbidden / Session
- Illustration optional SVG abstract; title; description; CTA
- Skeleton: pulse shapes matching layout (no random)
- Session: blocking full viewport

---

## 3. Themes

Light = default.
Dark: invert surfaces via semantic tokens only; status hues retuned for contrast; test AA before ship. Prototype may include `data-theme="dark"` stub.

---

## 4. Anti-patterns

- Neon gradients, heavy glass
- Radius 24px everywhere
- Color-only status
- Hover-only actions
- Modal for every edit (prefer drawer)
- KPI bingo (identical cards grid)
- Disabling focus outline
- Non-labeled icon buttons
- Infinite animation loops
- Real PII or external avatar URLs
