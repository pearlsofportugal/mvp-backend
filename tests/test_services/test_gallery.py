"""Full gallery: every real photo, in order, with sizes when the source states them."""
from uuid import uuid4

from sqlalchemy import select

from app.models.listing_model import Listing
from app.models.media_model import MediaAsset
from app.schemas.property_schema import MediaAsset as MediaSchema, PropertySchema
from app.services import scraper_service
from app.services.mapper_service import _build_gallery
from app.services.parser_service import parse_listing_page
from app.utils.images import dimensions_from_url, largest_srcset_candidate


def test_gallery_keeps_every_photo_in_order_with_positions():
    urls = [f"https://cdn.pt/p{i}.jpg" for i in range(1, 6)]
    gallery = _build_gallery(urls, [f"alt{i}" for i in range(5)])
    assert [m.url for m in gallery] == urls
    assert [m.position for m in gallery] == [0, 1, 2, 3, 4]
    assert gallery[2].alt_text == "alt2"


def test_gallery_drops_chrome_and_duplicates_and_leads_with_a_photo():
    gallery = _build_gallery(
        ["https://x.pt/logo.png", "https://x.pt/a.png", "https://x.pt/a.jpg", "https://x.pt/a.jpg", "https://x.pt/b.jpg"],
        None,
    )
    assert [m.url for m in gallery] == ["https://x.pt/a.jpg", "https://x.pt/a.png", "https://x.pt/b.jpg"]
    assert [m.position for m in gallery] == [0, 1, 2]


def test_gallery_with_only_chrome_is_empty():
    assert _build_gallery(["https://x.pt/logo.svg", "https://x.pt/favicon.ico"], None) == []


def test_dimensions_come_from_declared_sizes_then_rendition_suffix():
    gallery = _build_gallery(
        ["https://x.pt/a-1024x768.jpg", "https://x.pt/b.jpg", "https://x.pt/c.jpg"],
        None,
        {"https://x.pt/b.jpg": (1600, 1200)},
    )
    assert (gallery[0].width, gallery[0].height) == (1024, 768)
    assert (gallery[1].width, gallery[1].height) == (1600, 1200)
    assert (gallery[2].width, gallery[2].height) == (None, None)


def test_dimension_helpers():
    assert dimensions_from_url("https://x.pt/uploads/a-300x200.webp?v=1") == (300, 200)
    assert dimensions_from_url("https://x.pt/a-5x5.jpg") is None          # too small to be a photo
    assert dimensions_from_url("https://img.egorealestate.com/Z1280x960/a.jpg") is None  # bounding box only
    assert largest_srcset_candidate("a.jpg 480w, b.jpg 1280w, c.jpg 800w") == ("b.jpg", 1280)
    assert largest_srcset_candidate("a.jpg 1x, b.jpg 2x") == ("b.jpg", None)


def test_parser_collects_srcset_widest_and_og_image_size():
    html = """<html><head>
      <meta property="og:image" content="https://x.pt/cover.jpg">
      <meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
    </head><body>
      <picture><source srcset="https://x.pt/s-480.jpg 480w, https://x.pt/s-1600.jpg 1600w"></picture>
      <img src="https://x.pt/cover.jpg"><img src="https://x.pt/second.jpg" alt="Sala">
    </body></html>"""
    raw = parse_listing_page(html, "https://x.pt/i/1", {"image_selector": "img, source"})
    assert raw["images"] == ["https://x.pt/s-1600.jpg", "https://x.pt/cover.jpg", "https://x.pt/second.jpg"]
    assert raw["image_sizes"]["https://x.pt/s-1600.jpg"] == (1600, None)
    assert raw["image_sizes"]["https://x.pt/cover.jpg"] == (1200, 630)


def _schema(media) -> PropertySchema:
    return PropertySchema(source_partner="pearls", source_url="https://x.pt/1", title="T2", media=media)


async def test_replace_media_stores_sizes_and_skips_identical_gallery(db_session):
    listing = Listing(source_partner="pearls", source_url="https://x.pt/1", title="T2")
    db_session.add(listing)
    await db_session.commit()

    media = [
        MediaSchema(url="https://x.pt/a.jpg", type="photo", position=0, width=1600, height=1200),
        MediaSchema(url="https://x.pt/b.jpg", type="photo", position=1),
    ]
    await scraper_service._replace_media_assets(db_session, listing.id, _schema(media))
    await db_session.commit()
    first = (await db_session.execute(select(MediaAsset).order_by(MediaAsset.position))).scalars().all()
    assert [(m.url, m.position, m.width, m.height) for m in first] == [
        ("https://x.pt/a.jpg", 0, 1600, 1200), ("https://x.pt/b.jpg", 1, None, None)]
    ids = [m.id for m in first]

    await scraper_service._replace_media_assets(db_session, listing.id, _schema(media))
    await db_session.commit()
    again = (await db_session.execute(select(MediaAsset).order_by(MediaAsset.position))).scalars().all()
    assert [m.id for m in again] == ids          # identical gallery: rows untouched

    await scraper_service._replace_media_assets(db_session, listing.id, _schema(media[:1]))
    await db_session.commit()
    assert len((await db_session.execute(select(MediaAsset))).scalars().all()) == 1


async def test_detail_returns_all_photos_in_position_order(client, db_session):
    listing = Listing(source_partner="pearls", source_url="https://x.pt/1", title="T2")
    db_session.add(listing)
    await db_session.flush()
    for pos, name in [(2, "c"), (0, "a"), (1, "b")]:
        db_session.add(MediaAsset(listing_id=listing.id, url=f"https://x.pt/{name}.jpg", type="photo", position=pos,
                                  width=800 if name == "a" else None, height=600 if name == "a" else None))
    await db_session.commit()

    body = (await client.get(f"/api/v1/listings/{listing.id}")).json()["data"]
    assert [m["url"].rsplit("/", 1)[1] for m in body["media_assets"]] == ["a.jpg", "b.jpg", "c.jpg"]
    assert [m["position"] for m in body["media_assets"]] == [0, 1, 2]
    assert (body["media_assets"][0]["width"], body["media_assets"][0]["height"]) == (800, 600)
    assert set(body["media_assets"][1]) >= {"id", "url", "alt_text", "type", "position"}  # old fields intact
