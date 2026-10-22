"""The JSON Schema of the contract, public so the plugin (and its CI) can fetch it without a key."""
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "listing-2.0.schema.json"


@router.get("/schemas/listing/2.0.json", summary="JSON Schema of a v2 listing")
async def listing_schema() -> JSONResponse:
    return JSONResponse(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")), media_type="application/schema+json")
