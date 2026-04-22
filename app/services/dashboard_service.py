"""Dashboard service — partner-level aggregate statistics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.imodigi_export_model import ImodigiExport
from app.models.listing_model import Listing
from app.models.scrape_job_model import ScrapeJob
from app.schemas.dashboard_schema import PartnerStats, PartnerStatsResponse


class DashboardService:

    @staticmethod
    async def get_partner_stats(db: AsyncSession) -> PartnerStatsResponse:
        """Aggregate one row of statistics per source_partner in a minimal number of queries."""

        cutoff_7d = datetime.now(timezone.utc) - timedelta(days=7)

        # ── Query 1: per-partner listing aggregates ────────────────────────
        # Computes total, recent count, price stats, enriched count, and imodigi count
        # in a single pass over the listings table.
        imodigi_published = exists(
            select(ImodigiExport.id).where(
                ImodigiExport.listing_id == Listing.id,
                ImodigiExport.status.in_(["published", "updated"]),
            ).correlate(Listing)
        )

        listing_agg = (await db.execute(
            select(
                Listing.source_partner,
                func.count(Listing.id).label("total_listings"),
                func.count(case((Listing.updated_at >= cutoff_7d, 1))).label("recent_count"),
                func.avg(Listing.price_amount).label("avg_price"),
                func.min(Listing.price_amount).label("min_price"),
                func.max(Listing.price_amount).label("max_price"),
                func.max(Listing.updated_at).label("last_updated_at"),
                func.count(case((Listing.enriched_translations.isnot(None), 1))).label("enriched_count"),
                func.count(case((imodigi_published, 1))).label("exported_count"),
            ).group_by(Listing.source_partner)
        )).all()

        if not listing_agg:
            return PartnerStatsResponse(partners=[], total_partners=0)

        partner_keys = [row.source_partner for row in listing_agg]

        # ── Query 2: most recent scrape job per partner ────────────────────
        # Uses a LATERAL / subquery ranked by created_at DESC to avoid N+1.
        latest_job_sub = (
            select(
                ScrapeJob.site_key,
                ScrapeJob.id.label("job_id"),
                ScrapeJob.status.label("job_status"),
                ScrapeJob.created_at.label("job_created_at"),
                ScrapeJob.progress.label("job_progress"),
                func.row_number().over(
                    partition_by=ScrapeJob.site_key,
                    order_by=ScrapeJob.created_at.desc(),
                ).label("rn"),
            ).where(ScrapeJob.site_key.in_(partner_keys))
        ).subquery()

        job_rows = (await db.execute(
            select(
                latest_job_sub.c.site_key,
                latest_job_sub.c.job_id,
                latest_job_sub.c.job_status,
                latest_job_sub.c.job_created_at,
                latest_job_sub.c.job_progress,
            ).where(latest_job_sub.c.rn == 1)
        )).all()

        jobs_by_partner: dict[str, tuple] = {r.site_key: r for r in job_rows}

        # ── Assemble response ──────────────────────────────────────────────
        partners: list[PartnerStats] = []
        for row in listing_agg:
            job = jobs_by_partner.get(row.source_partner)
            scraped_count: int | None = None
            if job and job.job_progress and isinstance(job.job_progress, dict):
                scraped_count = job.job_progress.get("listings_scraped")

            partners.append(PartnerStats(
                source_partner=row.source_partner,
                total_listings=row.total_listings,
                listings_updated_last_7_days=row.recent_count,
                avg_price=float(row.avg_price) if row.avg_price is not None else None,
                min_price=float(row.min_price) if row.min_price is not None else None,
                max_price=float(row.max_price) if row.max_price is not None else None,
                last_listing_updated_at=row.last_updated_at,
                enriched_count=row.enriched_count,
                exported_to_imodigi_count=row.exported_count,
                last_job_id=str(job.job_id) if job else None,
                last_job_status=job.job_status if job else None,
                last_job_at=job.job_created_at if job else None,
                last_job_scraped_count=scraped_count,
            ))

        # Sort by most recently active partner first
        partners.sort(key=lambda p: p.last_listing_updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        return PartnerStatsResponse(partners=partners, total_partners=len(partners))
