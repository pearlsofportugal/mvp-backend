"""Dashboard service — partner-level aggregate statistics."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.imodigi_export_model import ImodigiExport
from app.models.listing_model import Listing
from app.models.scrape_job_model import ScrapeJob
from app.models.site_config_model import SiteConfig
from app.schemas.dashboard_schema import PartnerStats, PartnerStatsResponse, WeeklyStats, WeeklyStatsResponse
from app.schemas.site_config_schema import SiteIdentity


class DashboardService:

    @staticmethod
    async def get_partner_stats(db: AsyncSession) -> PartnerStatsResponse:
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

        # ── CTE: job mais recente por site_key ────────────────────────────
        latest_job_cte = (
            select(
                ScrapeJob.site_key,
                ScrapeJob.id.label("last_job_id"),
                ScrapeJob.status.label("last_job_status"),
                ScrapeJob.created_at.label("last_job_at"),
                ScrapeJob.progress.label("last_job_progress"),
                func.row_number()
                    .over(
                        partition_by=ScrapeJob.site_key,
                        order_by=ScrapeJob.created_at.desc(),
                    )
                    .label("rn"),
            )
        ).cte("latest_job")

        # ── Query principal ───────────────────────────────────────────────
        stmt = (
    select(
        SiteConfig,
        func.count(Listing.id).label("total_listings"),
        func.count(Listing.id)
            .filter(Listing.updated_at >= seven_days_ago)
            .label("listings_updated_last_7_days"),
        func.round(func.avg(Listing.price_amount), 2).label("avg_price"),
        func.round(func.min(Listing.price_amount), 2).label("min_price"),
        func.round(func.max(Listing.price_amount), 2).label("max_price"),
        func.count(Listing.id)
            .filter(Listing.enriched_translations.isnot(None))
            .label("enriched_count"),
        func.max(Listing.updated_at).label("last_listing_updated_at"),
        latest_job_cte.c.last_job_id,
        latest_job_cte.c.last_job_status,
        latest_job_cte.c.last_job_at,
        latest_job_cte.c.last_job_progress,
    )
    .outerjoin(Listing, Listing.source_partner == SiteConfig.key)
    .outerjoin(
        latest_job_cte,
        (latest_job_cte.c.site_key == SiteConfig.key)
        & (latest_job_cte.c.rn == 1),
    )
    .group_by(
        SiteConfig.id,
        latest_job_cte.c.last_job_id,
        latest_job_cte.c.last_job_status,
        latest_job_cte.c.last_job_at,
        latest_job_cte.c.last_job_progress,
    )
)

        rows = (await db.execute(stmt)).all()

        partners = [
            PartnerStats(
                site=SiteIdentity.model_validate(row.SiteConfig),
                total_listings=row.total_listings or 0,
                listings_updated_last_7_days=row.listings_updated_last_7_days or 0,
                avg_price=row.avg_price,
                min_price=row.min_price,
                max_price=row.max_price,
                enriched_count=row.enriched_count or 0,
                exported_to_imodigi_count=0,  # sem modelo ainda
                last_listing_updated_at=row.last_listing_updated_at,
                last_job_id=str(row.last_job_id) if row.last_job_id else None,
                last_job_status=row.last_job_status,
                last_job_at=row.last_job_at,
                # listings_scraped vive dentro do JSON progress — extrair em Python
                last_job_scraped_count=(
                    (row.last_job_progress or {}).get("listings_scraped")
                    if row.last_job_progress else None
                ),
            )
            for row in rows
        ]

        return PartnerStatsResponse(partners=partners, total_partners=len(partners))
    @staticmethod
    async def get_weekly_stats(db: AsyncSession) -> WeeklyStatsResponse:
        """
        Calcula o histórico de crescimento de imóveis agrupado cronologicamente
        pelas últimas 6 semanas para alimentar o gráfico do dashboard.
        """
        now = datetime.now(timezone.utc)
        
        # 1. Gerar os limites das 6 semanas (da mais antiga para a mais recente)
        # Cada semana termina num ponto e recua 7 dias.
        weeks_bounds = []
        for i in reversed(range(6)):
            end_date = now - timedelta(weeks=i)
            start_date = end_date - timedelta(days=7)
            # Guardamos uma label simples (ex: "Semana 1", "Semana 2")
            # Dica: Podes alterar o formato da label para "De DD/MM a DD/MM" usando strftime se preferires
            label = f"Semana {6 - i}"
            weeks_bounds.append((label, start_date, end_date))

        # 2. Construir uma query única eficiente usando CASE condicionais.
        # Isto evita fazer 6 queries separadas à base de dados.
        select_expressions = []
        for label, start, end in weeks_bounds:
            # COUNT para capturados especificamente dentro desta janela de 7 dias
            select_expressions.append(
                func.count(
                    case((and_(Listing.created_at >= start, Listing.created_at <= end), 1))
                ).label(f"captured_{label.replace(' ', '_').lower()}")
            )
            # COUNT para o total acumulado que já existia na DB ATÉ ao fim desta semana
            select_expressions.append(
                func.count(
                    case((Listing.created_at <= end, 1))
                ).label(f"total_{label.replace(' ', '_').lower()}")
            )

        # Executa a query agregada na tabela de listings
        query_result = await db.execute(select(*select_expressions))
        row = query_result.one()

        # 3. Montar a lista de schemas de resposta do Pydantic
        history: list[WeeklyStats] = []
        for label, _, _ in weeks_bounds:
            key_suffix = label.replace(' ', '_').lower()
            
            captured_count = getattr(row, f"captured_{key_suffix}", 0)
            total_count = getattr(row, f"total_{key_suffix}", 0)
            
            history.append(
                WeeklyStats(
                    label=label,
                    total_listings=total_count,
                    listings_captured=captured_count
                )
            )

        return WeeklyStatsResponse(history=history, total_weeks=len(history))