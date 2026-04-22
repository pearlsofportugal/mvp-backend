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
