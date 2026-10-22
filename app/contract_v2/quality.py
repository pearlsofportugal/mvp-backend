"""`quality`: issues the scraper found, a 0-100 score and whether the listing can be published unreviewed."""

# issue -> points taken off 100 (ordered by how much they hurt a published page)
PENALTIES: dict[str, int] = {
    "no_photos": 40,
    "price_missing": 25,
    "few_photos": 10,
    "low_res_photos": 10,
    "area_missing": 10,
    "description_short": 10,
    "address_unmatched": 10,
    "watermarked_photos": 5,
    "contact_in_text": 5,
    "missing_features": 5,
}
FEW_PHOTOS_BELOW = 5
LOW_RES_WIDTH = 800


def assess(
    *,
    photo_count: int,
    widths: list[int],
    watermarked: bool,
    price_ok: bool,
    area_ok: bool,
    description_synthesised: bool,
    had_contact: bool,
    address_matched: bool,
    features: list[str],
    place_ok: bool,
    active: bool,
) -> dict:
    """The contract's `quality` object (issues in a stable order, score, publishable)."""
    issues: list[str] = []
    if photo_count == 0:
        issues.append("no_photos")
    elif photo_count < FEW_PHOTOS_BELOW:
        issues.append("few_photos")
    if widths and min(widths) < LOW_RES_WIDTH:
        issues.append("low_res_photos")
    if watermarked:
        issues.append("watermarked_photos")
    if not price_ok:
        issues.append("price_missing")
    if not area_ok:
        issues.append("area_missing")
    if description_synthesised:
        issues.append("description_short")
    if had_contact:
        issues.append("contact_in_text")
    if not address_matched:
        issues.append("address_unmatched")
    if not features:
        issues.append("missing_features")

    score = max(0, 100 - sum(PENALTIES[i] for i in issues))
    publishable = active and photo_count >= 1 and price_ok and place_ok and not description_synthesised
    return {"score": score, "issues": issues, "publishable": publishable}
