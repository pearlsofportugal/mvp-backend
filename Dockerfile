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
    pip wheel --no-cache-dir --wheel-dir /wheels .

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

COPY --from=builder /install /usr/local
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser migrations ./migrations
# COPY --chown=appuser:appuser entrypoint.sh ./

USER appuser

EXPOSE 8000

# ENTRYPOINT ["./entrypoint.sh"]
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD ["sh", "-c", "[ \"${RUN_MIGRATIONS:-true}\" = \"true\" ] && alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
