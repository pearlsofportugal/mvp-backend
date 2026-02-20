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

### Enrichment
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/enrichment/run` | Batch enrich descriptions |
| POST | `/api/v1/enrichment/preview/{id}` | Preview enrichment |
| GET | `/api/v1/enrichment/stats` | Quality statistics |

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
2. **Mandatory rate limiting** — random delay between requests
3. **Identifiable User-Agent** — includes bot name + contact
4. **Exponential backoff** — on 429/5xx errors
5. **URL deduplication** — never visit the same URL twice per job
