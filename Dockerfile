# ── Estágio 1: Build ─────────────────────────────────────────────────────────
# Usamos a mesma versão (3.14) em ambos os estágios
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# Instalamos apenas no prefixo /install para ser fácil de copiar
RUN pip install --upgrade pip && \
    pip install --prefix=/install --no-cache-dir .

# ── Estágio 2: Runtime ───────────────────────────────────────────────────────
FROM python:3.14-slim

WORKDIR /app

# Criar utilizador seguro
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Libpq5 é a única dependência de sistema necessária para o Postgres
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copiamos apenas o que o pip instalou, sem o lixo do compilador
COPY --from=builder /install /usr/local

# COPIAR O CÓDIGO (Mas atenção ao .dockerignore!)
COPY --chown=appuser:appuser . .

USER appuser

EXPOSE 8000

# CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]