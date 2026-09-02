# --- Этап 1: сборочный образ backend -------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Пакет ставится в editable-режиме: bind-mount ./backend/src поверх
# делает правки кода видимыми в контейнере без пересборки (dev-поток).
COPY pyproject.toml ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

RUN pip install --upgrade pip \
 && pip install -e ".[dev]"

EXPOSE 8000

CMD ["uvicorn", "hr_manager.main:app", "--host", "0.0.0.0", "--port", "8000"]
