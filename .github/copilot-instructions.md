# Copilot Instructions — MVP-Scraper Backend

Backend de scraping imobiliário em Portugal. Extrai anúncios de portais configurados, normaliza os dados, persiste numa base de dados PostgreSQL e expõe uma API REST com autenticação por API key.

---

## ❌ Proibido — Nunca gerar isto

- `HTTPException` diretamente nos serviços — usar exceções de domínio de `app/core/exceptions.py`
- `os.environ` ou `os.getenv()` fora de `app/config.py` — usar sempre `settings.*`
- `session.execute()` síncrono — sempre `await session.execute()`
- Lazy loading de relacionamentos SQLAlchemy — usar `selectinload()` explicitamente
- `List[str]`, `Dict[str, Any]`, `Optional[str]` do `typing` — usar `list[str]`, `dict[str, Any]`, `str | None` (Python 3.10+)
- Devolver `dict` ou modelo Pydantic diretamente nos endpoints — sempre usar `ok()` de `responses.py`
- Inserir direto na DB em testes — usar a API (POST) salvo testes de baixo nível
- `@pytest.mark.asyncio` — `asyncio_mode = "auto"` já está configurado em `pyproject.toml`
- Migrações destrutivas (`DROP COLUMN`, `NOT NULL` sem default) — ver secção Alembic
- Variáveis globais com estado mutável
- Ignorar avisos do Mypy ou Pyright

---

## 1. Visão Geral do Projeto

**Finalidade:** API REST de scraping imobiliário. Permite:
- Configurar sites de scraping (CSS selectors, URL base, paginação)
- Lançar scrape jobs em background com progresso via SSE
- Explorar e filtrar listings imobiliários scraped
- Enriquecer descrições via Google Gemini
- Exportar dados em CSV/JSON/Excel

**Stack principal:**

| Camada | Tecnologia |
|---|---|
| Framework HTTP | FastAPI `>=0.110.0` |
| ORM | SQLAlchemy `>=2.0.25` — modo async (`asyncpg`) |
| Validação | Pydantic v2 `>=2.5.0` |
| Migrações | Alembic `>=1.13.0` |
| Parsing HTML | BeautifulSoup4 + lxml |
| HTTP cliente | httpx `>=0.27.0` (async), requests (sync/ethics) |
| IA | Google Gemini — `google-genai>=1.63.0` |
| Testes | pytest-asyncio + SQLite (`aiosqlite`) |
| Deploy | Docker Compose |

**Python:** `>=3.12,<3.14`

---

## 2. Arquitetura

```
backend/
├── app/
│   ├── main.py              # App factory, lifespan, middleware, routers
│   ├── config.py            # Todas as settings via pydantic-settings (BaseSettings)
│   ├── database.py          # Engine async, session factory, Base
│   ├── api/
│   │   ├── deps.py          # get_db(), RequireApiKey
│   │   ├── responses.py     # ok(), ERROR_RESPONSES, Meta
│   │   └── v1/
│   │       ├── listings.py        # CRUD + filtros + stats
│   │       ├── scrape_jobs.py     # Ciclo de vida dos jobs + SSE
│   │       ├── sites.py           # Gestão de SiteConfig + sugestão de seletores
│   │       ├── ai_enrichment.py   # Enriquecimento com Gemini
│   │       └── export.py          # CSV / JSON / Excel
│   ├── models/              # SQLAlchemy ORM (Mapped / mapped_column)
│   ├── schemas/             # Pydantic v2 (Create / Update / Read separados)
│   ├── services/            # Lógica de negócio
│   ├── crawler/             # Utilitários de scraping (cache, seletor, confiança)
│   └── core/                # Exceções, logging estruturado
├── alembic/                 # Migrations
└── tests/
    ├── conftest.py          # Fixtures: db_session, client
    ├── test_api/            # Testes de integração por router
    └── test_services/       # Testes unitários de serviços
```

### Fluxo de um pedido de scraping

```
POST /api/v1/jobs
  → scrape_jobs.py (router)
      → BackgroundTasks.add_task(run_scrape_job)
          → scraper_service.run_scrape_job()
              → EthicalScraper (ethics_service) — robots.txt + rate limiting
              → parser_service — extrai campos do HTML
              → mapper_service — normaliza para PropertySchema
              → DB upsert (source_url como chave de deduplicação)
              → ScrapeJob.update_progress() / add_log() / touch_heartbeat()
```

### Fluxo de um pedido CRUD normal

```
GET /api/v1/listings
  → listings.py (router)
      → get_db() (deps.py) — AsyncSession por pedido
      → query SQLAlchemy com selectinload()
      → ok(data, meta=Meta(...)) — envelope ApiResponse[T]
```

---

## 3. Padrões de Código

### Rotas FastAPI

- Todos os endpoints (exceto `/health` e `/docs`) usam `RequireApiKey` como dependência de segurança.
- Respostas envolvidas **sempre** com `ok()` → devolve `ApiResponse[T]`. Nunca devolver `dict` ou modelo diretamente.
- Parâmetros de query declarados com `Query(...)` com defaults explícitos e constraints (`ge`, `le`, `min_length`).
- `Request` injetado apenas quando necessário o `trace_id`.

```python
# app/api/v1/listings.py
@router.get("/", response_model=ApiResponse[PaginatedResponse])
async def list_listings(
    request: Request,
    district: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: str = Security(RequireApiKey),
):
    ...
    return ok(data=PaginatedResponse(items=items, total=total), meta=Meta(...), request=request)
```

### Schemas Pydantic v2

- Separação rigorosa: `*Create`, `*Update`, `*Read`, `*ListRead`, `*DetailRead`.
- `*Update` usa todos os campos `field | None = None` para suportar PATCH parcial.
- Usar `Field(...)` para constraints (`min_length`, `ge`, `le`, `pattern`).
- Envelope genérico `ApiResponse[T]` com `success`, `data`, `meta`, `message`, `errors`, `trace_id`.

```python
# app/schemas/base_schema.py
class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T | None = None
    meta: Meta | None = None
    message: str | None = None
    errors: list[ErrorDetail] | None = None
    trace_id: str | None = None
```

### Async/Await

- Toda a camada de dados usa `async with async_session_factory() as session`.
- `get_db()` em `deps.py` faz rollback automático em caso de exceção.
- Chamadas síncronas bloqueantes (Google Gemini) delegadas a `asyncio.to_thread()`.
- Nunca usar `session.execute()` de forma síncrona — sempre `await session.execute()`.

### Tratamento de erros

Hierarquia de exceções de domínio em `app/core/exceptions.py`:

```
AppException
├── NotFoundError
├── DuplicateError
├── ScrapingError
│   ├── RobotsBlockedError
│   └── RateLimitError
├── ParsingError
├── EnrichmentError
├── ExportError
├── JobAlreadyRunningError
└── JobCancelledError
```

- Nunca usar `HTTPException` diretamente nos serviços — apenas nos routers em casos excecionais.
- Handlers globais em `main.py` traduzem exceções de domínio para respostas HTTP.

```python
# Correto — em qualquer serviço
if not listing:
    raise NotFoundError(f"Listing {listing_id} not found")

# Errado — não usar nos serviços
raise HTTPException(status_code=404, detail="not found")
```

### Autenticação

- Header `X-API-Key` comparado com `settings.api_key` via `secrets.compare_digest` (proteção timing-attack).
- `RequireApiKey = Security(verify_api_key)` adicionado a todos os endpoints protegidos.

### Logging estruturado

```python
from app.core.logging import get_logger
logger = get_logger(__name__)
logger.info("Job started", extra={"job_id": str(job_id), "site_key": site_key})
```

Sempre incluir `job_id`, `site_key`, ou `listing_id` no `extra` para correlação de logs.

---

## 4. Base de Dados

### Modelos SQLAlchemy (estilo `mapped_column`)

- Todos usam `Mapped[T]` + `mapped_column(...)` do SQLAlchemy 2.0.
- Chaves primárias são UUID gerados pelo Python (`default=uuid.uuid4`).
- Timestamps em UTC: `default=lambda: datetime.now(timezone.utc)`.
- `expire_on_commit=False` na session factory — evita lazy loads pós-commit.
- Por defeito, todos os campos de anúncio são `Optional` (exceto `id`, `source_partner`, `created_at`).

```python
# app/models/listing_model.py
class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_url: Mapped[str | None] = mapped_column(String(2048), unique=True)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    district: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
```

### Sessão e transações

- Sessão por pedido HTTP via `get_db()` (generator com `try/except/finally`).
- Jobs de scraping usam uma única sessão para toda a vida do job.
- `selectinload` para eager loading — sem lazy loading:

```python
stmt = select(Listing).options(
    selectinload(Listing.media_assets),
    selectinload(Listing.price_history),
).where(Listing.id == listing_id)
```

### Deduplicação

`source_url` é `unique=True` na tabela `listings`. O scraper faz `SELECT` por `source_url` → se existe, faz `UPDATE`; se não, faz `INSERT`. Nunca usar `INSERT OR REPLACE` — o histórico de preços depende do upsert explícito.

### Alembic

```bash
alembic revision --autogenerate -m "descrição"  # gerar migration
alembic upgrade head                              # aplicar
```

- Migrations em `alembic/versions/`.
- Nunca editar migrations já aplicadas em produção.
- Colunas novas sempre `nullable=True` ou com `server_default`.
- Nunca `DROP COLUMN` sem a coluna já estar vazia em produção.

---

## 5. Scraping e Automação

### EthicalScraper (`app/services/ethics_service.py`)

Respeito obrigatório por `robots.txt` e rate limiting. Fail-closed: se o robots.txt não for acessível, a página é bloqueada.

```python
scraper = EthicalScraper(
    user_agent="MVPScraper/1.0 (+https://exemplo.com)",
    min_delay=2.0,
    max_delay=5.0,
    extra_headers={"Accept-Language": "pt-PT"},
)
html = scraper.fetch(url)  # None se bloqueado ou erro 4xx permanente
```

- User-Agent obrigatoriamente no formato `"BotName/Version (+contact)"`.
- Delay aleatório aplicado **antes** de cada pedido.
- Retries com backoff exponencial para 429 e 5xx; 4xx permanentes devolvem `None`.
- Cache de `robots.txt` por domínio com TTL de 1 hora.

### Parser (`app/services/parser_service.py`)

Dois modos de extração configurados por `SiteConfig.extraction_mode`:

| Modo | Descrição |
|---|---|
| `direct` | CSS selector direto para cada campo |
| `section` | Extrai pares `.name` / `.value` de uma secção HTML |

- Mapeamentos de campo carregados da tabela `FieldMapping` (cache de 5 minutos).
- DB tem prioridade sobre mapa estático interno (`_SUMMARY_FIELD_MAP`).

### Mapper (`app/services/mapper_service.py`)

Normaliza dados brutos para `PropertySchema`:

- Preços: `"250 000 €"` → `(250000.0, "EUR")` — suporta notação europeia.
- Áreas: `"120 m²"` → `120.0`.
- Tipologia: `"T3"` → `bedrooms=3`.
- Booleanos: `"Sim"/"Yes"` → `True`, `"Não"/"No"` → `False`.
- `price_per_m2` calculado automaticamente se `price_amount` e `area_useful_m2` existirem.
- Partner normalizers específicos por site com dispatcher pattern.

### Gestão de Jobs (`app/services/scraper_service.py`)

- `run_scrape_job(job_id)` — entry point chamado por `BackgroundTasks`.
- `recover_stale_jobs()` — chamado no startup e antes de criar novos jobs.
- Cancellation cooperativa: verificação de `cancel_requested_at` no loop de páginas.
- SSE stream em `/api/v1/jobs/{id}/stream` com eventos: `progress`, `status`, `heartbeat` (15s), `done`, `error`.
- Erros internos de extração registados via `job.add_log("error", message, url)` — nunca lançar exceção que quebre o job inteiro.

### HTML Cache

```python
# app/crawler/html_cache.py — LRU com TTL 300s, maxsize 100
await html_cache.get_or_set(url, fetch_fn)
```

Usar sempre para evitar re-downloads dentro do mesmo processo.

### Configuração de Sites (`SiteConfig`)

| Campo | Valores |
|---|---|
| `extraction_mode` | `"direct"` ou `"section"` |
| `pagination_type` | `"html_next"`, `"query_param"`, `"incremental_path"` |
| `pagination_param` | ex: `"page"` (para `query_param`) |
| `selectors` | JSON com seletores CSS por campo |
| `link_pattern` | Regex para filtrar URLs de anúncios |
| `image_filter` | Regex para filtrar URLs de imagens |
| `confidence_scores` | Dict de confiança por campo (0.0–1.0) |

---

## 6. Testes

### Fixtures principais (`tests/conftest.py`)

```python
# DB de teste: SQLite in-memory (aiosqlite)
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    # Cria tabelas, yield sessão, drop tabelas no final

@pytest_asyncio.fixture(scope="function")
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    # Override get_db com sessão de teste
    # Headers incluem X-API-Key automático
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": settings.api_key or "123"},
    ) as ac:
        yield ac
```

### Convenções de teste

- `asyncio_mode = "auto"` em `pyproject.toml` — não adicionar `@pytest.mark.asyncio`.
- `scope="function"` em todas as fixtures de DB — isolamento total entre testes.
- `app.dependency_overrides[get_db]` para injetar sessão de teste.
- Asserts sobre `response.status_code` e `response.json()["data"]`.
- Criar dados de teste via API (POST), não inserindo direto na DB.

```bash
pytest                          # Todos os testes
pytest tests/test_api/          # Só integração
pytest tests/test_services/     # Só unitários
pytest -v --tb=short            # Verbose com traceback curto
```

---

## 7. Convenções Específicas

### Nomenclatura

| Contexto | Convenção | Exemplo |
|---|---|---|
| Ficheiros Python | `snake_case` | `scraper_service.py` |
| Classes | `PascalCase` | `EthicalScraper`, `ScrapeJob` |
| Funções/variáveis | `snake_case` | `run_scrape_job`, `site_key` |
| Constantes de módulo | `_UPPER_SNAKE` (prefixo `_`) | `_SUMMARY_FIELD_MAP`, `_W` |
| Endpoints URL | `kebab-case` para multi-palavra | `/selector-suggestions` |
| Chaves de site | `snake_case` curto | `"pearls"`, `"habinedita"` |
| Campos de área | sufixo `_m2` | `area_useful_m2`, `area_land_m2` |

### Hierarquia de responsabilidades — nunca misturar camadas

| Camada | Responsabilidade |
|---|---|
| `api/v1/*.py` | Receber request, validar query params, chamar service, devolver `ok()` |
| `services/` | Lógica de negócio, orquestração, lançar exceções de domínio |
| `models/` | Definição ORM, sem lógica de negócio |
| `schemas/` | Validação de entrada/saída, sem acesso à DB |
| `crawler/` | Scraping, parsing, cache — sem dependência de FastAPI |

### Padrões recorrentes

**Envelope de resposta uniforme:**
```python
# Sempre — nunca devolver dict ou modelo diretamente
return ok(data=result, meta=Meta(page=page, total=total), request=request)
```

**Campos opcionais nos modelos:**
Todos os campos de anúncio são `str | None = None` por defeito. Nunca assumir que um campo existe sem verificar `None`.

**Upsert por `source_url`:**
```python
# SELECT → UPDATE ou INSERT — nunca INSERT OR REPLACE
existing = await db.scalar(select(Listing).where(Listing.source_url == url))
if existing:
    # UPDATE
else:
    # INSERT
```

**Configuração via `settings`:**
```python
# Correto
from app.config import settings
api_key = settings.api_key

# Errado
import os
api_key = os.environ["API_KEY"]
```

**Migrations não destrutivas:**
```python
# Correto — nova coluna sempre nullable ou com default
op.add_column("listings", sa.Column("new_field", sa.String(), nullable=True))

# Errado em produção
op.drop_column("listings", "old_field")
```

---

## 8. Fluxo das Features

### Feature 1 — Configuração de Sites (`/api/v1/sites`)

```
POST /api/v1/sites
  → sites.py (router)
      → cria SiteConfig na DB (site_key, base_url, extraction_mode, seletores)

POST /api/v1/sites/{site_key}/selector-suggestions
  → sites.py (router)
      → selector_suggester.suggest_selectors(url)
          → EthicalScraper.fetch(url) — HTML da página
          → JSON-LD extractor — ground-truth de campos da página
          → candidate_selectors() — gera candidatos CSS para cada campo
          → score_selector() — pontua cada candidato por texto encontrado
          → devolve ranking de seletores por campo (max 3 por campo)
```

**Pontos-chave:**
- `SiteConfig` armazena seletores por campo em `field_selectors: dict[str, str]` (JSON).
- As sugestões não são gravadas automaticamente — o utilizador confirma via `PATCH /api/v1/sites/{site_key}`.
- `extraction_mode`: `"direct"` (seletor por campo) ou `"section"` (pares nome/valor numa tabela HTML).

---

### Feature 2 — Scrape Jobs (`/api/v1/jobs`)

```
POST /api/v1/jobs
  → scrape_jobs.py (router)
      → valida que não existe job RUNNING para o mesmo site_key (JobAlreadyRunningError)
      → cria ScrapeJob com status=PENDING
      → BackgroundTasks.add_task(run_scrape_job, job_id, db)
          → scraper_service.run_scrape_job()
              → ScrapeJob.status = RUNNING
              → EthicalScraper.fetch(url) — HTML por URL
              │   ├── robots.txt check (fail-closed)
              │   ├── delay aleatório (min_delay..max_delay)
              │   └── retry exponencial em 429/5xx
              → parser_service.parse_listing(html, site_config)
              │   ├── modo "direct" — seletor CSS por campo
              │   └── modo "section" — pares .name/.value
              → mapper_service.map_to_property(raw_data, site_key)
              │   └── normaliza texto → tipos Python (Decimal, int, str…)
              → DB upsert por source_url
              │   ├── se existe → UPDATE + cria PriceHistory se preço mudou
              │   └── se não existe → INSERT novo Listing
              → ScrapeJob.update_progress(scraped, failed, total)
              → ScrapeJob.status = COMPLETED | FAILED

GET /api/v1/jobs/{id}/stream          # SSE — progresso em tempo real
  → EventSourceResponse
      → generator lê ScrapeJob da DB a cada 1s
      → emite evento com { status, progress_pct, scraped_count, logs[-5:] }
      → fecha stream quando status = COMPLETED | FAILED | CANCELLED

POST /api/v1/jobs/{id}/cancel
  → ScrapeJob.status = CANCELLING
  → scraper_service verifica flag entre URLs e para o loop

GET /api/v1/jobs                       # lista paginada de jobs
GET /api/v1/jobs/{id}                  # detalhe de job (com logs)
DELETE /api/v1/jobs/{id}              # remove job (apenas COMPLETED/FAILED)
```

**Pontos-chave:**
- O job corre em background — o POST devolve imediatamente com `status=PENDING`.
- Heartbeat atualizado a cada iteração — deteção de jobs stale na startup.
- Logs do job armazenados em `ScrapeJobLog` (tabela separada, eager-loaded).

---

### Feature 3 — Listings (`/api/v1/listings`)

```
GET /api/v1/listings
  → listings.py (router)
      → _apply_filters(stmt, params)
      │   ├── district, property_type, listing_type (filtros exatos)
      │   ├── price_min / price_max (BETWEEN em price_amount)
      │   ├── area_min / area_max (BETWEEN em area_useful_m2)
      │   ├── rooms_min / rooms_max (BETWEEN em rooms)
      │   ├── keyword (ILIKE em title + description)
      │   └── source_partner (filtro por site)
      → COUNT(*) para total
      → SELECT com LIMIT/OFFSET + selectinload(media_assets, price_history)
      → ok(PaginatedResponse(items, total), Meta(page, page_size, …))

GET /api/v1/listings/stats
  → agrega por district / property_type / source_partner
  → devolve contagens + preço médio por grupo

GET /api/v1/listings/{id}
  → SELECT por UUID + selectinload completo
  → NotFoundError se não existe

PATCH /api/v1/listings/{id}
  → merge campos não-None (sem sobrescrever outros campos)
  → se price_amount mudou → cria PriceHistory automaticamente
  → devolve ListingDetailRead

DELETE /api/v1/listings/{id}
  → cascade apaga MediaAsset, PriceHistory, FieldMapping ligados
```

**Pontos-chave:**
- `source_url` é a chave de deduplicação — `UNIQUE` na tabela.
- Filtros são opcionais e combinados com `AND`.
- `PriceHistory` criado automaticamente em cada upsert de scraping OU PATCH com preço diferente.

---

### Feature 4 — AI Enrichment (`/api/v1/enrichment`)

```
POST /api/v1/enrichment/ai/listing/{id}
  → ai_enrichment.py (router)
      → busca Listing por id (NotFoundError se não existe)
      → ai_enrichment_service.enrich_listing(listing, persist=True/False)
          → infer_listing_keywords(listing)   # extrai termos relevantes do título/descrição
          → rate limiter (token bucket por minuto — limite configurável)
          → asyncio.to_thread(_call_ai_for_seo, prompt)
          │   └── google.generativeai.GenerativeModel.generate_content()
          │       └── modelo configurado em settings.gemini_model
          → _normalize_output(raw_text)
          │   ├── extrai JSON do bloco ```json ... ```
          │   └── mapeia campos: title_seo, description_seo, tags, highlights
          → EnrichmentError se falhar parsing ou API indisponível
          → se persist=True → atualiza Listing na DB
      → devolve AiEnrichmentRead (campos enriquecidos sem sobrescrever originais)

GET /api/v1/enrichment/ai/listing/{id}
  → devolve campos SEO já gravados no Listing (title_seo, description_seo, tags)
  → NotFoundError se listing não existe; campos vazios se nunca enriquecido
```

**Pontos-chave:**
- Chamada à API Gemini é **síncrona** — delegada a `asyncio.to_thread()` para não bloquear o event loop.
- Rate limiter protege contra burst — configurado por `settings.gemini_rpm_limit`.
- `persist=False` (query param `save=false`) permite preview sem escrever na DB.
- `EnrichmentError` lançado se a resposta do modelo não tiver o JSON esperado.

---

### Feature 5 — Export (`/api/v1/export`)

```
GET /api/v1/export/csv
GET /api/v1/export/json
GET /api/v1/export/excel
  → export.py (router)
      → _build_export_query(filters)   # mesmos filtros que /listings
      → SELECT com .limit(MAX_EXPORT_ROWS + 1)
      │   └── se count > MAX_EXPORT_ROWS → ExportError (413)
      → _listing_to_dict(listing)      # serializa campos para dict plano
      → formato CSV  → csv.DictWriter → StreamingResponse(media_type="text/csv")
      → formato JSON → json.dumps(rows) → StreamingResponse(media_type="application/json")
      → formato Excel → openpyxl.Workbook → bytes → Response(media_type="application/vnd.openxmlformats…")
```

**Pontos-chave:**
- `MAX_EXPORT_ROWS = 5000` — proteção contra exports imensos acidentais.
- A resposta CSV e JSON usa `StreamingResponse` — não carrega tudo em memória.
- Excel constrói o workbook em memória (openpyxl) — adequado até ao limite de 5000 linhas.
- Requer autenticação (`RequireApiKey`) — endpoint não é público.

---

### Diagrama de dependências entre serviços

```
Router (api/v1/)
  │
  ├─ Depends(get_db)        → AsyncSession  (database.py)
  ├─ Security(RequireApiKey) → verify_api_key (deps.py)
  │
  └─ chama serviços (services/)
       │
       ├─ scraper_service
       │    ├─ ethics_service (EthicalScraper)
       │    │    └─ urllib.robotparser + httpx/requests
       │    ├─ parser_service
       │    │    └─ BeautifulSoup4 + SiteConfig (DB)
       │    └─ mapper_service
       │         └─ normalização de texto (regex, Decimal)
       │
       ├─ ai_enrichment_service
       │    └─ google.generativeai (Gemini API)
       │
       └─ preview_service / selector_suggester
            └─ ethics_service (fetch) + BeautifulSoup4
```