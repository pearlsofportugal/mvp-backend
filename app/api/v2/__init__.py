"""Contract v2 API (scraper -> Pearls plugin). The JSON shape is defined by app/contracts/listing-2.0.schema.json."""
from fastapi import APIRouter

from app.api.v2 import listings, system, taxonomies
from app.api.v2.schema import router as public_router

router = APIRouter()  # behind X-API-Key
router.include_router(listings.router, prefix="/listings")
router.include_router(taxonomies.router, prefix="/taxonomies")
router.include_router(system.router)

__all__ = ["router", "public_router"]
