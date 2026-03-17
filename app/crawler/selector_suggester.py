"""Selector suggestion and live preview helpers for site configuration."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import settings
from app.core.logging import get_logger
from app.crawler.html_cache import get_cached_html

logger = get_logger(__name__)

_FIELD_ORDER = (
    "price",
    "title",
    "area",
    "land_area",
    "rooms",
    "bathrooms",
    "property_type",
    "typology",
    "condition",
    "business_type",
    "district",
    "county",
    "parish",
    "images",
)
_JSON_LD_SELECTOR = "script[type='application/ld+json']"
_GENERIC_SELECTOR_FALLBACKS = {"div", "span", "strong", "p", "img"}
_ADDRESS_LABELS: dict[str, tuple[str, ...]] = {
    "district": ("distrito", "district", "regiao", "região"),
    "county": ("concelho", "county", "cidade", "municipio", "município"),
    "parish": ("freguesia", "parish", "zona", "localidade", "bairro"),
}
_STRUCTURED_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    **_ADDRESS_LABELS,
    "area": ("area", "área", "área útil", "area util", "área bruta", "area bruta", "gross area", "useful area"),
    "land_area": ("area terreno", "área terreno", "land area", "plot area", "lot area", "terreno"),
    "bathrooms": ("bathrooms", "casas de banho", "casa de banho", "wc", "wcs", "banho", "banhos"),
    "property_type": ("property type", "tipo de imóvel", "tipo imovel", "tipo", "type"),
    "typology": ("typology", "tipologia"),
    "condition": ("estado", "condition", "state"),
    "business_type": ("objectivo", "objetivo", "business type", "tipo de negócio", "tipo negocio", "natureza"),
}
_PROPERTY_KEYWORDS = (
    "moradia",
    "apartamento",
    "terreno",
    "quintinha",
    "quinta",
    "loja",
    "armazem",
    "armazém",
    "escritorio",
    "escritório",
    "garagem",
    "predio",
    "prédio",
    "vivenda",
)
_TITLE_STOPWORDS = {
    "favoritos",
    "consultados",
    "mensagem",
    "politica de privacidade",
    "política de privacidade",
    "centros de resolucao de litigios",
    "centros de resolução de litígios",
    "contactar imobiliaria",
    "contactar imobiliária",
    "agencia",
    "agência",
    "agente",
    "partilhar",
    "partilhar anuncio",
    "partilhar anúncio",
    "contactos",
    "contacte-nos",
    "voltar",
    "pesquisa",
    "pesquisar",
    "pedido de contacto",
}
_LOCATION_STOPWORDS = {
    "referencia do imovel",
    "referência do imóvel",
    "video",
    "vídeo",
    "consultados",
    "favoritos",
    "pedido de contacto",
    "partilhar",
}

_FIELD_SELECTOR_HINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "price": {
        "positive": ("price", "preco", "preço", "valor", "priceeur", "precoeur"),
        "negative": ("quarto", "quartos", "room", "wc", "wcs", "bath", "banho", "area", "m2"),
    },
    "title": {
        "positive": ("title", "titulo", "título", "nome", "imovel", "imovel-titulo", "headline"),
        "negative": ("favorite", "favoritos", "share", "partilha", "contact", "menu", "nav", "breadcrumb", "modal", "privacy", "litigios", "litígios", "card"),
    },
    "area": {
        "positive": ("area", "m2", "metros", "bruta", "util", "útil", "gross", "useful"),
        "negative": ("price", "preco", "preço", "quarto", "wc"),
    },
    "land_area": {
        "positive": ("terreno", "land", "lot", "plot", "parcel", "area-terreno"),
        "negative": ("price", "preco", "quarto", "wc", "util", "bruta"),
    },
    "rooms": {
        "positive": ("quarto", "quartos", "room", "rooms", "tipologia", "typology", "bed", "bedroom"),
        "negative": ("price", "preco", "preço", "valor", "wc", "area", "m2"),
    },
    "bathrooms": {
        "positive": ("bath", "bathroom", "banho", "wc", "wcs", "casa-de-banho"),
        "negative": ("price", "preco", "quarto", "room", "area", "m2"),
    },
    "property_type": {
        "positive": ("tipo", "property", "imovel", "imóvel", "house", "apartment", "land"),
        "negative": ("objectivo", "objetivo", "negocio", "business", "estado", "tipologia"),
    },
    "typology": {
        "positive": ("tipologia", "typology", "quarto", "quartos", "room"),
        "negative": ("price", "preco", "wc", "estado", "objectivo", "objetivo"),
    },
    "condition": {
        "positive": ("estado", "condition", "state", "used", "novo", "usado"),
        "negative": ("price", "preco", "objectivo", "objetivo", "negocio"),
    },
    "business_type": {
        "positive": ("objectivo", "objetivo", "business", "negocio", "negócio", "sale", "rent", "venda", "natureza"),
        "negative": ("price", "preco", "estado", "tipologia", "tipo"),
    },
    "district": {
        "positive": ("district", "distrito", "location", "morada", "breadcrumb"),
        "negative": ("price", "preco", "quarto", "wc", "gallery", "slider"),
    },
    "county": {
        "positive": ("county", "concelho", "cidade", "location", "morada", "breadcrumb"),
        "negative": ("price", "preco", "quarto", "wc", "gallery", "slider"),
    },
    "parish": {
        "positive": ("parish", "freguesia", "zona", "localidade", "bairro", "location"),
        "negative": ("price", "preco", "quarto", "wc", "gallery", "slider"),
    },
    "images": {
        "positive": ("gallery", "galeria", "foto", "image", "imagem", "slider"),
        "negative": ("logo", "icon", "avatar", "map"),
    },
}

_FIELD_HEURISTICS: dict[str, list[str]] = {
    "price": [
        "[class*='price']",
        "[class*='preco']",
        "[class*='valor']",
        "[itemprop='price']",
        ".price",
        "strong.price",
    ],
    "title": [
        "h1",
        "[class*='title']",
        "[class*='titulo']",
        "[class*='nome']",
    ],
    "area": [
        "[class*='area']",
        "[class*='m2']",
        "[class*='metros']",
        "[class*='util']",
        "[class*='bruta']",
    ],
    "land_area": [
        "[class*='terreno']",
        "[class*='land']",
        "[class*='plot']",
        "[class*='lot']",
        "[id*='area_terreno']",
    ],
    "rooms": [
        "[class*='quarto']",
        "[class*='room']",
        "[class*='divisao']",
    ],
    "bathrooms": [
        "[class*='bath']",
        "[class*='banho']",
        "[class*='wc']",
        "[id*='wcs']",
        "[id*='wc']",
    ],
    "property_type": [
        "[class*='tipo']",
        "[class*='type']",
        "[class*='imovel']",
        "[class*='property']",
    ],
    "typology": [
        "[class*='tipologia']",
        "[class*='typology']",
        "[class*='tipo']",
    ],
    "condition": [
        "[class*='estado']",
        "[class*='condition']",
        "[class*='state']",
    ],
    "business_type": [
        "[class*='objectivo']",
        "[class*='objetivo']",
        "[class*='business']",
        "[class*='negocio']",
        "[class*='natureza']",
    ],
    "district": [
        "[class*='district']",
        "[class*='distrito']",
        "[class*='location']",
        "[class*='morada']",
        "address",
    ],
    "county": [
        "[class*='county']",
        "[class*='concelho']",
        "[class*='cidade']",
        "[class*='location']",
        "address",
    ],
    "parish": [
        "[class*='parish']",
        "[class*='freguesia']",
        "[class*='zona']",
        "[class*='localidade']",
        "address",
    ],
    "images": [
        ".gallery img[src]",
        "[class*='slider'] img",
        "[class*='foto'] img",
    ],
}

_FIELD_VALIDATORS = {
    "price": re.compile(r"\d.*(?:€|eur|euro)", re.IGNORECASE),
    "area": re.compile(r"\d+(?:[\.,]\d+)?\s*(?:m2|m²|metros?)", re.IGNORECASE),
    "land_area": re.compile(r"\d+(?:[\.,]\d+)?\s*(?:m2|m²|metros?)", re.IGNORECASE),
    "rooms": re.compile(r"(?:\bT\d\b|\d+\s*(?:quartos?|rooms?|divis[oõ]es?))", re.IGNORECASE),
    "bathrooms": re.compile(r"(?:\d+\s*(?:wcs?|casas?\s+de\s+banho|bathrooms?)|^\d{1,2}$)", re.IGNORECASE),
    "property_type": re.compile(r"\b(?:moradia|apartamento|terreno|loja|armaz[eé]m|escrit[oó]rio|garagem|quintinha|quinta|predio|pr[eé]dio|vivenda)\b", re.IGNORECASE),
    "typology": re.compile(r"\bT\d+(?:\+\d+)?\b", re.IGNORECASE),
    "condition": re.compile(r"\b(?:usado|novo|renovado|recuperado|excelente|bom\s+estado|em\s+construc[aã]o|na\s+planta|para\s+recuperar)\b", re.IGNORECASE),
    "business_type": re.compile(r"\b(?:venda|comprar|arrendar|arrendamento|sale|rent|buy)\b", re.IGNORECASE),
    "district": re.compile(r"[A-Za-zÀ-ÿ]{3,}"),
    "county": re.compile(r"[A-Za-zÀ-ÿ]{3,}"),
    "parish": re.compile(r"[A-Za-zÀ-ÿ]{3,}"),
}


async def fetch_html(url: str) -> str:
    """Fetch raw HTML for a page using the crawler defaults."""
    headers = {"User-Agent": settings.default_user_agent}
    timeout = httpx.Timeout(settings.request_timeout)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def suggest_selectors(url: str) -> dict[str, Any]:
    """Suggest CSS selectors for common listing fields on a given page."""
    empty_result = {"source": "heuristic", "candidates": {field: [] for field in _FIELD_ORDER}}

    try:
        html = await get_cached_html(url, fetch_html)
    except Exception as exc:
        logger.error(
            "Failed to fetch HTML for selector suggestion: %s",
            url,
            extra={"url": url},
            exc_info=exc,
        )
        return empty_result

    soup = BeautifulSoup(html, "lxml")
    reference_values = _extract_json_ld_reference_values(soup)
    candidates = {
        field: _collect_candidates(
            soup=soup,
            field=field,
            expected_values=reference_values.get(field, []),
        )
        for field in _FIELD_ORDER
    }

    source = "json-ld" if any(reference_values.values()) else "heuristic"
    return {"source": source, "candidates": candidates}


async def preview_selector(url: str, selector: str) -> dict[str, Any]:
    """Preview the first extracted values for a CSS selector on a page."""
    try:
        html = await get_cached_html(url, fetch_html)
    except Exception as exc:
        logger.error(
            "Failed to fetch HTML for selector preview: %s",
            url,
            extra={"url": url},
            exc_info=exc,
        )
        return {"matches": 0, "preview": []}

    soup = BeautifulSoup(html, "lxml")
    elements = _safe_select(soup, selector)
    preview = []

    for element in elements[:3]:
        sample = _extract_sample_text(element, "images" if element.name == "img" else "text")
        if sample:
            preview.append(sample)

    return {"matches": len(elements), "preview": preview}


def _collect_candidates(soup: BeautifulSoup, field: str, expected_values: list[str]) -> list[dict[str, Any]]:
    """Collect and rank up to three selector candidates for a field."""
    ranked: dict[str, dict[str, Any]] = {}

    for heuristic_index, heuristic_selector in enumerate(_FIELD_HEURISTICS[field]):
        elements = _safe_select(soup, heuristic_selector)
        for element in elements[:5]:
            sample = _extract_sample_text(element, field)
            if not sample:
                continue

            selector = _build_selector(element, heuristic_selector)
            score = _score_candidate(
                field=field,
                selector=selector,
                sample=sample,
                heuristic_index=heuristic_index,
                expected_values=expected_values,
            )
            if score <= 0:
                continue

            existing = ranked.get(selector)
            candidate = {
                "selector": selector,
                "sample": sample,
                "score": round(min(score, 0.99), 2),
            }
            if existing is None or candidate["score"] > existing["score"]:
                ranked[selector] = candidate

    if field in _STRUCTURED_FIELD_LABELS:
        for candidate in _collect_structured_field_candidates(soup, field, expected_values):
            existing = ranked.get(candidate["selector"])
            if existing is None or candidate["score"] > existing["score"]:
                ranked[candidate["selector"]] = candidate

    return sorted(ranked.values(), key=lambda item: item["score"], reverse=True)[:3]


def _safe_select(soup: BeautifulSoup | Tag, selector: str) -> list[Tag]:
    """Run a CSS selector defensively and never raise for invalid syntax."""
    try:
        return list(soup.select(selector))
    except Exception:
        return []


def _extract_json_ld_reference_values(soup: BeautifulSoup) -> dict[str, list[str]]:
    """Extract field reference values from JSON-LD blocks when present."""
    reference_values = {field: [] for field in _FIELD_ORDER}

    for script in soup.select(_JSON_LD_SELECTOR):
        raw_payload = script.string or script.get_text(strip=True)
        if not raw_payload:
            continue

        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            continue

        for node in _iter_json_ld_nodes(payload):
            field_values = _extract_values_from_json_ld_node(node)
            for field, values in field_values.items():
                if values:
                    reference_values[field].extend(values)

    return {field: _unique_preserving_order(values) for field, values in reference_values.items()}


def _iter_json_ld_nodes(payload: Any) -> list[dict[str, Any]]:
    """Flatten nested JSON-LD payloads into individual object nodes."""
    nodes: list[dict[str, Any]] = []

    if isinstance(payload, dict):
        nodes.append(payload)
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                nodes.extend(_iter_json_ld_nodes(item))
    elif isinstance(payload, list):
        for item in payload:
            nodes.extend(_iter_json_ld_nodes(item))

    return nodes


def _extract_values_from_json_ld_node(node: dict[str, Any]) -> dict[str, list[str]]:
    """Extract comparable field values from a JSON-LD node."""
    type_names = {value.lower() for value in _as_list(node.get("@type")) if isinstance(value, str)}
    supported_types = {
        "realestatelisting",
        "offer",
        "product",
        "singlefamilyresidence",
        "residence",
        "house",
        "apartment",
    }
    if type_names and type_names.isdisjoint(supported_types):
        return {field: [] for field in _FIELD_ORDER}

    title = _first_string(node.get("name"), node.get("headline"), node.get("title"))
    price = _first_string(
        node.get("price"),
        _nested_value(node, "offers", "price"),
    )
    area = _first_string(
        _nested_value(node, "floorSize", "value"),
        node.get("floorSize"),
        _nested_value(node, "floorSize", "name"),
    )
    land_area = _first_string(
        _nested_value(node, "lotSize", "value"),
        node.get("lotSize"),
        _nested_value(node, "lotSize", "name"),
    )
    rooms = _first_string(node.get("numberOfRooms"), node.get("numberOfBedrooms"))
    bathrooms = _first_string(node.get("numberOfBathroomsTotal"), node.get("numberOfBathrooms"))
    property_type = _first_string(node.get("additionalType"), node.get("category"))
    if not property_type:
        property_type = _property_type_from_json_ld_type(type_names)
    typology = _typology_from_strings(title, _first_string(node.get("description")))
    condition = _first_string(node.get("itemCondition"), node.get("condition"))
    business_type = _first_string(node.get("businessFunction"), _nested_value(node, "offers", "businessFunction"))
    district, county, parish = _address_parts(node.get("address"))
    images = [_stringify(value) for value in _as_list(node.get("image")) if _stringify(value)]

    return {
        "price": [price] if price else [],
        "title": [title] if title else [],
        "area": [area] if area else [],
        "land_area": [land_area] if land_area else [],
        "rooms": [rooms] if rooms else [],
        "bathrooms": [bathrooms] if bathrooms else [],
        "property_type": [property_type] if property_type else [],
        "typology": [typology] if typology else [],
        "condition": [condition] if condition else [],
        "business_type": [business_type] if business_type else [],
        "district": [district] if district else [],
        "county": [county] if county else [],
        "parish": [parish] if parish else [],
        "images": images,
    }


def _score_candidate(
    field: str,
    selector: str,
    sample: str,
    heuristic_index: int,
    expected_values: list[str],
) -> float:
    """Assign a confidence score to a selector candidate."""
    base_score = max(0.2, 0.55 - heuristic_index * 0.04)
    quality_score = _field_quality_score(field, sample, selector)
    if quality_score < 0:
        return 0.0

    selector_score = _selector_context_score(field, selector)
    if selector_score < -0.1:
        return 0.0

    specificity_score = 0.08 if any(token in selector for token in (".", "#", "[")) else 0.02
    relation_score = 0.05 if " " in selector else 0.0
    expected_match_score = 0.0

    if expected_values:
        expected_match_score = max(_similarity(sample, expected_value) for expected_value in expected_values) * 0.25

    return base_score + quality_score + selector_score + specificity_score + relation_score + expected_match_score


def _field_quality_score(field: str, sample: str, selector: str) -> float:
    """Score whether the extracted sample resembles the target field."""
    if field == "title":
        normalized = _normalize_text(sample)
        if normalized in _TITLE_STOPWORDS:
            return -0.3
        if any(stopword in normalized for stopword in _TITLE_STOPWORDS):
            return -0.25
        normalized_selector = _normalize_text(selector)
        if "card" in normalized_selector and "h1" not in normalized_selector:
            return -0.16
        if "," in sample and not any(keyword in normalized for keyword in _PROPERTY_KEYWORDS):
            return -0.12
        bonus = 0.06 if normalized_selector.startswith("h1") else 0.0
        return 0.18 + bonus if len(sample) >= 5 and not sample.isnumeric() else 0.0

    if field == "images":
        if any(token in _normalize_text(selector) for token in _FIELD_SELECTOR_HINTS["images"]["negative"]):
            return -0.3
        return 0.22 if sample.startswith(("http://", "https://", "/", "//")) else 0.0

    normalized_sample = _normalize_text(sample)

    if field == "price":
        if re.fullmatch(r"\d{1,5}", normalized_sample):
            return -0.3
        explicit_currency = bool(_FIELD_VALIDATORS["price"].search(sample))
        long_number = bool(re.search(r"\d{2,3}(?:[.\s]\d{3})+(?:[,\.]\d+)?", sample))
        positive_selector = any(token in _normalize_text(selector) for token in _FIELD_SELECTOR_HINTS["price"]["positive"])
        if not explicit_currency and not (positive_selector and long_number):
            return -0.45
        if explicit_currency:
            return 0.22
        return 0.08

    if field in {"area", "land_area"}:
        if not re.search(r"\d", sample):
            return -0.18
        if normalized_sample in {"area", "área", "areas", "áreas", "util", "útil", "bruta"}:
            return -0.2

    if field == "rooms":
        if re.fullmatch(r"\d{1,2}", normalized_sample):
            if any(token in _normalize_text(selector) for token in _FIELD_SELECTOR_HINTS["rooms"]["positive"]):
                return 0.12
            return -0.15

    if field == "bathrooms":
        if any(token in normalized_sample for token in ("wc", "wcs", "banho", "bath")) and re.search(r"\d", sample):
            return -0.08
        if re.fullmatch(r"\d{1,2}", normalized_sample):
            if any(token in _normalize_text(selector) for token in _FIELD_SELECTOR_HINTS["bathrooms"]["positive"]):
                return 0.12
            return -0.15

    if field == "property_type":
        if any(token in normalized_sample for token in _PROPERTY_KEYWORDS):
            if len(sample) > 60 or sample.count(" ") > 8:
                return -0.15
            return 0.18
        if any(token in normalized_sample for token in ("venda", "arrendamento", "usado", "novo", "t0", "t1", "t2", "t3", "t4", "t5")):
            return -0.18

    if field == "typology":
        if _FIELD_VALIDATORS["typology"].search(sample):
            return 0.2
        return -0.15

    if field == "condition":
        if _FIELD_VALIDATORS["condition"].search(sample):
            return 0.18
        return -0.12

    if field == "business_type":
        if _FIELD_VALIDATORS["business_type"].search(sample):
            return 0.18
        return -0.12

    if field in _ADDRESS_LABELS:
        if normalized_sample in _LOCATION_STOPWORDS:
            return -0.4
        if len(sample) < 3 or re.fullmatch(r"\d+(?:[.,]\d+)?", normalized_sample):
            return -0.2
        if len(sample) > 80:
            return -0.25
        if sample.count(" ") > 8:
            return -0.2
        if any(token in normalized_sample for token in _ADDRESS_LABELS[field]):
            return 0.18
        if re.search(r"\b[A-ZÀ-Ý][a-zà-ÿ]+\b", sample) and sample.count(" ") <= 4:
            return 0.12

    pattern = _FIELD_VALIDATORS.get(field)
    if pattern is not None and pattern.search(sample):
        return 0.18

    if field in _ADDRESS_LABELS and len(sample) >= 3:
        return 0.12

    return 0.0


def _selector_context_score(field: str, selector: str) -> float:
    """Score a selector based on field-specific positive and negative hints."""
    hints = _FIELD_SELECTOR_HINTS.get(field)
    if hints is None:
        return 0.0

    normalized_selector = _normalize_text(selector)
    positive_hits = sum(1 for token in hints["positive"] if token in normalized_selector)
    negative_hits = sum(1 for token in hints["negative"] if token in normalized_selector)

    return positive_hits * 0.06 - negative_hits * 0.12


def _collect_structured_field_candidates(
    soup: BeautifulSoup,
    field: str,
    expected_values: list[str],
) -> list[dict[str, Any]]:
    """Extract candidates from generic PT/EN label/value summary blocks."""
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    all_structured_labels = {item for values in _STRUCTURED_FIELD_LABELS.values() for item in values}

    for node in soup.select("li, div, p, tr"):
        if len(node.get_text(" ", strip=True)) > 120:
            continue
        label_text = _find_structured_label(node, field)
        if not label_text:
            continue

        sample, value_element = _extract_structured_field_value(node, label_text, all_structured_labels)
        if not sample:
            continue

        selector = _build_selector(value_element or node, node.name or "div")
        if selector in seen:
            continue
        seen.add(selector)

        score = _score_candidate(
            field=field,
            selector=selector,
            sample=sample,
            heuristic_index=0,
            expected_values=expected_values,
        ) + 0.08
        if score <= 0:
            continue

        candidates.append(
            {
                "selector": selector,
                "sample": sample,
                "score": round(min(score, 0.99), 2),
            }
        )

    return candidates


def _find_structured_label(node: Tag, field: str) -> str | None:
    """Return a recognized label from a structured summary container."""
    allowed_labels = _STRUCTURED_FIELD_LABELS[field]
    for candidate in node.select("b, strong, th, .name, .label, dt, .icon_label"):
        text = _normalize_text(candidate.get_text(" ", strip=True)).rstrip(":")
        if text in allowed_labels:
            return text

    text = _normalize_text(node.get_text(" ", strip=True))
    for label in allowed_labels:
        if text.startswith(label):
            return label
    return None


def _extract_structured_field_value(node: Tag, label_text: str, all_structured_labels: set[str]) -> tuple[str | None, Tag | None]:
    """Extract the value side of a generic label/value row or icon block."""
    for candidate in node.select(".value, .lbl_valor, p.info-item.float-right, .info-item.float-right, .info-item, td, dd"):
        candidate_text = _truncate_whitespace(candidate.get_text(" ", strip=True))
        normalized = _normalize_text(candidate_text)
        if candidate_text and normalized and normalized != label_text and normalized not in all_structured_labels:
            return candidate_text, candidate

    for candidate in node.select("span"):
        if "icon_label" in (candidate.get("class") or []):
            continue
        candidate_text = _truncate_whitespace(candidate.get_text(" ", strip=True))
        normalized = _normalize_text(candidate_text)
        if candidate_text and normalized and normalized != label_text and normalized not in all_structured_labels:
            return candidate_text, candidate

    full_text = _truncate_whitespace(node.get_text(" ", strip=True))
    pattern = rf"{re.escape(label_text)}\s*:?\s*(.+)$"
    match = re.search(pattern, _normalize_text(full_text), re.IGNORECASE)
    if match:
        extracted = match.group(1).strip()
        if extracted:
            return extracted.title(), None

    return None, None


def _property_type_from_json_ld_type(type_names: set[str]) -> str | None:
    """Infer a property type from JSON-LD @type names when explicit labels are absent."""
    mapping = {
        "house": "House",
        "singlefamilyresidence": "House",
        "residence": "Residence",
        "apartment": "Apartment",
    }
    for type_name in type_names:
        if type_name in mapping:
            return mapping[type_name]
    return None


def _typology_from_strings(*values: str | None) -> str | None:
    """Extract a Portuguese typology code from nearby descriptive strings."""
    for value in values:
        if not value:
            continue
        match = re.search(r"\bT\d+(?:\+\d+)?\b", value, re.IGNORECASE)
        if match:
            return match.group(0).upper()
    return None


def _build_selector(element: Tag, fallback_selector: str) -> str:
    """Build a specific CSS selector for the matched element when possible."""
    current_selector = _element_selector(element)
    if current_selector and current_selector not in _GENERIC_SELECTOR_FALLBACKS:
        return current_selector

    parent = element.parent if isinstance(element.parent, Tag) else None
    if parent is not None:
        parent_selector = _element_selector(parent)
        if parent_selector and parent_selector not in _GENERIC_SELECTOR_FALLBACKS:
            return f"{parent_selector} {element.name}"

    return fallback_selector


def _element_selector(element: Tag) -> str:
    """Create a simple CSS selector from an element tag, id, and classes."""
    element_id = _css_token(element.get("id"))
    if element_id:
        return f"#{element_id}"

    selector = element.name or ""
    class_names = [_css_token(class_name) for class_name in element.get("class", [])]
    class_names = [class_name for class_name in class_names if class_name][:2]

    if class_names:
        selector += "".join(f".{class_name}" for class_name in class_names)

    return selector


def _extract_sample_text(element: Tag, field: str) -> str:
    """Extract a representative sample value from a matched element."""
    if field == "images" or element.name == "img":
        return _truncate_whitespace(element.get("src") or element.get("data-src") or element.get("alt") or "")

    text_value = element.get_text(" ", strip=True)
    if text_value:
        return _truncate_whitespace(text_value)

    return _truncate_whitespace(element.get("content") or element.get("value") or "")


def _address_parts(address_value: Any) -> tuple[str | None, str | None, str | None]:
    """Extract district, county, and parish-like parts from a JSON-LD address value."""
    if isinstance(address_value, str):
        parts = [_truncate_whitespace(part) for part in address_value.split(",") if part.strip()]
        district = parts[0] if len(parts) > 0 else None
        county = parts[1] if len(parts) > 1 else None
        parish = parts[2] if len(parts) > 2 else None
        return district, county, parish

    if isinstance(address_value, dict):
        district = _stringify(address_value.get("addressRegion"))
        county = _stringify(address_value.get("addressLocality"))
        parish = _stringify(address_value.get("name"))
        return district, county, parish

    return None, None, None


def _nested_value(node: dict[str, Any], *keys: str) -> Any:
    """Read a nested key path from a dict-like payload."""
    current: Any = node
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first_string(*values: Any) -> str | None:
    """Return the first truthy value converted to string."""
    for value in values:
        rendered = _stringify(value)
        if rendered:
            return rendered
    return None


def _stringify(value: Any) -> str | None:
    """Render a JSON-LD or HTML-derived value as a clean string."""
    if value is None:
        return None

    if isinstance(value, dict):
        for key in ("url", "contentUrl", "name", "value"):
            rendered = _stringify(value.get(key))
            if rendered:
                return rendered
        return None

    if isinstance(value, (list, tuple, set)):
        first = next((item for item in (_stringify(item) for item in value) if item), None)
        return first

    return _truncate_whitespace(str(value))


def _truncate_whitespace(value: str) -> str:
    """Collapse whitespace and trim long preview values."""
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized[:160]


def _normalize_text(value: str) -> str:
    """Normalize text for semantic matching and scoring."""
    lowered = _truncate_whitespace(value).lower()
    lowered = lowered.replace("á", "a").replace("à", "a").replace("ã", "a")
    lowered = lowered.replace("â", "a").replace("é", "e").replace("ê", "e")
    lowered = lowered.replace("í", "i").replace("ó", "o").replace("ô", "o")
    lowered = lowered.replace("õ", "o").replace("ú", "u").replace("ç", "c")
    return lowered


def _similarity(left: str, right: str) -> float:
    """Calculate a loose similarity score between two extracted values."""
    left_normalized = re.sub(r"\W+", "", left.lower())
    right_normalized = re.sub(r"\W+", "", right.lower())
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _css_token(value: Any) -> str:
    """Return a CSS-safe token using a conservative character set."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-zA-Z0-9_-]", "", value)


def _as_list(value: Any) -> list[Any]:
    """Normalize arbitrary JSON-LD values to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _unique_preserving_order(values: list[str]) -> list[str]:
    """Deduplicate values while keeping their first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
