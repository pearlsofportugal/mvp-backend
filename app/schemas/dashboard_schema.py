"""Pydantic schemas for GET /api/v1/dashboard/partners."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.site_config_schema import SiteIdentity

class PartnerStats(BaseModel):
    """Estatísticas agregadas de um partner, com o site associado embutido."""

    # Relação explícita — substitui source_partner: str solto
    site: SiteIdentity = Field(
        ...,
        description="Site config associado a este partner (join por site.key = source_partner).",
    )

    # Volume
    total_listings: int = Field(0, ge=0, description="Total de imóveis actualmente na DB.")
    listings_updated_last_7_days: int = Field(0, ge=0, description="Imóveis actualizados nos últimos 7 dias.")

    # Pricing
    avg_price: Decimal | None = Field(None, ge=0, decimal_places=2)
    min_price: Decimal | None = Field(None, ge=0, decimal_places=2)
    max_price: Decimal | None = Field(None, ge=0, decimal_places=2)

    # Enrichment / export
    enriched_count: int = Field(0, ge=0, description="Imóveis com enriquecimento AI.")
    exported_to_imodigi_count: int = Field(0, ge=0, description="Imóveis exportados para Imodigi.")

    # Timestamps
    last_listing_updated_at: datetime | None = Field(
        None,
        description="updated_at mais recente entre todos os imóveis deste partner.",
    )

    # Último scrape job
    last_job_id: str | None = Field(None, description="UUID do job de scraping mais recente.")
    last_job_status: str | None = Field(None, description="Status do job mais recente.")
    last_job_at: datetime | None = Field(None, description="created_at do job mais recente.")
    last_job_scraped_count: int | None = Field(None, ge=0, description="Imóveis scraped no último job.")


class PartnerStatsResponse(BaseModel):
    """Resposta para GET /api/v1/dashboard/partners."""

    partners: list[PartnerStats] = Field(default_factory=list)
    total_partners: int = Field(0, ge=0)


class WeeklyStats(BaseModel):
    """Estatísticas consolidadas para uma determinada semana."""

    label: str = Field(..., description="Nome de exibição (ex: '04/05 - 11/05').")
    total_listings: int = Field(0, ge=0, description="Volume acumulado até ao fim dessa semana.")
    listings_captured: int = Field(0, ge=0, description="Novos imóveis capturados nessa semana.")


class WeeklyStatsResponse(BaseModel):
    """Resposta para GET /api/v1/dashboard/weekly-stats."""

    history: list[WeeklyStats] = Field(default_factory=list)
    total_weeks: int = Field(0, ge=0)