#!/bin/sh
# Точка входа backend-контейнера.
#
# Перед стартом приложения применяем миграции: в docker compose это делает
# запуск самодостаточным (`docker compose up --build` сразу даёт рабочий стек).
# SKIP_MIGRATIONS=1 отключает шаг для сценариев, где миграции применяет
# отдельный контролируемый release-процесс (см. ROADMAP.md, Этап 7).
set -eu

if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
    echo "entrypoint: применяю миграции Alembic"
    alembic upgrade head
fi

exec "$@"
