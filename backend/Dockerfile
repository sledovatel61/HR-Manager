# HR Manager backend image: application code only, runs migrations then uvicorn.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN addgroup --system app && adduser --system --ingroup app --no-create-home app
USER app

EXPOSE 8000

# Dev/staging convenience: bring the schema up to date, then serve.
# Production release process (separate migration job, rollback) arrives with
# the deployment phase; see docs/ARCHITECTURE.md.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
