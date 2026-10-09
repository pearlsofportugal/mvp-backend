"""Applying AI translations also fills the columns that were never populated."""
from sqlalchemy import select

from app.models.listing_model import Listing
from app.schemas.ai_enrichment_schema import (
    _ALL_LOCALES,
    BulkEnrichmentRequest,
    ListingTranslationRequest,
    LocaleEnrichmentOutput,
)
from app.services import ai_enrichment_service


async def _listing(db, **kw) -> Listing:
    listing = Listing(source_partner="pearls", source_url="https://x.pt/1", title="T2", **kw)
    db.add(listing)
    await db.commit()
    return listing


def _apply(listing, **values) -> ListingTranslationRequest:
    return ListingTranslationRequest(
        listing_id=listing.id,
        locales=list(values),
        apply=True,
        translation_values={k: LocaleEnrichmentOutput(**v) for k, v in values.items()},
    )


async def test_apply_fills_meta_description_and_quality_score(db_session):
    listing = await _listing(
        db_session,
        raw_description="Apartamento T2 com 90 m² e duas casas de banho. Construído em 2010. Cozinha equipada.",
    )

    await ai_enrichment_service.enrich_translations_and_persist(
        db_session,
        listing.id,
        _apply(
            listing,
            en={"title": "EN", "description": "d", "meta_description": "EN meta"},
            de={"title": "DE", "description": "d", "meta_description": "DE meta"},
            pt={"title": "PT", "description": "d", "meta_description": "PT meta"},
        ),
    )

    db_session.expire_all()
    row = (await db_session.execute(select(Listing))).scalar_one()
    assert set(row.enriched_translations) == {"en", "de", "pt"}
    assert row.meta_description == "PT meta"           # Portuguese preferred (the scraped language)
    assert row.description_quality_score is not None and row.description_quality_score > 0


async def test_apply_never_replaces_existing_values(db_session):
    listing = await _listing(
        db_session, raw_description="Texto.", meta_description="Scraped meta", description_quality_score=77)

    await ai_enrichment_service.enrich_translations_and_persist(
        db_session, listing.id, _apply(listing, en={"title": "EN", "meta_description": "EN meta"}))

    db_session.expire_all()
    row = (await db_session.execute(select(Listing))).scalar_one()
    assert (row.meta_description, row.description_quality_score) == ("Scraped meta", 77)


async def test_meta_falls_back_to_english_when_no_portuguese(db_session):
    listing = await _listing(db_session, raw_description="Texto.")
    await ai_enrichment_service.enrich_translations_and_persist(
        db_session, listing.id, _apply(listing, en={"title": "EN", "meta_description": "EN meta"}))
    db_session.expire_all()
    assert (await db_session.execute(select(Listing.meta_description))).scalar_one() == "EN meta"


def test_default_locales_include_english_and_german():
    assert {"en", "de"} <= set(_ALL_LOCALES)
    assert {"en", "de"} <= set(BulkEnrichmentRequest().locales)


def test_prompt_uses_the_cleaned_description():
    listing = Listing(source_partner="p", source_url="u", title="T2",
                      raw_description="Casa. Nota para agentes imobiliários: segredo.",
                      description="Casa. Nota para agentes imobiliários: segredo.",
                      description_clean="Casa.")
    prompt = ai_enrichment_service._build_multilang_prompt(listing, ["T2"], ["en"])
    assert "Casa." in prompt and "segredo" not in prompt
