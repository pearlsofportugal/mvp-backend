"""Internal-only endpoints invoked by Cloud Tasks."""
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.config import settings
from app.models.scrape_job_model import ScrapeJob
from app.services.job_dispatcher_service import start_cloud_run_scrape_worker
from app.services.scrape_job_event_service import record_event

router = APIRouter()


class WorkerDispatchRequest(BaseModel):
    job_id: UUID


def _verify_dispatch_token(token: str | None = Header(None, alias="X-Worker-Dispatch-Token")) -> None:
    if not settings.worker_dispatch_token or not token or not secrets.compare_digest(token, settings.worker_dispatch_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid worker dispatch token")


@router.post("/workers/scrape", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(_verify_dispatch_token)])
async def start_scrape_worker(payload: WorkerDispatchRequest, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    job = await db.get(ScrapeJob, payload.job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scrape job not found")
    if job.status != "pending":
        return {"job_id": str(job.id), "status": job.status}

    execution_id = await start_cloud_run_scrape_worker(job.id)
    job.execution_id = execution_id or None
    job.dispatched_at = datetime.now(timezone.utc)
    await record_event(db, job.id, "worker_started", "Cloud Run worker execution started", data={"execution_id": execution_id})
    await db.commit()
    return {"job_id": str(job.id), "execution_id": execution_id}
