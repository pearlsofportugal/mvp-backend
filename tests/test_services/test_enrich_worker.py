"""app/enrich_worker.py (Cloud Run Job entrypoint) and scripts/setup_enrich_worker.py."""
import importlib.util
from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import enrich_worker
from app.core.exceptions import EnrichmentError
from app.models.listing_model import Listing
from app.schemas.ai_enrichment_schema import LocaleEnrichmentOutput

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup_enrich_worker.py"
SPEC = importlib.util.spec_from_file_location("setup_enrich_worker", SCRIPT)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


async def _add(db, partner, n, enriched=False):
    rows = [Listing(source_partner=partner, source_url=f"https://{partner}/{i}/{int(enriched)}", title="T2",
                    enriched_translations={"pt": {"title": "x"}} if enriched else None) for i in range(n)]
    db.add_all(rows)
    await db.commit()
    return [r.id for r in rows]


async def test_pending_ids_only_unenriched_and_filtered_by_partner(db_session):
    todo = await _add(db_session, "a", 2)
    await _add(db_session, "a", 1, enriched=True)
    await _add(db_session, "b", 1)
    assert set(await enrich_worker.pending_ids(db_session, ["a"], False, set(), 10)) == set(todo)
    assert len(await enrich_worker.pending_ids(db_session, [], False, set(), 10)) == 3
    assert len(await enrich_worker.pending_ids(db_session, [], True, set(), 10)) == 4  # --force: enriched ones too


async def test_pending_ids_skips_what_was_already_handled_and_honours_the_limit(db_session):
    ids = await _add(db_session, "a", 3)
    assert len(await enrich_worker.pending_ids(db_session, [], False, {ids[0]}, 10)) == 2
    assert len(await enrich_worker.pending_ids(db_session, [], False, set(), 1)) == 1


class _Session:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


async def test_run_processes_every_pending_listing_once_and_counts_outcomes():
    queue = ["1", "2", "3"]
    outcomes = {"1": "enriched", "2": "failed", "3": "skipped"}

    async def fake_pending(db, partners, force, skip, limit):
        return [i for i in queue if i not in skip][:limit]

    async def fake_enrich(lid, locales, force):
        return outcomes[lid]

    with patch("app.database.async_session_factory", lambda: _Session()), \
            patch.object(enrich_worker, "pending_ids", fake_pending), \
            patch.object(enrich_worker, "enrich_one", fake_enrich):
        counts = await enrich_worker.run([], ["pt"], False, concurrency=2, limit=0)
    assert counts == {"enriched": 1, "skipped": 1, "failed": 1}  # the failed one is not retried forever


async def test_run_stops_at_the_limit():
    async def fake_pending(db, partners, force, skip, limit):
        return [str(len(skip) + i) for i in range(limit)]

    async def fake_enrich(lid, locales, force):
        return "enriched"

    with patch("app.database.async_session_factory", lambda: _Session()), \
            patch.object(enrich_worker, "pending_ids", fake_pending), \
            patch.object(enrich_worker, "enrich_one", fake_enrich):
        counts = await enrich_worker.run([], ["pt"], False, concurrency=3, limit=7)
    assert counts["enriched"] == 7


async def test_enrich_one_retries_when_the_rate_limiter_rejects():
    calls = []

    async def fake_enrich(listing, payload):
        calls.append(payload.apply)
        if len(calls) == 1:
            raise EnrichmentError("AI rate limit exceeded: max 20 requests per 60 seconds.")
        return SimpleNamespace(locales_generated=["pt"], results={"pt": LocaleEnrichmentOutput(title="t")})

    class _DB(_Session):
        async def __aenter__(self):
            return SimpleNamespace(commit=_noop)

    async def _noop():
        return None

    async def fake_get(db, lid):
        return object()

    async def no_sleep(_):
        return None

    with patch("app.database.async_session_factory", lambda: _DB()), \
            patch("app.repositories.listings_repository.ListingRepository.get_listing_by_id", fake_get), \
            patch.object(enrich_worker, "enrich_listing_translations", fake_enrich), \
            patch.object(enrich_worker.asyncio, "sleep", no_sleep):
        assert await enrich_worker.enrich_one(uuid4(), ["pt"], False) == "enriched"
    assert calls == [False, False, True]  # rejected preview, retried preview, then apply


def test_setup_dry_run_prints_the_job_command_and_hides_values(capsys):
    info = {"url": "u", "image": "r/mvp-backend:abc", "sa": "sa@p.iam", "sql": "p:r:db",
            "env": {"DATABASE_URL": "postgresql://u:hunter2@/db"}, "secrets": {"API_KEY": "api-key:latest"}}
    args = SimpleNamespace(project="p", region="r", service="s", job="mvp-enrich-worker")
    with patch.object(setup, "service_info", lambda g, a: info):
        setup.deploy_job(setup.Gcloud(apply=False), args)
        setup.execute_job(setup.Gcloud(apply=False), SimpleNamespace(
            project="p", region="r", job="mvp-enrich-worker", partner="realkey", force=False, limit=0))
    out = capsys.readouterr().out
    assert "python,-m,app.enrich_worker" in out and "--task-timeout 86400" in out
    assert "jobs execute mvp-enrich-worker" in out and "--args --partner,realkey" in out
    assert "hunter2" not in out
