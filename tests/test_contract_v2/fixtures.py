"""Listing doubles shaped like what each source really stores (ORM-like objects, no database)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
LONG_PT = (
    "Apartamento T2 remodelado, com 90 m2 em zona calma, perto de comércio e transportes. "
    "Cozinha equipada, varanda e lugar de garagem. Vidros duplos e aquecimento central."
)


def media(urls, *, kind="photo", width=None, height=None):
    return [
        SimpleNamespace(id=uuid4(), url=u, type=kind, position=i, width=width, height=height, alt_text=None)
        for i, u in enumerate(urls)
    ]


def make_listing(**overrides):
    base = dict(
        id=uuid4(), partner_id="REF-1", source_partner="realkey", source_url="https://www.realkey.pt/pt-PT/Imovel/1",
        title="Apartamento T2 em Canidelo com garagem", business_type="sale", property_type="Apartamento",
        condition="Usado", typology="T2", typology_extra=None, bedrooms=2, bathrooms=2, floor="3",
        price_amount=245000, price_per_m2=2722, price_on_request=False,
        area_useful_m2=90.0, area_gross_m2=105.0, area_land_m2=None,
        district="Porto", county="Vila Nova de Gaia", parish="Canidelo",
        district_code="13", county_code="1317", parish_code="131704", full_address=None,
        has_garage=True, has_elevator=True, has_balcony=None, has_air_conditioning=None, has_pool=None, has_garden=None,
        energy_certificate="C", construction_year=2008,
        raw_description=LONG_PT, description=LONG_PT, description_clean=LONG_PT, meta_description=None,
        enriched_translations=None, description_quality_score=82,
        status="active", removed_at=None, first_seen_at=NOW - timedelta(days=20), last_seen_at=NOW,
        created_at=NOW - timedelta(days=20), updated_at=NOW - timedelta(days=2), content_hash="a" * 64,
        media_assets=media([f"https://imgs.centralimo.pt/25287/properties/{i:08x}-0000-0000-0000-000000000000.jpg" for i in range(1, 8)]),
        price_history=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def partner_listings():
    """One representative listing per source, with that source's quirks."""
    egos = [f"https://images.egorealestate.com/Z1280x960/OAYES/S5/C1/P1/Tphoto/ID{i:04x}.jpg" for i in range(1, 9)]
    watermark = [f"https://multimedia.hcpro.pt/hcpro/6072/properties/6220355/foto/watermark/{i:04x}.jpg" for i in range(1, 7)]
    ximo = [f"https://mediamysquare.ximo.pt//imoveis/1251026/{i:04x}.jpg" for i in range(1, 9)]
    habinedita = [f"https://mediahabineditagondomar.ximo.pt/IMOVEIS/1102/{i:04x}.jpg" for i in range(1, 9)]
    ego_common = dict(condition="Novo", energy_certificate="A", property_type="Apartamento")
    return {
        "realkey": make_listing(),
        "imoantlantico": make_listing(
            source_partner="imoantlantico", source_url="https://www.imo-atlantico.pt/imovel-venda-moradia-t3-funchal-6222281",
            title="MORADIA T3 EM SANTANA", property_type="Moradia", typology="T3", price_amount=None, price_on_request=True,
            district="Madeira", county="Funchal", parish="Santo António", district_code="31", county_code="3103", parish_code="310305",
            energy_certificate="Exempted", condition="Em uso", media_assets=media(watermark),
        ),
        "habinedita": make_listing(
            source_partner="habinedita", source_url="https://www.habinedita.com/imoveis/apartamento-t3-venda_1/pt/",
            typology="T3", property_type="Apartamento", media_assets=media(habinedita), energy_certificate="B-",
        ),
        "mysquare": make_listing(
            source_partner="mysquare", source_url="https://www.mysquare.pt/pt/imoveis/apartamento_1251026/",
            media_assets=media(ximo), condition="Em construção", energy_certificate=None, construction_year=None,
        ),
        **{
            key: make_listing(
                source_partner=key, source_url=f"https://www.{key}.pt/imovel/casa-1/{n}", media_assets=media(egos),
                typology="T1", typology_extra="+1", **ego_common,
            )
            for n, key in enumerate(
                ["barcelcasa", "brightmangroup_pt", "casa10_pt", "entreparedes", "escolhacerta", "habita", "imobiliariaprp",
                 "sottomayor", "sunpoint", "t2mais1"], start=1,
            )
        },
    }


PARTNER = {
    "key": "realkey", "name": "Realkey", "legal_name": None, "ami_licence": None, "logo_url": None,
    "website": "https://www.realkey.pt", "phone": None, "email": None, "address": None,
}
