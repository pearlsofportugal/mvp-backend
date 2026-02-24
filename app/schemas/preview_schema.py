"""Schemas for the site preview/test endpoint."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PreviewListingRequest(BaseModel):
    """Request body for POST /api/v1/sites/preview/listing"""
    url: str = Field(..., description="URL da página de detalhe a testar")
    selectors: Dict[str, Any] = Field(..., description="Seletores CSS a testar")
    extraction_mode: str = Field("section", pattern="^(section|direct)$")
    base_url: str = Field(..., description="Base URL do site")
    image_filter: Optional[str] = Field(None, description="Padrão regex para filtrar imagens")


class PreviewListingPageRequest(BaseModel):
    """Request body for POST /api/v1/sites/preview/listing-page"""
    url: str = Field(..., description="URL da página de listagem a testar")
    selectors: Dict[str, Any] = Field(..., description="Seletores CSS a testar")
    base_url: str = Field(..., description="Base URL do site")
    link_pattern: Optional[str] = Field(None, description="Padrão regex para filtrar links")


class FieldPreviewResult(BaseModel):
    """Result for a single extracted field."""
    field: str
    raw_value: Optional[str]
    mapped_to: Optional[str]
    status: str  # "ok" | "empty" | "error"


class PreviewListingResponse(BaseModel):
    """Response for the listing detail preview."""
    url: str
    extraction_mode: str
    fields: List[FieldPreviewResult]
    images_found: int
    raw_data: Dict[str, Any]
    warnings: List[str]


class PreviewListingPageResponse(BaseModel):
    """Response for the listing page (links) preview."""
    url: str
    links_found: int
    sample_links: List[str]
    next_page_url: Optional[str]
    warnings: List[str]