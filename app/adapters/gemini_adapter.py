"""Gemini adapter — isolates all google-genai SDK calls from the rest of the app.

Services interact only with GeminiAdapter; no service imports google.genai directly.
"""
import json
import time
from threading import Lock
from typing import Any

from app.config import settings
from app.core.exceptions import EnrichmentError
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: Any = None
_client_lock = Lock()

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


def _get_client() -> Any:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking
                if not settings.google_genai_api_key:
                    raise EnrichmentError("google_genai_api_key is not configured")
                try:
                    from google import genai  # type: ignore[import]
                    _client = genai.Client(api_key=settings.google_genai_api_key)
                except ImportError as exc:
                    raise EnrichmentError("google-genai dependency is not available", detail=str(exc)) from exc
    return _client


def _extract_json(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.replace("json", "", 1).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            return json.loads(candidate[start : end + 1])
        raise


class GeminiAdapter:
    """Thin wrapper around the Gemini generative AI SDK.

    Provides a single `generate` method so that callers never import google-genai
    directly. Swap the implementation here to change model providers.
    """

    def generate(
        self,
        *,
        system_instruction: str,
        prompt: str,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Call the Gemini API synchronously and return parsed JSON.

        Args:
            system_instruction: The system prompt / persona.
            prompt: The user-facing content prompt.
            temperature: Override the default temperature from settings.

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            EnrichmentError: On API failure or non-JSON response.
        """
        client = _get_client()
        temp = temperature if temperature is not None else settings.google_genai_temperature

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model=settings.google_genai_model,
                    config={
                        "system_instruction": system_instruction,
                        "temperature": temp,
                        "response_mime_type": "application/json",
                    },
                    contents=prompt,
                )
                # A malformed/non-JSON response is also worth retrying — the
                # model is non-deterministic, so a fresh attempt may well
                # produce valid JSON even though nothing else changed.
                return _extract_json(str(response.text))
            except EnrichmentError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BACKOFF_SECONDS[attempt]
                    logger.warning(
                        "Gemini generate_content failed (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)

        logger.exception("Gemini generate_content failed after %d attempts", _MAX_RETRIES, exc_info=last_exc)
        raise EnrichmentError("Failed to generate AI output", detail=str(last_exc)) from last_exc


# Module-level singleton — services import this directly.
gemini_adapter = GeminiAdapter()
