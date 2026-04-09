"""Imodigi adapter — isolates all httpx calls for the Imodigi CRM API.

Services interact only with ImodigiAdapter; no service imports httpx directly.
"""
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import ImodigiError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _headers() -> dict[str, str]:
    return {
        "X-API-Token": settings.imodigi_api_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _raise_if_error(response: httpx.Response) -> dict[str, Any]:
    """Parse JSON body; raise ImodigiError on non-success state."""
    try:
        body: dict[str, Any] = response.json()
    except Exception as exc:
        raise ImodigiError(
            f"Imodigi returned non-JSON response (HTTP {response.status_code})"
        ) from exc

    if body.get("state") != "success":
        kind = body.get("kind", "unknown")
        message = body.get("message", str(body))
        raise ImodigiError(f"Imodigi error [{kind}]: {message}")

    return body


class ImodigiAdapter:
    """Thin async wrapper around the Imodigi CRM REST API.

    All httpx usage is contained here. Swap the base URL or auth scheme
    without touching any service or router.
    """

    def __init__(self, base_url: str | None = None, timeout: int = 60) -> None:
        self._base_url = base_url or settings.imodigi_base_url
        self._timeout = timeout

    async def get_stores(self) -> list[dict[str, Any]]:
        """GET /crm-stores.php — return list of active stores."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/crm-stores.php",
                headers=_headers(),
            )
        body = _raise_if_error(resp)
        return body.get("stores", [])

    async def get_catalog_values(self) -> dict[str, Any]:
        """GET /crm-property-values.php — return allowed catalog values."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/crm-property-values.php",
                headers=_headers(),
            )
        body = _raise_if_error(resp)
        return body.get("values", {})

    async def search_locations(
        self,
        level: str,
        *,
        country_id: int | None = None,
        region_id: int | None = None,
        district_id: int | None = None,
        county_id: int | None = None,
        q: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """GET /crm-locations.php — search locations by hierarchical level."""
        params: dict[str, Any] = {"level": level, "limit": min(limit, 100)}
        if country_id is not None:
            params["countryId"] = country_id
        if region_id is not None:
            params["regionId"] = region_id
        if district_id is not None:
            params["districtId"] = district_id
        if county_id is not None:
            params["countyId"] = county_id
        if q:
            params["q"] = q

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._base_url}/crm-locations.php",
                headers=_headers(),
                params=params,
            )
        body = _raise_if_error(resp)
        return body.get("items", [])

    async def create_property(
        self,
        client_id: int,
        property_payload: dict[str, Any],
        *,
        images: list[str] | None = None,
        translations: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /crm-properties.php — create a new property."""
        import json
        request_body: dict[str, Any] = {"client": client_id, "property": property_payload}
        if images:
            request_body["images"] = images
        if translations:
            request_body["translations"] = translations
        logger.info("Imodigi CREATE payload:\n%s", json.dumps(request_body, indent=2, default=str))
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/crm-properties.php",
                headers=_headers(),
                json=request_body,
            )
        return _raise_if_error(resp)

    async def update_property(
        self,
        client_id: int,
        imodigi_property_id: int,
        property_payload: dict[str, Any],
        *,
        images: list[str] | None = None,
        translations: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """PATCH /crm-properties.php — update an existing property."""
        import json
        request_body: dict[str, Any] = {
            "client": client_id,
            "propertyId": imodigi_property_id,
            "property": property_payload,
        }
        if images:
            request_body["images"] = images
        if translations:
            request_body["translations"] = translations
        logger.info("Imodigi UPDATE payload:\n%s", json.dumps(request_body, indent=2, default=str))
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.patch(
                f"{self._base_url}/crm-properties.php",
                headers=_headers(),
                json=request_body,
            )
        return _raise_if_error(resp)


# Module-level singleton — services import this directly.
imodigi_adapter = ImodigiAdapter()
