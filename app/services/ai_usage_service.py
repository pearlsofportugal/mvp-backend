"""AI usage log and daily budget.

``check_budget`` and ``record_call`` are called from the (synchronous) Gemini adapter, so they use a
small sync engine on ``DATABASE_URL_SYNC`` instead of the request's async session. That keeps every
caller covered — SEO enrichment, bulk jobs, the enrich worker, the generic-ingest fallback — with a
single hook, and keeps usage rows out of the caller's transaction (a rolled-back enrichment still
leaves its cost on record, which is the point).

Both are best-effort around the database: if it cannot be reached the call is allowed and the
problem is logged. The API needs that same database to store anything the AI returns, so an outage
stops the work anyway; bookkeeping alone must never be what stops it.

The day is a UTC day. Limits of 0 mean "no limit".
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Lock
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AIBudgetExceededError
from app.core.logging import get_logger
from app.models.ai_usage_model import STATUS_ERROR, STATUS_OK, AiUsageLog

logger = get_logger(__name__)

_engine: Engine | None = None
_engine_lock = Lock()

_MILLION = Decimal(1_000_000)
_ERROR_MAX_CHARS = 500


def _get_engine() -> Engine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = settings.database_url_sync_explicit
                kwargs: dict[str, Any] = {"pool_pre_ping": True}
                if not url.startswith("sqlite"):
                    kwargs.update(pool_size=2, max_overflow=2, pool_recycle=1800)
                _engine = create_engine(url, **kwargs)
    return _engine


def start_of_utc_day(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def estimate_cost_eur(prompt_tokens: int | None, output_tokens: int | None) -> Decimal | None:
    """Cost of one call from the configured per-million-token prices; None when none are set."""
    price_in = Decimal(str(settings.ai_price_input_eur_per_mtok))
    price_out = Decimal(str(settings.ai_price_output_eur_per_mtok))
    if price_in <= 0 and price_out <= 0:
        return None
    if prompt_tokens is None and output_tokens is None:
        return None
    return (Decimal(prompt_tokens or 0) * price_in + Decimal(output_tokens or 0) * price_out) / _MILLION


def _limits_enabled() -> bool:
    return bool(
        settings.ai_daily_call_limit > 0
        or settings.ai_daily_token_limit > 0
        or settings.ai_daily_cost_limit_eur > 0
    )


def _totals_since(session: Session, since: datetime) -> tuple[int, int, Decimal]:
    row = session.execute(
        select(
            func.coalesce(func.sum(AiUsageLog.attempts), 0),
            func.coalesce(func.sum(AiUsageLog.total_tokens), 0),
            func.coalesce(func.sum(AiUsageLog.cost_eur), 0),
        ).where(AiUsageLog.created_at >= since)
    ).one()
    return int(row[0]), int(row[1]), Decimal(str(row[2]))


def exceeded_limit(calls: int, tokens: int, cost: Decimal) -> str | None:
    """Human-readable reason when today's usage has reached a limit, else None."""
    if settings.ai_daily_call_limit > 0 and calls >= settings.ai_daily_call_limit:
        return f"{calls}/{settings.ai_daily_call_limit} calls"
    if settings.ai_daily_token_limit > 0 and tokens >= settings.ai_daily_token_limit:
        return f"{tokens}/{settings.ai_daily_token_limit} tokens"
    if settings.ai_daily_cost_limit_eur > 0 and cost >= Decimal(str(settings.ai_daily_cost_limit_eur)):
        return f"EUR {cost:.2f}/{settings.ai_daily_cost_limit_eur:.2f}"
    return None


def check_budget() -> None:
    """Raise AIBudgetExceededError when today's usage already reached a configured daily limit.

    Concurrent callers can overshoot a limit by a few calls (check-then-call is not atomic); the
    limit is a spending brake, not an exact meter.
    """
    if not _limits_enabled():
        return
    try:
        with Session(_get_engine()) as session:
            calls, tokens, cost = _totals_since(session, start_of_utc_day())
    except Exception as exc:  # noqa: BLE001 — see module docstring
        logger.error("AI budget check skipped, usage table unreachable: %s", exc)
        return
    reason = exceeded_limit(calls, tokens, cost)
    if reason:
        raise AIBudgetExceededError(
            f"Daily AI budget reached ({reason} today, UTC). It resets at 00:00 UTC."
        )


def record_call(
    *,
    operation: str,
    model: str,
    status: str = STATUS_OK,
    attempts: int = 1,
    prompt_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
    listing_id: Any = None,
    source_partner: str | None = None,
) -> None:
    """Append one usage row. Never raises: a logging failure must not fail the enrichment."""
    try:
        if total_tokens is None and (prompt_tokens is not None or output_tokens is not None):
            total_tokens = (prompt_tokens or 0) + (output_tokens or 0)
        row = AiUsageLog(
            operation=operation or "unknown",
            model=model,
            status=status,
            attempts=max(1, attempts),
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_eur=estimate_cost_eur(prompt_tokens, output_tokens),
            duration_ms=duration_ms,
            error=error[:_ERROR_MAX_CHARS] if error else None,
            listing_id=listing_id,
            source_partner=source_partner,
        )
        with Session(_get_engine()) as session:
            session.add(row)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not record AI usage: %s", exc)


async def get_usage_summary(db: AsyncSession, days: int = 7) -> dict[str, Any]:
    """Today's totals against the limits, plus a per-day history, for the dashboard."""
    days = max(1, min(days, 90))
    today = start_of_utc_day()
    since = today - timedelta(days=days - 1)

    day = func.date(AiUsageLog.created_at)
    rows = (
        await db.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(AiUsageLog.attempts), 0),
                func.coalesce(func.sum(AiUsageLog.total_tokens), 0),
                func.coalesce(func.sum(AiUsageLog.cost_eur), 0),
                func.count().filter(AiUsageLog.status == STATUS_ERROR),
            )
            .where(AiUsageLog.created_at >= since)
            .group_by(day)
            .order_by(day)
        )
    ).all()
    history = [
        {
            "date": str(r[0]),
            "calls": int(r[1]),
            "tokens": int(r[2]),
            "cost_eur": float(r[3]) if r[3] else None,
            "errors": int(r[4]),
        }
        for r in rows
    ]
    today_row = next((h for h in history if h["date"] == today.date().isoformat()), None)
    calls = today_row["calls"] if today_row else 0
    tokens = today_row["tokens"] if today_row else 0
    cost = Decimal(str(today_row["cost_eur"] or 0)) if today_row else Decimal(0)

    by_operation = (
        await db.execute(
            select(AiUsageLog.operation, func.coalesce(func.sum(AiUsageLog.attempts), 0))
            .where(AiUsageLog.created_at >= today)
            .group_by(AiUsageLog.operation)
        )
    ).all()

    return {
        "date": today.date().isoformat(),
        "calls": calls,
        "tokens": tokens,
        "cost_eur": float(cost) if cost else None,
        "limits": {
            "calls": settings.ai_daily_call_limit or None,
            "tokens": settings.ai_daily_token_limit or None,
            "cost_eur": settings.ai_daily_cost_limit_eur or None,
        },
        "budget_reached": exceeded_limit(calls, tokens, cost) is not None,
        "resets_at": (today + timedelta(days=1)).isoformat(),
        "prices_configured": estimate_cost_eur(1, 1) is not None,
        "by_operation": {op: int(n) for op, n in by_operation},
        "history": history,
    }
