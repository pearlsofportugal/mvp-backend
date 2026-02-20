# MVP Scraper — Backend API

Real estate scraper backend with REST API, built with FastAPI + PostgreSQL.

## Quick Start

### With Docker (recommended)

```bash
cd backend
cp .env.example .env
docker-compose up --build
```

The API will be available at `http://localhost:8000`.
- **Docs**: http://localhost:8000/docs (Swagger UI)
- **ReDoc**: http://localhost:8000/redoc
- **Health**: http://localhost:8000/health

### Local Development

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -e ".[dev]"

# Start PostgreSQL (e.g. via Docker)
docker run -d --name mvp-pg -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=mvp_scraper postgres:16-alpine

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Run migrations
alembic upgrade head

# Start the server
uvicorn app.main:app --reload
```

### Run Tests

```bash
pip install -e ".[dev]"
pytest -v
```

## API Endpoints

### Listings
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/listings` | List with filters, pagination, sorting |
| GET | `/api/v1/listings/{id}` | Get listing by ID |
| POST | `/api/v1/listings` | Create listing manually |
| PATCH | `/api/v1/listings/{id}` | Update listing fields |
| DELETE | `/api/v1/listings/{id}` | Delete listing |
| GET | `/api/v1/listings/stats` | Aggregated statistics |
| GET | `/api/v1/listings/duplicates` | Detect duplicates |

### Scrape Jobs
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/jobs` | Launch new scraping job |
| GET | `/api/v1/jobs` | List jobs |
| GET | `/api/v1/jobs/{id}` | Job status + progress |
| POST | `/api/v1/jobs/{id}/cancel` | Cancel running job |
| DELETE | `/api/v1/jobs/{id}` | Delete job record |

### Site Configs
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/sites` | List configured sites |
| GET | `/api/v1/sites/{key}` | Get site config |
| POST | `/api/v1/sites` | Add new site |
| PATCH | `/api/v1/sites/{key}` | Update site selectors |
| DELETE | `/api/v1/sites/{key}` | Deactivate site |

### AI Enrichment
> Router prefix: `/api/v1/enrichment/ai`

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/enrichment/ai/optimize` | Optimize free text with AI (SEO title/description/meta) |
| POST | `/api/v1/enrichment/ai/listing` | Enrich a specific listing by ID |
| GET | `/api/v1/enrichment/ai/preview/{listing_id}` | Preview AI enrichment without saving |
| GET | `/api/v1/enrichment/ai/stats` | Aggregated enrichment statistics |

#### `POST /api/v1/enrichment/ai/listing` — request body
```json
{
  "listing_id": "uuid",
  "fields": ["title", "description", "meta_description"],
  "keywords": ["optional", "custom", "keywords"],
  "apply": false,
  "force": false
}
```
- `apply: true` persiste as alterações na base de dados
- `force: true` regenera mesmo que o campo já tenha valor
- Se `keywords` for vazio, são inferidas automaticamente a partir do listing

### Export
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/export/csv` | Download filtered CSV |
| GET | `/api/v1/export/json` | Download filtered JSON |
| GET | `/api/v1/export/excel` | Download filtered Excel |

## Architecture

```
backend/
├── alembic/              # Database migrations
├── app/
│   ├── main.py           # FastAPI app factory + healthcheck
│   ├── config.py         # Settings from .env
│   ├── database.py       # Async SQLAlchemy engine
│   ├── models/           # SQLAlchemy ORM models
│   ├── schemas/          # Pydantic request/response schemas
│   ├── api/v1/           # REST API routers
│   ├── services/         # Business logic (scraping, parsing, enrichment)
│   └── core/             # Logging, exceptions
├── tests/                # pytest test suite
├── docker-compose.yml    # PostgreSQL + API
└── Dockerfile
```

## Ethical Scraping Rules

1. **Fail-closed robots.txt** — if robots.txt can't be loaded, ALL requests to that domain are blocked
2. **Mandatory rate limiting** — random delay between requests (configurable min/max delay)
3. **Identifiable User-Agent** — must include bot name + contact info
4. **Retries with exponential backoff** — 429/5xx retriable, 4xx returns None immediately
5. **Per-domain robots.txt cache** — 1-hour TTL to avoid hammering robots.txt endpoints
6. **URL deduplication** — within a job, the same URL is never fetched twice