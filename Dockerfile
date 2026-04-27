FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./

# Gera wheels — camada cacheada enquanto pyproject.toml não mudar
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /wheels . && \
    pip install --no-cache-dir playwright && \
    playwright install chromium --with-deps

# Código só depois — não invalida o cache de dependências
COPY app ./app

RUN pip install --no-cache-dir --prefix=/install /wheels/*

# --- Runtime ---
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd -r appuser && useradd -r -g appuser appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Instalar binários do Playwright antes de copiar do builder — esta camada
# não depende do código da app, por isso fica cacheada entre builds.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN pip install playwright && \
    playwright install chromium --with-deps && \
    chmod -R a+rx /opt/ms-playwright

COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser alembic ./alembic
# COPY --chown=appuser:appuser entrypoint.sh ./

USER appuser

EXPOSE 8000

# ENTRYPOINT ["./entrypoint.sh"]
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["sh", "-c", "[ \"${RUN_MIGRATIONS:-true}\" = \"true\" ] && alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
