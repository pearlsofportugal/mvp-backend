"""Service — business logic for SiteConfig management."""
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException, DuplicateError, NotFoundError
from app.models.site_config_model import SiteConfig
from app.repositories.site_config_repository import SiteConfigRepository
from app.schemas.site_config_schema import SiteConfigCreate, SiteConfigUpdate


class SiteConfigService:

    @staticmethod
    async def get_all(db: AsyncSession, *, include_inactive: bool = False) -> list[SiteConfig]:
        return await SiteConfigRepository.get_all(db, include_inactive=include_inactive)

    @staticmethod
    async def get_by_key(db: AsyncSession, key: str) -> SiteConfig:
        site = await SiteConfigRepository.get_by_key(db, key)
        if not site:
            raise NotFoundError(f"Site config '{key}' not found")
        return site

    @staticmethod
    async def create(db: AsyncSession, payload: SiteConfigCreate) -> tuple[SiteConfig, str]:
        """Create or reactivate a site config.

        Returns (site, message) where message indicates whether it was created or reactivated.
        """
        existing = await SiteConfigRepository.get_by_key(db, payload.key)
        if existing:
            if existing.is_active:
                raise DuplicateError(f"Site config with key '{payload.key}' already exists")
            for field, value in payload.model_dump().items():
                setattr(existing, field, value)
            existing.is_active = True
            return await SiteConfigRepository.save(db, existing), "Site reactivated successfully"

        site = SiteConfig(**payload.model_dump())
        return await SiteConfigRepository.create(db, site), "Site created successfully"

    @staticmethod
    async def update(db: AsyncSession, key: str, payload: SiteConfigUpdate) -> SiteConfig:
        site = await SiteConfigRepository.get_by_key(db, key)
        if not site:
            raise NotFoundError(f"Site config '{key}' not found")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(site, field, value)
        return await SiteConfigRepository.save(db, site)

    @staticmethod
    async def delete(db: AsyncSession, key: str, *, permanent: bool = False) -> str:
        site = await SiteConfigRepository.get_by_key(db, key)
        if not site:
            raise NotFoundError(f"Site config '{key}' not found")
        if permanent:
            await SiteConfigRepository.delete(db, site)
            return "Site deleted successfully"
        if not site.is_active:
            raise AppException(
                f"Site config '{key}' is already deactivated. Use permanent=true to delete permanently."
            )
        site.is_active = False
        await SiteConfigRepository.save(db, site)
        return "Site deactivated successfully"
