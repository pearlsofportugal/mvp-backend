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


def test_prompt_demands_a_155_character_meta_description():
    listing = Listing(source_partner="p", source_url="u", title="T2", description_clean="Casa.")
    prompt = ai_enrichment_service._build_multilang_prompt(listing, ["T2", "Porto"], ["pt", "en"])
    assert "exactly 155 characters" in prompt
    assert "140" not in prompt.split("META DESCRIPTION RULES")[1]
    assert "call to action" in prompt and "primary keyword" in prompt


def test_fit_meta_description_never_exceeds_155():
    fit = ai_enrichment_service.fit_meta_description
    long_text = "Descubra este apartamento T2 luminoso no Porto, com varanda e garagem. " * 6
    assert len(fit(long_text)) <= 155
    no_stops = "palavra " * 40
    assert len(fit(no_stops)) <= 155 and fit(no_stops).endswith(".")
    exact = "x" * 155
    assert fit(exact) == exact


def test_fit_meta_description_keeps_short_text_and_handles_empty():
    fit = ai_enrichment_service.fit_meta_description
    assert fit("  Visite hoje.  ") == "Visite hoje."
    assert fit("") is None and fit(None) is None and fit('  ""  ') is None


def test_parse_locale_output_trims_the_meta_description():
    raw = {"pt": {"title": "T", "description": "D", "meta_description": "Descubra a sua nova casa. " * 20}}
    out = ai_enrichment_service._parse_locale_output(raw, "pt")
    assert len(out.meta_description) <= 155
