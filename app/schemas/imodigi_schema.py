"""Pydantic v2 schemas for the Imodigi CRM integration."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────── Catalog / Lookup ───────────────────────────

class ImodigiStoreRead(BaseModel):
    id: int
    name: str


class ImodigiCatalogValues(BaseModel):
    """Allowed values returned by GET /crm-property-values.php."""
    property_type: list[str] = Field(default_factory=list)
    business_type: list[str] = Field(default_factory=list)
    state: list[str] = Field(default_factory=list)
    availability: list[str] = Field(default_factory=list)
    energy_class: list[str] = Field(default_factory=list)
    country: list[str] = Field(default_factory=list)


class ImodigiLocationItem(BaseModel):
    id: int
    name: str
    id_pais: int | None = None
    id_regiao: int | None = None
    id_distrito: int | None = None
    id_concelho: int | None = None

# ✅ Depois
class ImodigiGetProperty(BaseModel):
    reference: str | None = None      # "PG06058", "A/001", "." — sempre string
    property_id: int | None = None
    client_id: int | None = None
# ─────────────────────────── Export record ──────────────────────────────

class ImodigiExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    listing_id: UUID
    imodigi_property_id: int | None = None
    imodigi_reference: str | None = None
    imodigi_client_id: int | None = None
    status: str
    last_error: str | None = None
    last_exported_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ImodigiExportRequest(BaseModel):
    """Request body for POST /api/v1/imodigi/export/{listing_id}."""
    client_id: int | None = Field(
        None,
        description="Imodigi store ID. Overrides the IMODIGI_CLIENT_ID setting when provided.",
    )


class ImodigiBulkExportRequest(BaseModel):
    """Request body for POST /api/v1/imodigi/publish/bulk."""

    listing_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Explicit listing IDs to export. "
            "When empty all listings not yet published (or failed) are exported."
        ),
    )
    client_id: int | None = Field(
        None,
        description="Imodigi store ID. Overrides the IMODIGI_CLIENT_ID setting when provided.",
    )
    source_partner: str | None = Field(
        None,
        description="Only export listings from this source partner.",
    )
    is_enriched: bool | None = Field(
        None,
        description="Only export listings that are enriched (true) or not enriched (false).",
    )
    limit: int = Field(
        50,
        ge=1,
        le=500,
        description="Maximum number of listings to process when listing_ids is empty.",
    )


class ImodigiExportResponse(BaseModel):
    """Returned after a successful export to Imodigi."""
    listing_id: UUID
    imodigi_property_id: int | None = None
    imodigi_reference: str | None = None
    status: str
    action: str = Field(description="'created' or 'updated'")


class ImodigiResetRequest(BaseModel):
    """Request body for POST /api/v1/imodigi/publications/reset."""

    listing_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Listing IDs whose export records should be deleted. "
            "Pass an empty list to reset ALL export records."
        ),
    )


class ImodigiSyncItem(BaseModel):
    """Item de uma das categorias do sync report."""
    listing_id: UUID | None = Field(None, description="ID local do listing")
    imodigi_property_id: int | None = Field(None, description="property_id no Imodigi")
    local_property_id: int | None = Field(None, description="property_id guardado na nossa DB")
    partner_id: str | None = Field(None, description="Referência externa / partner_id comum aos dois lados")
    export_status: str | None = Field(None, description="Status do registo em imodigi_exports (se existir)")
    divergence: str = Field(
        description="only_in_crm | only_in_db | in_both | never_exported"
    )


class ImodigiSyncSummary(BaseModel):
    """Contagens do sync report."""
    total_in_crm: int = Field(description="Total de propriedades no Imodigi CRM")
    total_in_db: int = Field(description="Total de registos published/updated/failed/pending na nossa DB")
    total_in_both: int = Field(description="Sincronizados — existem nos dois lados")
    total_only_in_crm: int = Field(description="Existem no CRM mas sem registo local")
    total_only_in_db: int = Field(description="Exportados mas já não existem no CRM")
    total_never_exported: int = Field(description="Listings locais que nunca foram exportados")
    total_property_id_mismatches: int = Field(description="Em ambos os lados mas property_id diverge")


class ImodigiSyncReport(BaseModel):
    """Resultado completo do sync check entre a DB local e o Imodigi CRM."""
    client_id: int
    checked_at: datetime
    summary: ImodigiSyncSummary

    in_both: list[ImodigiSyncItem] = Field(
        default_factory=list,
        description="partner_id existe nos dois lados — sincronizados",
    )
    only_in_db: list[ImodigiSyncItem] = Field(
        default_factory=list,
        description="Temos export record published/updated mas o partner_id já não existe no CRM",
    )
    only_in_crm: list[ImodigiSyncItem] = Field(
        default_factory=list,
        description="Existem no CRM mas sem export record correspondente na DB",
    )
    never_exported: list[ImodigiSyncItem] = Field(
        default_factory=list,
        description="Listings locais com partner_id que nunca foram exportados para o Imodigi",
    )
    property_id_mismatches: list[ImodigiSyncItem] = Field(
        default_factory=list,
        description="partner_id bate nos dois lados mas o property_id diverge (re-criação no CRM)",
    )