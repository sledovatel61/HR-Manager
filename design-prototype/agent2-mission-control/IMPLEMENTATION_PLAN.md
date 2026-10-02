# Mission Control · план реализации одним PR

Статический прототип, без изменений продукта. Палитра: canvas `#0b1019`, app `#111925`, raised `#172231`, accent `#72e2cc`; светлая: canvas `#edf3f4`, app `#f8fbfa`, accent `#08766c`. Статусы всегда содержат подпись и точку, не полагаются только на цвет.

| Semantic-токен | Dark | Light |
|---|---|---|
| `--surface-canvas` | `#0b1019` | `#edf3f4` |
| `--surface-app` | `#111925` | `#f8fbfa` |
| `--surface-raised` | `#172231` | `#ffffff` |
| `--surface-sunken` | `#0e1621` | `#e9f0f0` |
| `--surface-sidebar` | `#0b121d` | `#10222e` |
| `--text-primary` | `#f4f7f9` | `#142833` |
| `--text-secondary` | `#b9c7d2` | `#405762` |
| `--text-tertiary` | `#94a8b7` | `#536e78` |
| `--border-subtle` | `#263747` | `#d7e2e4` |
| `--border-default` | `#344556` | `#c4d4d7` |
| `--border-focus` | `#67e5d0` | `#08796f` |
| `--accent-default` | `#72e2cc` | `#08766c` |
| `--accent-subtle` | `#153b3c` | `#d8f0ed` |
| `--status-success-{fg,bg,border}` | `#90e6c2, #193d36, #356a58` | `#12634a, #e1f4e9, #addac1` |
| `--status-warning-{fg,bg,border}` | `#f5ce8c, #45351f, #715535` | `#79510d, #fff2d8, #edcc91` |
| `--status-danger-{fg,bg,border}` | `#ffabb2, #462832, #75404a` | `#9b3240, #fce9eb, #eac0c6` |

Остальные semantic-токены (info, neutral, violet, teal, indigo, overlay, hover, pressed, selected и disabled), а также spacing/radius/shadow/type/motion/layout описаны в `style.css` по именам из `frontend/src/design-system/tokens.css`. Плотность меняет `--row-height`, `--control-height-md` и spacing. Никаких внешних шрифтов не требуется.

## Файлы будущего PR

1. `frontend/src/design-system/tokens.css` — палитра обеих тем, focus и density.
2. `frontend/src/app-shell/workspace.css`, `frontend/src/styles.css` — сайдбар, верхняя панель, сетка.
3. `frontend/src/design-system/components/{button,statusChip,avatar,drawer,modal,tabs,field,toast,stateViews}.css` — общий язык контролов.
4. `frontend/src/features/{candidates/candidates,candidates/kanban,candidates/drawer,analytics/analytics,documents/documents,calendar/calendar}.css` — таблица, карточки и разделы.

Риски: проверить контраст всех комбинаций статусных токенов и длинные русские строки, адаптивную ширину kanban и keyboard drag-and-drop существующей реализации. Не менять API, модель данных, роутинг, drag-and-drop логику и доступность. CSS-only restyle и подмена токенов укладываются в один PR; макетные графики/демо-данные не переносить буквально в продукт.
