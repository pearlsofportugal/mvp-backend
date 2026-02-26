"""Parser service — HTML parsing for property listings with DB-configurable field mappings.

Supports two extraction modes:
1. Section-based: extracts from sections with .name/.value pairs (e.g., Pearls of Portugal)
2. Direct selector: CSS selectors for each field directly (configurable per site)

CONFIGURATION:
  - Field name translations loaded from 'field_mappings' table
  - Feature detection keywords loaded from 'field_mappings' table (type='feature')
  - Cache is loaded at application startup via init_parser_cache() (called in lifespan)
  - Cache is also refreshed lazily on parse_listing_page() calls (TTL-based)

MELHORIAS v2:
  - _load_field_mappings() chamada no startup via init_parser_cache() e lazy na parse
  - asyncio.Lock protege a recarga do cache contra race conditions
  - Lookup do field_map convertido de O(n×m) para O(n) via índice invertido por token
  - parse_listing_card() (dead code) removida — funcionalidade mantida em _parse_listing_card_soup()
  - Fallback str(soup) no pattern matching tornado opt-in via 'text_pattern_search_html' selector flag
  - Campo 'details_section = body' documentado explicitamente
  - Paridade de campos entre modos section e direct (description, advertiser, etc.)
"""
import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.core.logging import get_logger
from app.database import async_session_factory

logger = get_logger(__name__)




_FIELD_MAP_CACHE: Dict[str, str] = {}
_FEATURE_MAP_CACHE: Dict[str, str] = {}


_FIELD_TOKEN_INDEX: Dict[str, List[tuple[str, str]]] = {}

_CACHE_TIMESTAMP: Optional[datetime] = None
_CACHE_TTL_SECONDS = 300  


_cache_lock = asyncio.Lock()

_DEFAULT_FIELD_MAP: Dict[str, str] = {
    "price": "price",
    "preço": "price",
    "valor": "price",

    "business type": "business_type",
    "business state": "business_state",
    "tipo de negócio": "business_type",
    "tipo negócio": "business_type",

    "typology": "typology",
    "tipologia": "typology",
    "tipo": "typology",

    "bedrooms": "bedrooms",
    "quartos": "bedrooms",
    "assoalhadas": "bedrooms",

    "bathrooms": "bathrooms",
    "casas de banho": "bathrooms",
    "wc": "bathrooms",
    "living rooms": "living_rooms",
    "salas": "living_rooms",

    "floor": "floor",
    "andar": "floor",
    "piso": "floor",

    "energy certificate": "energy_certificate",
    "certificado energético": "energy_certificate",
    "classe energética": "energy_certificate",
    "energy class": "energy_certificate",

    "construction year": "construction_year",
    "ano de construção": "construction_year",

    "property type": "property_type",
    "tipo de imóvel": "property_type",

    "district": "district",
    "distrito": "district",
    "county": "county",
    "concelho": "county",
    "parish": "parish",
    "freguesia": "parish",

    "reference": "property_id",
    "referência": "property_id",
    "ref": "property_id",
}

_DEFAULT_FEATURE_MAP: Dict[str, str] = {
    "garage": "garage",
    "garagem": "garage",
    "parking": "garage",
    "estacionamento": "garage",
    "box": "garage",
    "elevator": "elevator",
    "elevador": "elevator",
    "lift": "elevator",
    "balcony": "balcony",
    "varanda": "balcony",
    "terraço": "balcony",
    "terrace": "balcony",
    "marquise": "balcony",
    "air conditioning": "air_conditioning",
    "ar condicionado": "air_conditioning",
    "a/c": "air_conditioning",
    "climatização": "air_conditioning",
    "pool": "swimming_pool",
    "piscina": "swimming_pool",
    "swimming": "swimming_pool",
}


def _build_token_index(field_map: Dict[str, str]) -> Dict[str, List[tuple[str, str]]]:
    """Constrói um índice invertido por token para lookup O(1).

    Para cada source_key do field_map, extrai os tokens individuais (palavras)
    e mapeia cada token para (source_key, target_field). Assim, ao processar um
    nome HTML, basta tokenizar e fazer lookup direto em vez de iterar todo o mapa.

    Exemplo:
      "casas de banho" → bathrooms
      Gera tokens: "casas", "de", "banho" → todos apontam para "bathrooms"
      Mas a correspondência final verifica se o source_key COMPLETO está no nome,
      garantindo que "de" sozinho não faça match indesejado.
    """
    index: Dict[str, List[tuple[str, str]]] = {}
    for source_key, target_field in field_map.items():
        for token in source_key.split():
            if token not in index:
                index[token] = []
            index[token].append((source_key, target_field))
    return index


def _lookup_field(name: str, field_map: Dict[str, str], token_index: Dict[str, List[tuple[str, str]]]) -> Optional[str]:
    """Lookup O(n_tokens) do field_map para um nome HTML.

    1. Tokeniza o nome HTML.
    2. Para cada token, consulta o índice invertido para obter candidatos.
    3. Verifica se o source_key completo do candidato está contido no nome.
    4. Retorna o primeiro target_field que fizer match.

    Esta abordagem é substancialmente mais rápida que iterar todo o field_map
    (O(m)) para cada item HTML, especialmente com field_maps grandes vindos de DB.
    """
    tokens = name.split()
    seen_sources: set[str] = set()

    for token in tokens:
        candidates = token_index.get(token, [])
        for source_key, target_field in candidates:
            if source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            if source_key in name:
                return target_field

    return None


async def _load_field_mappings() -> None:
    """Carrega os field mappings da DB com caching e protecção contra race conditions.

    Usa asyncio.Lock para garantir que apenas um coroutine recarrega o cache de
    cada vez — essencial quando múltiplos jobs correm concorrentemente.
    """
    global _FIELD_MAP_CACHE, _FEATURE_MAP_CACHE, _FIELD_TOKEN_INDEX, _CACHE_TIMESTAMP

    now = datetime.now(timezone.utc)

    if (
        _CACHE_TIMESTAMP
        and _FIELD_MAP_CACHE
        and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return

    async with _cache_lock:
        now = datetime.now(timezone.utc)
        if (
            _CACHE_TIMESTAMP
            and _FIELD_MAP_CACHE
            and (now - _CACHE_TIMESTAMP).total_seconds() < _CACHE_TTL_SECONDS
        ):
            return

        try:
            from sqlalchemy import select
            from app.models.field_mapping_model import FieldMapping

            async with async_session_factory() as db:
                result = await db.execute(
                    select(FieldMapping).where(FieldMapping.is_active.is_(True))
                )
                mappings = result.scalars().all()

                field_map: Dict[str, str] = {}
                feature_map: Dict[str, str] = {}

                for m in mappings:
                    if m.mapping_type == "field":
                        field_map[m.source_name.lower()] = m.target_field
                    elif m.mapping_type == "feature":
                        feature_map[m.source_name.lower()] = m.target_field

                if field_map:
                    _FIELD_MAP_CACHE = field_map
                    _FIELD_TOKEN_INDEX = _build_token_index(field_map)
                if feature_map:
                    _FEATURE_MAP_CACHE = feature_map

                _CACHE_TIMESTAMP = now
                logger.debug(
                    "Loaded %d field mappings and %d feature mappings from DB",
                    len(field_map),
                    len(feature_map),
                )

        except Exception as e:
            logger.warning("Could not load field mappings from DB: %s. Using defaults.", str(e))
            _FIELD_MAP_CACHE = _DEFAULT_FIELD_MAP.copy()
            _FEATURE_MAP_CACHE = _DEFAULT_FEATURE_MAP.copy()
            _FIELD_TOKEN_INDEX = _build_token_index(_FIELD_MAP_CACHE)
            _CACHE_TIMESTAMP = now  


def _get_field_map() -> Dict[str, str]:
    return _FIELD_MAP_CACHE if _FIELD_MAP_CACHE else _DEFAULT_FIELD_MAP


def _get_feature_map() -> Dict[str, str]:
    return _FEATURE_MAP_CACHE if _FEATURE_MAP_CACHE else _DEFAULT_FEATURE_MAP


def _get_token_index() -> Dict[str, List[tuple[str, str]]]:
    if _FIELD_TOKEN_INDEX:
        return _FIELD_TOKEN_INDEX
    return _build_token_index(_DEFAULT_FIELD_MAP)


def invalidate_parser_cache() -> None:
    """Limpa o cache do parser (chamar após atualizações de config na DB)."""
    global _FIELD_MAP_CACHE, _FEATURE_MAP_CACHE, _FIELD_TOKEN_INDEX, _CACHE_TIMESTAMP
    _FIELD_MAP_CACHE = {}
    _FEATURE_MAP_CACHE = {}
    _FIELD_TOKEN_INDEX = {}
    _CACHE_TIMESTAMP = None
    logger.info("Parser configuration cache invalidated")


async def init_parser_cache() -> None:
    """Inicializa o cache do parser carregando os field mappings da DB.

    Deve ser chamada no startup da aplicação (lifespan) para garantir que o
    cache está pronto antes do primeiro job de scraping arrancar.
    """
    await _load_field_mappings()




def parse_listing_links(
    html: str,
    base_url: str,
    selectors: Dict[str, Any],
) -> List[str]:
    """Extrai URLs de listings de uma página de resultados de pesquisa."""
    soup = BeautifulSoup(html, "lxml")
    link_selector = selectors.get("listing_link_selector", "a")
    link_pattern = selectors.get("listing_link_pattern")

    links: List[str] = []
    for a_tag in soup.select(link_selector):
        href = a_tag.get("href")
        if not href:
            continue
        absolute_url = urljoin(base_url, href)

        if link_pattern and not re.search(link_pattern, absolute_url):
            continue

        if absolute_url not in links:
            links.append(absolute_url)

    logger.info("Found %d listing links on page", len(links))
    return links


def parse_next_page(
    html: str,
    base_url: str,
    selectors: Dict[str, Any],
) -> Optional[str]:
    """Extrai a URL da próxima página a partir da paginação."""
    soup = BeautifulSoup(html, "lxml")
    next_selector = selectors.get("next_page_selector")

    if not next_selector:
        return None

    next_link = soup.select_one(next_selector)
    if next_link and next_link.get("href"):
        return urljoin(base_url, next_link["href"])

    return None


async def parse_listing_page(
    html: str,
    url: str,
    selectors: Dict[str, Any],
    extraction_mode: str = "direct",
) -> Dict[str, Any]:
    """Faz parse de uma página de detalhe de listing num dict raw.

    NOTA: É async para poder fazer refresh do cache de field mappings se necessário.
    O refresh é lazy (TTL-based) e protegido por lock — não bloqueia o event loop
    porque a leitura da DB é async.
    """
    await _load_field_mappings()

    soup = BeautifulSoup(html, "lxml")
    data: Dict[str, Any] = {"url": url}

    if extraction_mode == "section":
        data.update(_parse_section_based(soup, selectors))
    else:
        data.update(_parse_direct_selectors(soup, selectors))

    data.update(_parse_images(soup, selectors, url))
    data.update(_parse_seo(soup))

    return data




def _parse_section_based(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Parse via extração baseada em secções (pares nome/valor)."""
    data: Dict[str, Any] = {}

    title_selector = selectors.get("title_selector")
    if title_selector:
        title_el = soup.select_one(title_selector)
        if title_el:
            data["title"] = title_el.get_text(strip=True)

    location_selector = selectors.get("location_selector")
    if location_selector:
        loc_el = soup.select_one(location_selector)
        if loc_el:
            data["location"] = loc_el.get_text(strip=True)

    condition_selector = selectors.get("condition_selector")
    if condition_selector:
        cond_el = soup.select_one(condition_selector)
        if cond_el:
            data["condition"] = cond_el.get_text(strip=True)

    desc_selector = selectors.get("description_selector")
    if desc_selector:
        for selector in desc_selector.split(","):
            desc_el = soup.select_one(selector.strip())
            if desc_el:
                text = desc_el.get_text(strip=True)
                if text and len(text) > 50:
                    data["raw_description"] = text
                    break

    adv_selector = selectors.get("advertiser_selector")
    if adv_selector:
        el = soup.select_one(adv_selector)
        if el:
            data["advertiser"] = el.get_text(strip=True)

    adv_phone_selector = selectors.get("advertiser_phone_selector")
    if adv_phone_selector:
        el = soup.select_one(adv_phone_selector)
        if el:
            data["advertiser_phone"] = el.get_text(strip=True)

    text_patterns = selectors.get("text_patterns", {})
    if text_patterns:
        data.update(_extract_via_text_patterns(soup, text_patterns, selectors))

    details_section = selectors.get("details_section")

    if details_section and details_section != "body":
        section = soup.select_one(details_section)
        if section:
            extracted = _extract_name_value_pairs(section, selectors)
            logger.debug("Extracted %d fields from details section", len(extracted))
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v

    areas_section = selectors.get("areas_section")
    if areas_section:
        section = soup.select_one(areas_section)
        if section:
            extracted = _extract_area_pairs(section, selectors)
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v


    divisions_section = selectors.get("divisions_section")
    if divisions_section:
        section = soup.select_one(divisions_section)
        if section:
            extracted = _extract_divisions(section, selectors)
            for k, v in extracted.items():
                if k not in data:
                    data[k] = v

    chars_section = selectors.get("characteristics_section")
    if chars_section:
        section = _safe_select_one(soup, chars_section)
        if section:
            data.update(_extract_characteristics(section, selectors))

    nearby_section = selectors.get("nearby_section")
    if nearby_section:
        section = _safe_select_one(soup, nearby_section)
        if section:
            nearby_items = []
            item_selector = selectors.get("nearby_item_selector", ".name")
            for item in section.select(item_selector):
                text = item.get_text(strip=True)
                if text:
                    nearby_items.append(text)
            if nearby_items:
                data["nearby"] = nearby_items

    return data


def _safe_select_one(soup: BeautifulSoup, selector: str) -> Optional[Tag]:
    """Seleciona um elemento com fallback para pseudo-selectors não suportados pelo soupsieve.

    Se o selector falhar ou não retornar nada, seleciona todos os matches e retorna o último.
    (Cobre casos como :last-of-type combinado com ID selectors.)
    """
    try:
        el = soup.select_one(selector)
        if el:
            return el
        all_els = soup.select(selector)
        return all_els[-1] if all_els else None
    except Exception:
        base_selector = re.sub(r':[a-z-]+(\([^)]*\))?', '', selector).strip()
        if base_selector and base_selector != selector:
            try:
                all_els = soup.select(base_selector)
                return all_els[-1] if all_els else None
            except Exception:
                pass
    return None




def _extract_name_value_pairs(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai pares nome/valor de uma secção de detalhes.

    Usa o índice de tokens para lookup O(n_tokens) em vez de O(m) por item.

    Fallback: quando o value element está ausente ou vazio (ex: energy certificate
    renderizado como <img>), extrai a classe energética do src ou alt da imagem.
    """
    data: Dict[str, Any] = {}
    name_selector = selectors.get("detail_name_selector", ".name")
    value_selector = selectors.get("detail_value_selector", ".value")
    token_index = _get_token_index()
    field_map = _get_field_map()

    items = section.select(selectors.get("detail_item_selector", ".detail"))
    for item in items:
        name_el = item.select_one(name_selector)
        if not name_el:
            continue

        name = name_el.get_text(strip=True).lower()


        value_el = item.select_one(value_selector)
        value = value_el.get_text(strip=True) if value_el else ""


        if not value:
            img = item.select_one("img")
            if img:
                alt = (img.get("alt") or "").strip()
                alt_match = re.search(r'\b([A-G])\b', alt, re.IGNORECASE)
                if alt_match:
                    value = alt_match.group(1).upper()
                else:
                    src = img.get("src") or img.get("data-src") or ""
                    src_match = re.search(r'energy[-_]([a-g])', src, re.IGNORECASE)
                    if src_match:
                        value = src_match.group(1).upper()

        if not value:
            continue

        target_field = _lookup_field(name, field_map, token_index)
        if target_field:
            data[target_field] = value

    return data


def _extract_divisions(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai quartos/casas de banho/salas de uma secção de divisões.

    Layout Pearls of Portugal:
      <div class="division">
        <img class="icon" src="...bedrooms.png">
        <div class="name">Bedrooms</div>
        <div class="value">5</div>
      </div>

    Usa o mesmo field_map de _extract_name_value_pairs para suporte multilíngua.
    """
    data: Dict[str, Any] = {}
    item_selector = selectors.get("division_item_selector", "div.division")
    name_selector = selectors.get("division_name_selector", "div.name")
    value_selector = selectors.get("division_value_selector", "div.value")
    token_index = _get_token_index()
    field_map = _get_field_map()

    for item in section.select(item_selector):
        name_el = item.select_one(name_selector)
        value_el = item.select_one(value_selector)
        if not name_el or not value_el:
            continue

        name = name_el.get_text(strip=True).lower()
        value = value_el.get_text(strip=True)
        if not value:
            continue

        target_field = _lookup_field(name, field_map, token_index)
        if target_field:
            data[target_field] = value

    return data


def _extract_area_pairs(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai medidas de área de uma secção de áreas."""
    data: Dict[str, Any] = {}
    items = section.select(selectors.get("area_item_selector", ".area"))
    name_selector = selectors.get("area_name_selector", ".name")
    value_selector = selectors.get("area_value_selector", ".value")

    for item in items:
        name_el = item.select_one(name_selector)
        value_el = item.select_one(value_selector)
        if name_el and value_el:
            name = name_el.get_text(strip=True).lower()
            value = value_el.get_text(strip=True)

            if "useful" in name or "útil" in name:
                data["useful_area"] = value
            elif "gross" in name or "bruta" in name:
                data["gross_area"] = value
            elif "land" in name or "terreno" in name:
                data["land_area"] = value

    return data


def _extract_characteristics(section: Tag, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai características booleanas (amenidades) de uma secção de features."""
    data: Dict[str, Any] = {}
    items = section.select(selectors.get("char_item_selector", ".name"))
    feature_map = _get_feature_map()

    for item in items:
        text = item.get_text(strip=True).lower()
        for keyword, field in feature_map.items():
            if keyword in text:
                data[field] = "Yes"
                break

    return data




def _parse_direct_selectors(soup: BeautifulSoup, selectors: Dict[str, Any]) -> Dict[str, Any]:
    """Parse via CSS selectors diretos para cada campo."""
    data: Dict[str, Any] = {}

    simple_fields = {
        "title_selector": "title",
        "location_selector": "location",
        "condition_selector": "condition",
        "property_type_selector": "property_type",
        "typology_selector": "typology",
        "bedrooms_selector": "bedrooms",
        "bathrooms_selector": "bathrooms",
        "floor_selector": "floor",
        "construction_year_selector": "construction_year",
        "energy_certificate_selector": "energy_certificate",
        "district_selector": "district",
        "county_selector": "county",
        "parish_selector": "parish",
        "price_selector": "price",
        "business_type_selector": "business_type",
        "price_per_m2_selector": "price_per_m2",
        "publication_date_selector": "publication_date",
        "advertiser_selector": "advertiser",
        "advertiser_phone_selector": "advertiser_phone",
        "advertiser_email_selector": "advertiser_email",
    }

    for selector_key, field in simple_fields.items():
        selector = selectors.get(selector_key)
        if not selector:
            continue
        el = soup.select_one(selector)
        if el:
            data[field] = el.get_text(strip=True)

    desc_selector = selectors.get("description_selector")
    if desc_selector:
        for selector in desc_selector.split(","):
            desc_el = soup.select_one(selector.strip())
            if desc_el:
                text = desc_el.get_text(strip=True)
                if text and len(text) > 50:
                    data["raw_description"] = text
                    break

    prop_id_selector = selectors.get("property_id_selector")
    if prop_id_selector:
        el = soup.select_one(prop_id_selector)
        if el:
            text = el.get_text(strip=True)
            if text:
                data["property_id"] = text
            else:
                for attr in ("reference", "data-reference", "data-id", "content"):
                    val = el.get(attr)
                    if val:
                        data["property_id"] = val
                        break

    features_selector = selectors.get("features_selector")
    if features_selector:
        feature_map = _get_feature_map()
        for el in soup.select(features_selector):
            text = el.get_text(strip=True).lower()
            for keyword, mapped_field in feature_map.items():
                if keyword in text:
                    data[mapped_field] = "Yes"
                    break

    individual_features = {
        "garage_selector": "garage",
        "elevator_selector": "elevator",
        "balcony_selector": "balcony",
        "air_conditioning_selector": "air_conditioning",
        "pool_selector": "swimming_pool",
        "garden_selector": "garden",
    }
    for selector_key, field in individual_features.items():
        selector = selectors.get(selector_key)
        if selector and soup.select_one(selector):
            data[field] = "Yes"

    text_patterns = selectors.get("text_patterns", {})
    if text_patterns:
        data.update(_extract_via_text_patterns(soup, text_patterns, selectors))

    return data



def _parse_images(soup: BeautifulSoup, selectors: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    """Extrai imagens da página de listing."""
    data: Dict[str, Any] = {"images": [], "alt_texts": []}

    image_selector = selectors.get("image_selector", "img")
    image_filter = selectors.get("image_filter")

    for img in soup.select(image_selector):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if not src:
            continue

        absolute_url = urljoin(base_url, src)

        if image_filter and not re.search(image_filter, absolute_url):
            continue

        data["images"].append(absolute_url)
        data["alt_texts"].append(img.get("alt", ""))

    return data


def _parse_seo(soup: BeautifulSoup) -> Dict[str, Any]:
    """Extrai elementos SEO da página."""
    data: Dict[str, Any] = {}

    title_tag = soup.find("title")
    if title_tag:
        data["page_title"] = title_tag.get_text(strip=True)

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        data["meta_description"] = meta_desc["content"]

    headers = []
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(strip=True)
            if text:
                headers.append({"level": f"h{level}", "text": text})
    if headers:
        data["headers"] = headers

    return data


def _extract_via_text_patterns(
    soup: BeautifulSoup,
    patterns: Dict[str, str],
    selectors: Dict[str, Any],
) -> Dict[str, Any]:
    """Extrai dados via regex aplicados ao texto da página.

    Por omissão, aplica os padrões apenas ao texto limpo (get_text).
    O fallback para o HTML raw é opt-in via selector flag 'text_pattern_search_html: true'.
    Isto evita serializar str(soup) (custoso em memória) para todos os sites.
    """
    data: Dict[str, Any] = {}
    search_html = selectors.get("text_pattern_search_html", False)

    full_text = soup.get_text(separator=" ", strip=True)
    full_html = str(soup) if search_html else None

    for field, pattern in patterns.items():
        try:
            match = re.search(pattern, full_text, re.IGNORECASE | re.DOTALL)
            if not match and full_html:
                match = re.search(pattern, full_html, re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1).strip()
                if value:
                    data[field] = value
        except Exception as e:
            logger.warning("Error applying pattern for %s: %s", field, str(e))

    return data