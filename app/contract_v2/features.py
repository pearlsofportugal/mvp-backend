"""`features[]`: controlled amenity codes, from the stored flags plus clear evidence in the text.

Only positives are listed (the contract's `null` = unknown, and a feature we are not sure about must
not appear). A mention does not count when it is negated ("sem lareira", "nao mobilado") or hedged right
before it ("perto de", "possibilidade de"). The hedge window is deliberately tight: the extractor used for
the stored flags also treats "vista" as a hedge up to three words earlier, which would drop "churrasqueira"
from "vista mar e churrasqueira".
"""
import re

from app.services.attribute_extractor import _NEGATION_BEFORE, _fold

# contract code -> (pt, en)
FEATURE_LABELS: dict[str, tuple[str, str]] = {
    "garage": ("Garagem", "Garage"),
    "parking": ("Estacionamento", "Parking"),
    "elevator": ("Elevador", "Lift"),
    "balcony": ("Varanda", "Balcony"),
    "terrace": ("Terraço", "Terrace"),
    "garden": ("Jardim", "Garden"),
    "pool": ("Piscina", "Swimming pool"),
    "air_conditioning": ("Ar condicionado", "Air conditioning"),
    "central_heating": ("Aquecimento central", "Central heating"),
    "fireplace": ("Lareira", "Fireplace"),
    "solar_panels": ("Painéis solares", "Solar panels"),
    "storage_room": ("Arrecadação", "Storage room"),
    "furnished": ("Mobilado", "Furnished"),
    "equipped_kitchen": ("Cozinha equipada", "Equipped kitchen"),
    "sea_view": ("Vista mar", "Sea view"),
    "river_view": ("Vista rio", "River view"),
    "mountain_view": ("Vista serra", "Mountain view"),
    "gated_community": ("Condomínio fechado", "Gated community"),
    "concierge": ("Portaria", "Concierge"),
    "accessible": ("Acessibilidade reduzida", "Accessible"),
    "barbecue": ("Churrasqueira", "Barbecue"),
    "playground": ("Parque infantil", "Playground"),
    "gym": ("Ginásio", "Gym"),
    "security_alarm": ("Alarme", "Security alarm"),
    "video_intercom": ("Videoporteiro", "Video intercom"),
    "double_glazing": ("Vidros duplos", "Double glazing"),
}

# stored boolean column -> contract code
FLAG_COLUMNS: dict[str, str] = {
    "has_garage": "garage",
    "has_elevator": "elevator",
    "has_balcony": "balcony",
    "has_air_conditioning": "air_conditioning",
    "has_pool": "pool",
    "has_garden": "garden",
}

# patterns run on accent-free, lower-case text
_TEXT_TERMS: dict[str, re.Pattern[str]] = {
    "parking": re.compile(r"\bestacionamento\b|\bparqueamento\b"),
    "terrace": re.compile(r"\bterracos?\b|\brooftop\b"),
    "central_heating": re.compile(r"aquecimento central|piso radiante|\bradiadores\b|\bcaldeira\b"),
    "fireplace": re.compile(r"\blareira\b|\bsalamandra\b|recuperador de calor"),
    "solar_panels": re.compile(r"paineis? solares?|solar termico|fotovoltaic"),
    "storage_room": re.compile(r"\barrecadacao\b|\barrumos\b"),
    "furnished": re.compile(r"(?<!nao )(?<!semi-)\bmobilad[oa]\b|\btotalmente equipad[oa]\b"),
    "equipped_kitchen": re.compile(r"cozinha (?:totalmente |completamente |toda )?equipada"),
    "sea_view": re.compile(r"vistas? (?:de |para o |sobre o |frente )?(?:mar|oceano)\b|\bfrente mar\b"),
    "river_view": re.compile(r"vistas? (?:de |para o |sobre o )?rio\b"),
    "mountain_view": re.compile(r"vistas? (?:de |para a |sobre a )?(?:serra|montanha)\b"),
    "gated_community": re.compile(r"condominio fechado|urbanizacao fechada|comunidade fechada"),
    "concierge": re.compile(r"\bportaria\b|\bporteiro\b|\bconcierge\b"),
    "accessible": re.compile(r"mobilidade reduzida|\bacessibilidade\b"),
    "barbecue": re.compile(r"\bchurrasqueira\b|\bbarbecue\b"),
    "playground": re.compile(r"parque infantil|zona de recreio|\bplayground\b"),
    "gym": re.compile(r"\bginasio\b|\bgym\b|sala de fitness"),
    "security_alarm": re.compile(r"\balarme\b|videovigilancia|sistema de seguranca"),
    "video_intercom": re.compile(r"\bvideo ?porteiro\b|\bvideoporteiro\b"),
    "double_glazing": re.compile(r"vidros? duplos?|janelas duplas|caixilharia com vidro duplo"),
}


_HEDGE_RIGHT_BEFORE = re.compile(
    r"(?:perto|junto|proximo|proximidade|possibilidade|possivel|opcional|licenca|projeto|projecto)\W+(?:de|do|da|a|ao|para)?\W*(?:\w+\W+){0,1}$"
)
_CONTEXT = 40


def _clearly_present(folded: str, term: re.Pattern[str]) -> bool:
    """True when *term* appears at least once without a negation or an immediate hedge in front of it."""
    for match in term.finditer(folded):
        before = folded[max(0, match.start() - _CONTEXT):match.start()]
        if _NEGATION_BEFORE.search(before) or _HEDGE_RIGHT_BEFORE.search(before):
            continue
        return True
    return False


def extract_features(listing, text: str | None) -> list[str]:
    """Sorted unique contract codes that are true for *listing* (stored flags + clear text evidence)."""
    found: set[str] = {code for column, code in FLAG_COLUMNS.items() if getattr(listing, column, None) is True}
    explicitly_absent = {code for column, code in FLAG_COLUMNS.items() if getattr(listing, column, None) is False}
    if text:
        folded = _fold(text)
        for code, pattern in _TEXT_TERMS.items():
            if _clearly_present(folded, pattern):
                found.add(code)
    # a stored `false` (the source said there is none) wins over a looser text match
    return sorted(found - explicitly_absent)
