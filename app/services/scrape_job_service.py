"""Service — business logic for ScrapeJob lifecycle management."""
import math
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, JobAlreadyRunningError, NotFoundError
from app.models.scrape_job_model import ScrapeJob
from app.repositories.scrape_job_repository import ScrapeJobRepository
from app.repositories.site_config_repository import SiteConfigRepository
from app.schemas.base_schema import Meta
from app.schemas.scrape_job_schema import JobCreate


class ScrapeJobService:

    @staticmethod
    async def create_job(db: AsyncSession, payload: JobCreate) -> ScrapeJob:
        running = await ScrapeJobRepository.get_running(db)
        if running:
            raise JobAlreadyRunningError(
                "A scrape job is already running. Wait for it to finish or cancel it."
            )

        site_config = await SiteConfigRepository.get_by_key(db, payload.site_key)
        if not site_config or not site_config.is_active:
            raise NotFoundError(f"Site config '{payload.site_key}' not found or inactive")

        job = ScrapeJob(
            site_key=payload.site_key,
            base_url=site_config.base_url,
            start_url=payload.start_url,
            max_pages=payload.max_pages,
            status="pending",
            config=payload.config.model_dump() if payload.config else None,
            progress={"pages_visited": 0, "listings_found": 0, "listings_scraped": 0, "errors": 0},
        )
        return await ScrapeJobRepository.create(db, job)

    @staticmethod
    async def get_job(db: AsyncSession, job_id: UUID) -> ScrapeJob:
        job = await ScrapeJobRepository.get_by_id(db, job_id)
        if not job:
            raise NotFoundError(f"Scrape job {job_id} not found")
        return job

    @staticmethod
    async def list_jobs(
        db: AsyncSession,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ScrapeJob], Meta]:
        jobs, total = await ScrapeJobRepository.get_all(db, status, page, page_size)
        pages = math.ceil(total / page_size) if total > 0 else 0
        return jobs, Meta(page=page, page_size=page_size, total=total, pages=pages)

    @staticmethod
    async def cancel_job(db: AsyncSession, job_id: UUID) -> tuple[ScrapeJob, str]:
        job = await ScrapeJobRepository.get_by_id(db, job_id)
        if not job:
            raise NotFoundError(f"Scrape job {job_id} not found")

        if job.status == "pending":
            job.mark_cancelled()
            await ScrapeJobRepository.save(db, job)
            return job, "Job cancelled successfully"

        if job.status == "running":
            if job.cancel_requested_at is None:
                job.request_cancel()
                await ScrapeJobRepository.save(db, job)
                return job, "Job cancellation requested successfully"
            return job, "Job cancellation was already requested"

        raise AppException(f"Job {job_id} cannot be cancelled (status: {job.status})")

    @staticmethod
    async def delete_job(db: AsyncSession, job_id: UUID) -> None:
        job = await ScrapeJobRepository.get_by_id(db, job_id)
        if not job:
            raise NotFoundError(f"Scrape job {job_id} not found")
        if job.status == "running":
            raise AppException("Cannot delete a running job. Cancel it first.")
        await ScrapeJobRepository.delete(db, job)
