"""Repository — data-access layer for SiteConfig records."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.site_config_model import SiteConfig


class SiteConfigRepository:

    @staticmethod
    async def get_all(db: AsyncSession, *, include_inactive: bool = False) -> list[SiteConfig]:
        query = select(SiteConfig).order_by(SiteConfig.name)
        if not include_inactive:
            query = query.where(SiteConfig.is_active.is_(True))
        return (await db.execute(query)).scalars().all()

    @staticmethod
    async def get_by_key(db: AsyncSession, key: str) -> SiteConfig | None:
        return (
            await db.execute(select(SiteConfig).where(SiteConfig.key == key))
        ).scalar_one_or_none()

    @staticmethod
    async def create(db: AsyncSession, site: SiteConfig) -> SiteConfig:
        db.add(site)
        await db.commit()
        await db.refresh(site)
        return site

    @staticmethod
    async def save(db: AsyncSession, site: SiteConfig) -> SiteConfig:
        await db.commit()
        await db.refresh(site)
        return site

    @staticmethod
    async def delete(db: AsyncSession, site: SiteConfig) -> None:
        await db.delete(site)
        await db.commit()
