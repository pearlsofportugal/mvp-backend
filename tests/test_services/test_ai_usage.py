"""AI usage log and daily budget: every Gemini call is logged and a daily limit stops new calls."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import enrich_worker
from app.adapters import gemini_adapter as adapter_module
from app.adapters.gemini_adapter import GeminiAdapter, _usage_from
from app.config import settings
from app.core.exceptions import AIBudgetExceededError, EnrichmentError
from app.models.ai_usage_model import STATUS_ERROR, STATUS_OK, AiUsageLog
from app.services import ai_usage_service as usage

pytestmark = pytest.mark.real_ai_usage


@pytest.fixture
def table():
    """The usage table on the sync engine the adapter uses (sqlite test.db), empty per test."""
    engine = usage._get_engine()
    AiUsageLog.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        session.query(AiUsageLog).delete()
        session.commit()
    yield engine
    with Session(engine) as session:
        session.query(AiUsageLog).delete()
        session.commit()


@pytest.fixture
def limits(monkeypatch):
    """Start every test with no limits and no prices; tests switch on what they need."""
    for name, value in {
        "ai_daily_call_limit": 0, "ai_daily_token_limit": 0, "ai_daily_cost_limit_eur": 0.0,
        "ai_price_input_eur_per_mtok": 0.0, "ai_price_output_eur_per_mtok": 0.0,
    }.items():
        monkeypatch.setattr(settings, name, value)
    return monkeypatch


def _rows(engine) -> list[AiUsageLog]:
    with Session(engine) as session:
        return list(session.scalars(select(AiUsageLog).order_by(AiUsageLog.created_at)))


def _log(n=1, **kw):
    for _ in range(n):
        usage.record_call(operation="seo_copywriting", model="m", **kw)


def _response(text='{"pt": {}}', prompt=100, candidates=50, thoughts=None, total=None):
    response = MagicMock()
    response.text = text
    response.usage_metadata = SimpleNamespace(
        prompt_token_count=prompt, candidates_token_count=candidates,
        thoughts_token_count=thoughts, total_token_count=total,
    )
    return response


class TestRecordCall:
    def test_stores_tokens_context_and_a_total(self, table, limits):
        listing_id = uuid4()
        usage.record_call(operation="seo_copywriting", model="gemini-x", prompt_tokens=100, output_tokens=40,
                          duration_ms=250, listing_id=listing_id, source_partner="mysquare")
        (row,) = _rows(table)
        assert (row.operation, row.model, row.status, row.attempts) == ("seo_copywriting", "gemini-x", STATUS_OK, 1)
        assert (row.prompt_tokens, row.output_tokens, row.total_tokens) == (100, 40, 140)
        assert row.listing_id == listing_id and row.source_partner == "mysquare" and row.duration_ms == 250
        assert row.cost_eur is None  # no prices configured

    def test_cost_comes_from_the_configured_prices(self, table, limits):
        limits.setattr(settings, "ai_price_input_eur_per_mtok", 0.5)
        limits.setattr(settings, "ai_price_output_eur_per_mtok", 3.0)
        _log(prompt_tokens=1_000_000, output_tokens=100_000)
        (row,) = _rows(table)
        assert row.cost_eur == Decimal("0.80")  # 1M*0.5 + 0.1M*3.0

    def test_errors_are_kept_and_truncated(self, table, limits):
        _log(status=STATUS_ERROR, attempts=3, error="x" * 2000)
        (row,) = _rows(table)
        assert row.status == STATUS_ERROR and row.attempts == 3 and len(row.error) == 500

    def test_never_raises_when_the_database_is_unreachable(self, table, limits):
        with patch.object(usage, "_get_engine", side_effect=RuntimeError("db down")):
            usage.record_call(operation="x", model="m")  # must not raise


class TestBudget:
    def test_no_limits_never_blocks_and_does_not_touch_the_database(self, table, limits):
        with patch.object(usage, "_get_engine", side_effect=AssertionError("no query expected")):
            usage.check_budget()

    def test_call_limit_blocks_once_reached(self, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 3)
        _log(2)
        usage.check_budget()  # 2 < 3
        _log(1)
        with pytest.raises(AIBudgetExceededError) as exc:
            usage.check_budget()
        assert "3/3 calls" in str(exc.value)
        assert "rate limit" not in str(exc.value).lower()  # the worker retries anything saying "rate limit"

    def test_retries_count_as_calls(self, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 3)
        _log(attempts=3)  # one logical call, three billed attempts
        with pytest.raises(AIBudgetExceededError):
            usage.check_budget()

    def test_yesterdays_usage_does_not_count(self, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 1)
        with Session(table) as session:
            session.add(AiUsageLog(operation="x", model="m", created_at=usage.start_of_utc_day() - timedelta(seconds=1)))
            session.commit()
        usage.check_budget()

    def test_token_limit(self, table, limits):
        limits.setattr(settings, "ai_daily_token_limit", 1000)
        _log(prompt_tokens=600, output_tokens=300)
        usage.check_budget()
        _log(prompt_tokens=60, output_tokens=40)
        with pytest.raises(AIBudgetExceededError) as exc:
            usage.check_budget()
        assert "tokens" in str(exc.value)

    def test_cost_limit_needs_prices_to_have_any_effect(self, table, limits):
        limits.setattr(settings, "ai_daily_cost_limit_eur", 1.0)
        _log(prompt_tokens=5_000_000, output_tokens=5_000_000)  # no prices: cost unknown
        usage.check_budget()
        limits.setattr(settings, "ai_price_input_eur_per_mtok", 0.2)
        limits.setattr(settings, "ai_price_output_eur_per_mtok", 0.2)
        _log(prompt_tokens=3_000_000, output_tokens=3_000_000)  # EUR 1.20 now on record
        with pytest.raises(AIBudgetExceededError):
            usage.check_budget()

    def test_a_broken_usage_table_does_not_block_the_work(self, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 1)
        with patch.object(usage, "_get_engine", side_effect=RuntimeError("db down")):
            usage.check_budget()  # fails open, logs the problem


class TestAdapter:
    @pytest.fixture(autouse=True)
    def _fast(self, monkeypatch):
        monkeypatch.setattr(adapter_module.time, "sleep", lambda _s: None)

    def _client(self, monkeypatch, *responses):
        client = MagicMock()
        client.models.generate_content.side_effect = list(responses)
        monkeypatch.setattr(adapter_module, "_get_client", lambda: client)
        return client

    def test_a_successful_call_is_logged_with_tokens_and_context(self, table, limits):
        self._client(limits, _response(prompt=120, candidates=40, thoughts=10, total=170))
        listing_id = uuid4()
        result = GeminiAdapter().generate(system_instruction="s", prompt="p", operation="seo_copywriting",
                                          listing_id=listing_id, source_partner="habita")
        assert result == {"pt": {}}
        (row,) = _rows(table)
        assert row.status == STATUS_OK and row.attempts == 1
        assert (row.prompt_tokens, row.output_tokens, row.total_tokens) == (120, 50, 170)  # thinking is output
        assert (row.operation, row.listing_id, row.source_partner) == ("seo_copywriting", listing_id, "habita")
        assert row.duration_ms is not None

    def test_retries_are_one_row_with_the_attempt_count(self, table, limits):
        self._client(limits, RuntimeError("503"), _response())
        GeminiAdapter().generate(system_instruction="s", prompt="p")
        (row,) = _rows(table)
        assert row.status == STATUS_OK and row.attempts == 2

    def test_a_failed_call_is_logged_as_an_error(self, table, limits):
        self._client(limits, RuntimeError("a"), RuntimeError("b"), RuntimeError("boom"))
        with pytest.raises(EnrichmentError):
            GeminiAdapter().generate(system_instruction="s", prompt="p")
        (row,) = _rows(table)
        assert row.status == STATUS_ERROR and row.attempts == 3 and "boom" in row.error
        assert row.total_tokens is None

    def test_over_budget_makes_no_api_call(self, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 1)
        _log(1)
        client = self._client(limits, _response())
        with pytest.raises(AIBudgetExceededError):
            GeminiAdapter().generate(system_instruction="s", prompt="p")
        client.models.generate_content.assert_not_called()
        assert len(_rows(table)) == 1  # nothing logged for a blocked call

    def test_usage_without_metadata_is_stored_as_unknown(self, table, limits):
        bare = MagicMock()
        bare.text = '{"a": 1}'  # MagicMock attributes are not ints: treated as missing
        self._client(limits, bare)
        GeminiAdapter().generate(system_instruction="s", prompt="p")
        (row,) = _rows(table)
        assert (row.prompt_tokens, row.output_tokens, row.total_tokens) == (None, None, None)

    def test_usage_from_adds_thinking_tokens_to_the_output(self):
        assert _usage_from(_response(prompt=10, candidates=5, thoughts=7, total=22)) == (10, 12, 22)
        assert _usage_from(_response(prompt=10, candidates=5, thoughts=None, total=None)) == (10, 5, None)


class TestWorker:
    async def test_enrich_one_reports_budget_without_retrying(self):
        calls = []

        async def fake_enrich(listing, payload):
            calls.append(1)
            raise AIBudgetExceededError("Daily AI budget reached (3/3 calls today, UTC).")

        class _DB:
            async def __aenter__(self):
                return SimpleNamespace()

            async def __aexit__(self, *a):
                return False

        async def fake_get(db, lid):
            return object()

        with patch("app.database.async_session_factory", lambda: _DB()), \
                patch("app.repositories.listings_repository.ListingRepository.get_listing_by_id", fake_get), \
                patch.object(enrich_worker, "enrich_listing_translations", fake_enrich):
            assert await enrich_worker.enrich_one(uuid4(), ["pt"], False) == "budget"
        assert calls == [1]

    async def test_run_stops_when_the_budget_is_hit_and_leaves_the_rest_pending(self, capsys):
        queue = [str(i) for i in range(10)]
        handled = []

        async def fake_pending(db, partners, force, skip, limit):
            return [i for i in queue if i not in skip][:limit]

        async def fake_enrich(lid, locales, force):
            handled.append(lid)
            return "enriched" if int(lid) < 2 else "budget"

        class _S:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *a):
                return False

        with patch("app.database.async_session_factory", lambda: _S()), \
                patch.object(enrich_worker, "pending_ids", fake_pending), \
                patch.object(enrich_worker, "enrich_one", fake_enrich), \
                patch.object(enrich_worker, "CHUNK", 4):
            counts = await enrich_worker.run([], ["pt"], False, concurrency=1, limit=0)

        assert counts == {"enriched": 2, "skipped": 0, "failed": 0}  # the budget stop is not a failure
        assert handled == ["0", "1", "2"]  # 3..9 never started: no further chunks, no more calls
        assert "daily AI budget reached" in capsys.readouterr().out


class TestUsageEndpoint:
    async def test_reports_today_against_the_limits_with_history(self, client, db_session, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 10)
        limits.setattr(settings, "ai_price_input_eur_per_mtok", 1.0)
        now = datetime.now(timezone.utc)
        with Session(table) as session:
            session.add_all([
                AiUsageLog(operation="seo_copywriting", model="m", attempts=2, total_tokens=300, cost_eur=Decimal("0.01"),
                           created_at=usage.start_of_utc_day() + timedelta(seconds=1)),
                AiUsageLog(operation="listing_extract", model="m", attempts=1, total_tokens=100, status=STATUS_ERROR,
                           created_at=usage.start_of_utc_day() + timedelta(seconds=2)),
                AiUsageLog(operation="seo_copywriting", model="m", attempts=5, total_tokens=900,
                           created_at=usage.start_of_utc_day() - timedelta(hours=1)),  # yesterday
            ])
            session.commit()

        response = await client.get("/api/v1/enrichment/ai/usage?days=3")
        assert response.status_code == 200
        data = response.json()["data"]
        assert (data["calls"], data["tokens"]) == (3, 400)
        assert data["limits"] == {"calls": 10, "tokens": None, "cost_eur": None}
        assert data["budget_reached"] is False and data["prices_configured"] is True
        assert data["by_operation"] == {"seo_copywriting": 2, "listing_extract": 1}
        assert [d["calls"] for d in data["history"]] == [5, 3]
        assert data["history"][-1]["errors"] == 1
        assert data["resets_at"] > now.isoformat()[:10]

    async def test_budget_reached_flag(self, client, db_session, table, limits):
        limits.setattr(settings, "ai_daily_call_limit", 2)
        _log(2)
        data = (await client.get("/api/v1/enrichment/ai/usage")).json()["data"]
        assert data["budget_reached"] is True

    async def test_endpoint_rejects_an_invalid_range(self, client, db_session):
        assert (await client.get("/api/v1/enrichment/ai/usage?days=0")).status_code == 422


class TestApiError:
    async def test_budget_error_is_a_429_with_its_own_code(self, client, db_session, monkeypatch):
        async def boom(*args, **kwargs):
            raise AIBudgetExceededError("Daily AI budget reached (3/3 calls today, UTC). It resets at 00:00 UTC.")

        monkeypatch.setattr("app.api.v1.ai_enrichment.enrich_translations_and_persist", boom)
        response = await client.post(
            "/api/v1/enrichment/ai/translations",
            json={"listing_id": str(uuid4()), "locales": ["pt"]},
        )
        assert response.status_code == 429
        assert response.json()["errors"][0]["code"] == "AI_BUDGET_EXCEEDED"


def test_alembic_keeps_a_single_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"alembic has several heads: {heads}"
