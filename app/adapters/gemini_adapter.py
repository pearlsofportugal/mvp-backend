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
from app.models.ai_usage_model import STATUS_ERROR, STATUS_OK
from app.services import ai_usage_service

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


def _token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_from(response: Any) -> tuple[int | None, int | None, int | None]:
    """(prompt, output, total) tokens from a response's usage_metadata; None for what is missing.

    Thinking tokens are billed as output, so they are added to the candidate tokens.
    """
    meta = getattr(response, "usage_metadata", None)
    prompt = _token_count(getattr(meta, "prompt_token_count", None))
    candidates = _token_count(getattr(meta, "candidates_token_count", None))
    thoughts = _token_count(getattr(meta, "thoughts_token_count", None))
    output = None if candidates is None and thoughts is None else (candidates or 0) + (thoughts or 0)
    total = _token_count(getattr(meta, "total_token_count", None))
    return prompt, output, total


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
        operation: str = "unknown",
        listing_id: Any = None,
        source_partner: str | None = None,
    ) -> dict[str, Any]:
        """Call the Gemini API synchronously and return parsed JSON.

        Every call is checked against the daily budget first and logged afterwards (tokens, cost,
        status) — see ``ai_usage_service``.

        Args:
            system_instruction: The system prompt / persona.
            prompt: The user-facing content prompt.
            temperature: Override the default temperature from settings.
            operation / listing_id / source_partner: Context stored with the usage row.

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            AIBudgetExceededError: When today's configured limit has been reached (no API call made).
            EnrichmentError: On API failure or non-JSON response.
        """
        client = _get_client()
        ai_usage_service.check_budget()
        temp = temperature if temperature is not None else settings.google_genai_temperature

        started = time.monotonic()
        attempts = 0
        context = {"operation": operation, "model": settings.google_genai_model,
                   "listing_id": listing_id, "source_partner": source_partner}

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            attempts += 1
            try:
                response = client.models.generate_content(
                    model=settings.google_genai_model,
                    config={
                        "system_instruction": system_instruction,
                        "temperature": temp,
                        "response_mime_type": "application/json",
                        # Fail fast instead of hanging forever — see the
                        # comment on google_genai_timeout_ms in config.py.
                        "http_options": {"timeout": settings.google_genai_timeout_ms},
                    },
                    contents=prompt,
                )
                # A malformed/non-JSON response is also worth retrying — the
                # model is non-deterministic, so a fresh attempt may well
                # produce valid JSON even though nothing else changed.
                result = _extract_json(str(response.text))
                prompt_tokens, output_tokens, total_tokens = _usage_from(response)
                ai_usage_service.record_call(
                    status=STATUS_OK, attempts=attempts, prompt_tokens=prompt_tokens,
                    output_tokens=output_tokens, total_tokens=total_tokens,
                    duration_ms=int((time.monotonic() - started) * 1000), **context,
                )
                return result
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
        ai_usage_service.record_call(
            status=STATUS_ERROR, attempts=attempts, error=str(last_exc),
            duration_ms=int((time.monotonic() - started) * 1000), **context,
        )
        raise EnrichmentError("Failed to generate AI output", detail=str(last_exc)) from last_exc


# Module-level singleton — services import this directly.
gemini_adapter = GeminiAdapter()
