"""eGO Real Estate platform extractor — for the /ingest generic path.

eGO Real Estate (egorealestate.com) powers hundreds of Portuguese agency
websites. Every eGO site renders its listing detail page from the same
client-side template, so a single adapter works for *any* eGO domain without a
per-site SiteConfig.

The listing data is injected by JavaScript after the initial HTML, so the raw
page must be rendered (Playwright) before parsing. Detection is done on the
static HTML (which always carries the eGO asset/bundle references).

Output is a raw dict shaped like the other eGO partner parsers, so it feeds
straight into `mapper_service.normalize_ego_platform_payload`.
"""
from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.core.logging import get_logger

logger = get_logger(__name__)

# Markers that only appear on eGO-generated sites (present in the static HTML).
_EGO_MARKERS = (
    "egoforge/websiteeditor",
    "ep.egorealestate.com",
    "websiteapi.egorealestate.com",
    "dataapi.realestates",
)

# eGO "Detalhes do Imóvel" label (lowercased) → raw dict key.
_DETAIL_LABEL_MAP = {
    "distrito": "district",
    "concelho": "county",
    "freguesia": "parish",
    "estado": "condition",
    "referência": "property_id",
    "referencia": "property_id",
    "natureza": "property_type",
    "tipologia": "typology",
    "área útil": "useful_area",
    "area útil": "useful_area",
    "area util": "useful_area",
    "área bruta": "gross_area",
    "area bruta": "gross_area",
    "área do terreno": "land_area",
    "area do terreno": "land_area",
    "ano construção": "construction_year",
    "ano de construção": "construction_year",
    "ano construcao": "construction_year",
}
_BUSINESS_LABELS = {"venda": "sale", "arrendamento": "rent", "trespasse": "trespasse"}
# eGO renders these when the listing has no energy class set — treat as absent.
_ENERGY_NOISE = {"unavailable", "indisponível", "indisponivel", "n/d", "n/a", "-", "--"}

_REF_PREFIX_RE = re.compile(r"^\s*ref\.?\s*", re.IGNORECASE)
_ENERGY_CLASS_RE = re.compile(r"\benergyClass\b\s+([A-G](?:\+{1,3}|-)?)", re.IGNORECASE)
_PHOTO_ID_RE = re.compile(r"/(ID[0-9a-fA-F][0-9a-fA-F-]+)\.")
_PHOTO_PROP_RE = re.compile(r"/P(\d+)/")
_PHOTO_SIZE_RE = re.compile(r"/Z\d+x\d+/")


def looks_like_ego_platform(html: str) -> bool:
    """True when the static HTML is an eGO Real Estate generated site."""
    low = html.lower()
    return "egorealestate" in low and any(m in low for m in _EGO_MARKERS)


def _clean(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _text_or_energy(value_el: Tag | None) -> str:
    """Detail value: plain text, or the letter from `<i class="energyClass E">`."""
    text = _clean(value_el)
    if text:
        return text
    if value_el is not None:
        icon = value_el.select_one("i.energyClass, i[class*='energyClass']")
        if icon is not None:
            m = _ENERGY_CLASS_RE.search(" ".join(icon.get("class", [])))
            if m:
                return m.group(1).upper()
    return ""


def _extract_gallery(soup: BeautifulSoup) -> list[str]:
    """Property photos from `img.Streamed`, deduped, biggest variant per photo.

    Filters out the eGO CRM logo and any thumbnails belonging to a different
    property number (related-listings carousel).
    """
    raw_urls: list[str] = []
    for img in soup.select("img.Streamed, img[data-imgthumb]"):
        src = img.get("data-imgthumb") or img.get("src") or ""
        if src and "images.egorealestate.com" in src and "/Tphoto/" in src:
            raw_urls.append(src)

    if not raw_urls:
        return []

    # Keep photos of the dominant property number only.
    prop_counts = Counter(m.group(1) for u in raw_urls if (m := _PHOTO_PROP_RE.search(u)))
    main_prop = prop_counts.most_common(1)[0][0] if prop_counts else None

    best: dict[str, str] = {}
    for u in raw_urls:
        prop_m = _PHOTO_PROP_RE.search(u)
        if main_prop and prop_m and prop_m.group(1) != main_prop:
            continue
        id_m = _PHOTO_ID_RE.search(u)
        if not id_m:
            continue
        photo_id = id_m.group(1)
        # Prefer a large render; normalise the size segment so we don't keep
        # both a 320x240 and a 1280x960 of the same photo.
        big = _PHOTO_SIZE_RE.sub("/Z1280x960/", u)
        # eGO serves every photo as "<name>.jpg.webp", but the same URL without the
        # trailing ".webp" returns the original JPEG. Prefer that: WebP can't be
        # embedded by common PDF tooling (TCPDF) or older GD builds, so a WebP-only
        # gallery silently disappears from every report built on top of it.
        big = re.sub(r"(\.(?:jpe?g|png))\.webp$", r"\1", big, flags=re.IGNORECASE)
        best.setdefault(photo_id, big)
    return list(best.values())


def extract_ego_platform(html: str, url: str) -> dict:
    """Parse a rendered eGO listing page into a raw dict for the eGO normalizer."""
    soup = BeautifulSoup(html, "lxml")
    raw: dict = {"url": url}

    title_el = soup.select_one("div.propertyTitle h1, h1.propertyTitle, .propertyName h1")
    if title_el:
        raw["title"] = _clean(title_el)

    # "Detalhes do Imóvel" table — scoped to the main property block.
    details = soup.select_one(".propertyDetails")
    detail_items = details.select("li.detailItem") if details else []
    for item in detail_items:
        label = _clean(item.select_one(".label")).lower().rstrip(":").strip()
        if not label:
            continue
        value_el = item.select_one(".value")
        if label in _BUSINESS_LABELS:
            raw.setdefault("business_type", _BUSINESS_LABELS[label])
            price_text = _text_or_energy(value_el)
            if price_text:
                raw.setdefault("price", price_text)
            continue
        key = _DETAIL_LABEL_MAP.get(label)
        if not key:
            continue
        value = _text_or_energy(value_el)
        if not value:
            continue
        if label in ("referência", "referencia"):
            value = _REF_PREFIX_RE.sub("", value).strip()
        if key == "energy_certificate" and value.strip().lower() in _ENERGY_NOISE:
            continue
        raw.setdefault(key, value)

    # Energy certificate — its detail row renders as an icon only.
    if not raw.get("energy_certificate"):
        icon = soup.select_one(".propertyDetails i.energyClass, .propertyDetails i[class*='energyClass']")
        if icon is not None:
            m = _ENERGY_CLASS_RE.search(" ".join(icon.get("class", [])))
            if m:
                raw["energy_certificate"] = m.group(1).upper()

    # Rooms / bathrooms — the summary icon strip (first match = main property).
    if not raw.get("bedrooms"):
        beds = soup.select_one("i.egoiconfont-quartos ~ .value, .wb-fld-rooms .value")
        if beds and _clean(beds):
            raw["bedrooms"] = _clean(beds)
    baths = soup.select_one("i.egoiconfont-chuveiro ~ .value, .wb-fld-bathrooms .value")
    if baths and _clean(baths):
        raw["bathrooms"] = _clean(baths)

    # Price fallback — the price strip, if the "Venda/Arrendamento" row had none.
    if not raw.get("price"):
        price_el = soup.select_one(".wb-fld-price li .value, .wb-fld-price .value, .propertyPrice .value")
        if price_el and _clean(price_el):
            raw["price"] = _clean(price_el)

    # Business type fallback — price strip label ("Venda" / "Arrendamento").
    if not raw.get("business_type"):
        bt_label = soup.select_one(".wb-fld-price li .label, .wb-fld-price .label")
        if bt_label:
            raw["business_type"] = _clean(bt_label).lower()

    # Location string — build from the address parts we scraped; the eGO
    # `.wb-fld-location` value ("Sintra > Freguesia") omits the district.
    loc_parts = [raw.get("district"), raw.get("county"), raw.get("parish")]
    loc = ", ".join(p for p in loc_parts if p)
    if not loc:
        loc_el = soup.select_one(".wb-fld-location, .propertyLocation")
        if loc_el:
            loc = _clean(loc_el)
    if loc:
        raw["location"] = loc

    # Full description.
    desc_el = soup.select_one(".specsText, .propertyDescription .specsText, #description .specsText")
    if desc_el:
        text = desc_el.get_text("\n", strip=True)
        if text:
            raw["raw_description"] = text

    # Advertiser / contact.
    agent_el = soup.select_one(
        ".agentName, .consultantName, .wb-fld-consultant .name, .propertyConsultant .name"
    )
    if agent_el and _clean(agent_el):
        raw["advertiser"] = _clean(agent_el)
    for tel_el in soup.select('a[href^="tel:"]'):
        phone = tel_el.get("href", "").removeprefix("tel:").strip()
        if phone:
            raw["contacts"] = phone
            break

    images = _extract_gallery(soup)
    if images:
        raw["images"] = images
        raw["alt_texts"] = [""] * len(images)

    # SEO block (kept on the payload, same as the other eGO parsers).
    page_title = soup.select_one("title")
    meta_desc = soup.select_one('meta[name="description"]')
    if page_title:
        raw["page_title"] = _clean(page_title)
    if meta_desc and meta_desc.get("content"):
        raw["meta_description"] = meta_desc["content"].strip()

    return raw


def ego_source_partner(url: str) -> str:
    host = (urlparse(url).hostname or "unknown").lower().removeprefix("www.")
    return "ego_" + re.sub(r"[^a-z0-9]+", "_", host).strip("_")
