# app/core/normalizer.py
import re
import unicodedata
from typing import Optional
from app.core.catalogs import VALID_ENERGY_CLASSES, EXEMPT_ENERGY_CLASSES, LAND_PROPERTY_TYPES


_ENERGY_RATING_PATTERN = re.compile(
    r"(?:certificado|certifica(?:\u00e7\u00e3o|cao)|classe)\s+energ(?:\u00e9tico|etico|\u00e9tica|etica)|"
    r"energy\s+(?:certificate|class)",
    re.IGNORECASE,
)


def _normalized_text(value: str) -> str:
    """Lowercase text without accents, for matching site-specific labels."""
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value.lower())
        if unicodedata.category(char) != "Mn"
    )


def _is_land_property_type(property_type: str | None) -> bool:
    """Return whether a Portuguese or English property type denotes land."""
    if not property_type:
        return False

    normalized = _normalized_text(property_type).strip()
    catalog_types = {_normalized_text(value) for value in LAND_PROPERTY_TYPES}
    return normalized in catalog_types or bool(
        re.search(r"\b(?:terreno|lote|lot|ground|land|allotment)\b", normalized)
    )


def normalize_energy_certificate(raw_val: Optional[str], property_type: Optional[str] = None) -> Optional[str]:
    """
    Normaliza o certificado energético de acordo com o catálogo oficial.
    Resolve o bug de terrenos e isenções.
    """
    # Regra 1: Se for um terreno, nunca tem certificado energético
    if _is_land_property_type(property_type):
        return "Exempted"

    if not raw_val:
        return "Unavailable"

    val_clean = raw_val.strip().upper()
    compact = re.sub(r"\s+", "", val_clean)

    # Mapeamento de sinonimos e variantes apanhadas no scraping
    synonyms = {
        "A+": "A+", "A +": "A+", "A´": "A+", "A'": "A+",
        "A": "A", "B": "B", "B-": "B-", "B -": "B-",
        "C": "C", "D": "D", "E": "E", "F": "F", "G": "G",
        "ISENTO": "Exempted", "EXENTO": "Exempted", "EXEMPT": "Exempted", "EXEMPTED": "Exempted",
        "EM TRAMITE": "Evaluation in progress", "EM TRÂMITE": "Evaluation in progress", 
        "IN PROGRESS": "Evaluation in progress", "EM PROCESSAMENTO": "Evaluation in progress",
        "N/A": "Unavailable", "NA": "Unavailable", "UNAVAILABLE": "Unavailable"
    }

    # Procura direta no mapa de sinónimos
    if val_clean in synonyms:
        return synonyms[val_clean]
    if compact in synonyms:
        return synonyms[compact]

    # Não aceitar letras soltas em texto arbitrário (por exemplo, o artigo
    # português "a" numa descrição). Só extrair uma classe de uma frase que
    # identifique explicitamente o certificado energético.
    if _ENERGY_RATING_PATTERN.search(raw_val):
        rating_match = re.search(r"\b([A-G](?:[+-])?)(?![A-Z0-9])", val_clean)
        if rating_match:
            return rating_match.group(1)

    # Validação contra a lista oficial final
    for valid_class in VALID_ENERGY_CLASSES:
        if valid_class.upper() == val_clean:
            return valid_class

    return "Unavailable"
