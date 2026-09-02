# Review Checklist — Design / Prototype

Для владельца проекта перед принятием дизайн-этапа.

## Визуальная целостность

- [ ] Единая палитра Signal Desk, без случайных цветов
- [ ] Типографика и spacing согласованы
- [ ] Нет glassmorphism / neon / landing-hero на app screens
- [ ] Статусы: chip = цвет + текст (+ optional icon)
- [ ] Иконки одного stroke-стиля
- [ ] Light theme читаем 8+ часов

## UX

- [ ] Все обязательные экраны доступны из nav/palette
- [ ] Table ↔ Kanban сохраняет фильтры
- [ ] Transfer требует reason + confirm
- [ ] Role-based nav (HR не видит Users)
- [ ] Empty / loading / error / forbidden / session states
- [ ] Command palette ищет кандидатов и команды
- [ ] Русский UI, понятные глаголы

## Accessibility

- [ ] Focus-visible виден
- [ ] Escape закрывает overlay
- [ ] Modal trap + return focus
- [ ] aria-live toasts
- [ ] Skip link
- [ ] Контраст текста ≥4.5:1 на основных парах
- [ ] prefers-reduced-motion
- [ ] Не заявляется ложная WCAG-сертификация

## Responsive

- [ ] 1440 / 1280 / 1024 usable
- [ ] Rail collapsible
- [ ] Tables scroll internally if needed

## Производительность прототипа

- [ ] Нет тяжёлых библиотек без нужды
- [ ] Мок-данные локальные
- [ ] Dev server / build быстрые

## Данные и безопасность артефактов

- [ ] Нет реальных ФИО/телефонов/email
- [ ] Email только @example.com/org/net
- [ ] Нет секретов, .env, токенов
- [ ] Нет внешних runtime fetch/CDN images
- [ ] Аватары = инициалы/SVG

## Изоляция от production

- [ ] `frontend/src` не изменён
- [ ] backend не изменён
- [ ] migrations / workflows / docker не изменены
- [ ] Нет merge в main
- [ ] Прототип только в `design-prototype/`
- [ ] Документация в `design/`

## Git

- [ ] Ветка опубликована
- [ ] Commit hash сверен через `git ls-remote`
- [ ] PR не создан без запроса владельца

## Запуск

- [ ] README / инструкция запуска прототипа работает
- [ ] lint / typecheck / build проходят
