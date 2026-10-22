"""GET /api/v2/health: schema version, listing counts per partner and the last run of each site."""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.contract_v2 import SCHEMA_VERSION
from app.models.listing_model import Listing
from app.models.scrape_job_model import ScrapeJob
from app.models.site_config_model import SiteConfig

router = APIRouter()


def _iso(value) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


@router.get("/health", summary="Contract health: counts per partner and last run")
async def contract_health(db: AsyncSession = Depends(get_db)) -> dict:
    counts: dict[str, dict[str, int]] = {}
    rows = (
        await db.execute(select(Listing.source_partner, Listing.status, func.count()).group_by(Listing.source_partner, Listing.status))
    ).all()
    for partner, status, n in rows:
        counts.setdefault(partner, {})[status] = int(n)

    sites = (await db.execute(select(SiteConfig.key, SiteConfig.name))).all()
    recent = (await db.execute(select(ScrapeJob).order_by(ScrapeJob.created_at.desc()).limit(300))).scalars().all()
    last_job: dict[str, ScrapeJob] = {}
    for job in recent:
        last_job.setdefault(job.site_key, job)

    partners = []
    for key, name in sorted(sites):
        job = last_job.get(key)
        partners.append({
            "key": key,
            "name": name,
            "active": counts.get(key, {}).get("active", 0),
            "removed": counts.get(key, {}).get("removed", 0),
            "last_run": None if job is None else {
                "status": job.status,
                "started_at": _iso(job.started_at or job.created_at),
                "completed_at": _iso(job.completed_at),
                "listings_scraped": (job.progress or {}).get("listings_scraped"),
            },
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "total_active": sum(p["active"] for p in partners),
        "partners": partners,
    }
