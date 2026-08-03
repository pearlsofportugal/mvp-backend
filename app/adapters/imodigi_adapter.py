"""Imodigi adapter — isolates all httpx calls for the Imodigi CRM API.

Services interact only with ImodigiAdapter; no service imports httpx directly.
"""
import asyncio
import json
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import ImodigiError
from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Perform an HTTP request with retry/backoff on transient failures.

    A single Imodigi timeout or 5xx blip must not fail an export outright when
    the next attempt would likely succeed — the caller only sees the final
    response (or a raised error after every attempt is exhausted).
    """
    last_exc: Exception | None = None
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            response = None

        if response is not None and response.status_code not in _RETRYABLE_STATUS:
            return response

        if attempt < _MAX_RETRIES - 1:
            delay = _RETRY_BACKOFF_SECONDS[attempt]
            reason = f"HTTP {response.status_code}" if response is not None else str(last_exc)
            logger.warning(
                "Imodigi %s %s failed (attempt %d/%d): %s — retrying in %.1fs",
                method, url, attempt + 1, _MAX_RETRIES, reason, delay,
            )
            await asyncio.sleep(delay)

    if response is not None:
        return response
    raise ImodigiError(f"Imodigi request failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def _headers() -> dict[str, str]:
    token = settings.imodigi_api_token
    if not token:
        raise ImodigiError("imodigi_api_token is not configured")
    return {
        "X-API-Token": token,
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
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared AsyncClient, creating it lazily on first use."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP client. Call during application shutdown."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def get_stores(self) -> list[dict[str, Any]]:
        """GET /crm-stores.php — return list of active stores."""
        resp = await _request_with_retry(
            self._get_client(), "GET", f"{self._base_url}/crm-stores.php",
            headers=_headers(),
        )
        body = _raise_if_error(resp)
        return body.get("stores", [])

    async def get_catalog_values(self) -> dict[str, Any]:
        """GET /crm-property-values.php — return allowed catalog values."""
        resp = await _request_with_retry(
            self._get_client(), "GET", f"{self._base_url}/crm-property-values.php",
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

        resp = await _request_with_retry(
            self._get_client(), "GET", f"{self._base_url}/crm-locations.php",
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
        request_body: dict[str, Any] = {"client": client_id, "property": property_payload}
        if images:
            request_body["images"] = images
        if translations:
            request_body["translations"] = translations
        if settings.debug:
            logger.debug("Imodigi CREATE payload:\n%s", json.dumps(request_body, indent=2, default=str))
        resp = await _request_with_retry(
            self._get_client(), "POST", f"{self._base_url}/crm-properties.php",
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
        request_body: dict[str, Any] = {
            "client": client_id,
            "propertyId": imodigi_property_id,
            "property": property_payload,
        }
        if images:
            request_body["images"] = images
        if translations:
            request_body["translations"] = translations
        if settings.debug:
            logger.debug("Imodigi UPDATE payload:\n%s", json.dumps(request_body, indent=2, default=str))
        resp = await _request_with_retry(
            self._get_client(), "PATCH", f"{self._base_url}/crm-properties.php",
            headers=_headers(),
            json=request_body,
        )
        return _raise_if_error(resp)

    async def get_property(self, client_id: int) -> list[dict[str, Any]]:
        resp = await _request_with_retry(
            self._get_client(), "GET", f"{self._base_url}/crm-properties.php",
            headers=_headers(),
            params={"client": client_id},
        )
        body = _raise_if_error(resp)
        return body.get("properties", [])  # extrai a lista, ignora "state" e "total"


# Module-level singleton — services import this directly.
imodigi_adapter = ImodigiAdapter()
