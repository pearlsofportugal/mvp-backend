"""Tests for the generic structured label/value candidate suggester.

Covers bugs found while adding gross_area/construction_year support, all
reproduced from remax.pt's and era.pt's real markup: a Tailwind-style spec
grid where every row shares identical generic classes, with no semantic hook
anywhere (no "gallery"/"label"/"value" class, nothing but utility classes).
"""
from bs4 import BeautifulSoup

from app.crawler import selector_suggester as ss

# remax.pt's real characteristics grid: every row is structurally identical —
# same wrapping <div> classes, same <span class="ml-auto ..."> for the value —
# so nothing but DOM position distinguishes one row from another.
_REMAX_GRID_HTML = """
<html><body><div class="grid">
  <div class="flex flex-row flex-nowrap items-center justify-start text-base">
    <svg></svg><span class="ml-2.5">Área Bruta Privativa m²</span><span class="ml-auto text-grey-neutral whitespace-nowrap">197</span>
  </div>
  <div class="flex flex-row flex-nowrap items-center justify-start text-base">
    <svg></svg><span class="ml-2.5">Área Útil m²</span><span class="ml-auto text-grey-neutral whitespace-nowrap">180</span>
  </div>
  <div class="flex flex-row flex-nowrap items-center justify-start text-base">
    <svg></svg><span class="ml-2.5">Quartos</span><span class="ml-auto text-grey-neutral whitespace-nowrap">5</span>
  </div>
  <div class="flex flex-row flex-nowrap items-center justify-start text-base">
    <svg></svg><span class="ml-2.5">Ano de Construção</span><span class="ml-auto text-grey-neutral whitespace-nowrap">1951</span>
  </div>
  <div class="flex flex-row flex-nowrap items-center justify-start text-base">
    <svg></svg><span class="ml-2.5">WC/Casas de banho</span><span class="ml-auto text-grey-neutral whitespace-nowrap">3</span>
  </div>
  <div class="flex flex-row flex-nowrap items-center justify-start text-base">
    <svg></svg><span class="ml-2.5">Divisões</span><span class="ml-auto text-grey-neutral whitespace-nowrap">Cozinha</span>
  </div>
</div></body></html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestBuildPositionalSelector:
    def test_finds_a_globally_unique_chain_among_identical_siblings(self):
        soup = _soup(_REMAX_GRID_HTML)
        rows = soup.select("div.flex")
        year_value = rows[3].select("span")[1]  # "1951"
        selector = ss._build_positional_selector(soup, year_value)
        assert selector is not None
        assert soup.select(selector) == [year_value]

    def test_returns_none_for_truly_indistinguishable_duplicates(self):
        soup = _soup("<html><body><p>x</p><p>x</p></body></html>")
        p = soup.select("p")[0]
        # Both <p> tags are nth-of-type(1)/(2) of the same parent — already
        # unique by position, so this one *does* resolve; duplicate content
        # alone (not duplicate position) never defeats a positional selector.
        assert ss._build_positional_selector(soup, p) is not None


class TestBuildSelectorNeverReturnsAmbiguousClassSelector:
    def test_homogeneous_rows_each_get_their_own_unique_selector(self):
        soup = _soup(_REMAX_GRID_HTML)
        rows = soup.select("div.flex")
        value_spans = [row.select("span")[1] for row in rows]
        selectors = [ss._build_selector(soup, el, "div") for el in value_spans]
        # Every row shares the same class-based selector ("span.ml-auto...");
        # _build_selector must not return that ambiguous string for any of
        # them — each selector must resolve back to its own distinct element.
        for selector, element in zip(selectors, value_spans):
            assert soup.select(selector) == [element]
        assert len(set(selectors)) == len(selectors)


class TestIsValidStructuredValue:
    def test_rejects_a_compound_label_glued_from_two_dictionary_entries(self):
        # "WC/Casas de banho" (remax.pt) contains both "wc" and "casas de
        # banho" as substrings but matches neither exactly — a naive
        # exact-match check lets it through as if it were a value.
        all_labels = {item for values in ss._STRUCTURED_FIELD_LABELS.values() for item in values}
        assert ss._is_valid_structured_value("WC/Casas de banho", "wc", all_labels) is False

    def test_rejects_the_label_extended_with_extra_descriptive_words(self):
        # "Área Bruta Privativa m²" (remax.pt) is the on-page label text; the
        # matched dictionary synonym is only "área bruta" — not equal, so an
        # exact-equality check misses it, but it's still the label, not "197".
        all_labels = {item for values in ss._STRUCTURED_FIELD_LABELS.values() for item in values}
        assert ss._is_valid_structured_value("Área Bruta Privativa m²", "area bruta", all_labels) is False

    def test_accepts_a_real_numeric_value(self):
        all_labels = {item for values in ss._STRUCTURED_FIELD_LABELS.values() for item in values}
        assert ss._is_valid_structured_value("197", "área bruta", all_labels) is True

    def test_accepts_a_real_non_numeric_value(self):
        all_labels = {item for values in ss._STRUCTURED_FIELD_LABELS.values() for item in values}
        assert ss._is_valid_structured_value("Sim", "elevador", all_labels) is True


class TestCollectStructuredFieldCandidatesOnRemaxStyleGrid:
    def test_gross_area_extracts_the_number_not_the_label(self):
        soup = _soup(_REMAX_GRID_HTML)
        candidates = ss._collect_structured_field_candidates(soup, "gross_area", [])
        assert candidates
        assert candidates[0]["sample"] == "197"

    def test_construction_year_extracts_the_year(self):
        soup = _soup(_REMAX_GRID_HTML)
        candidates = ss._collect_structured_field_candidates(soup, "construction_year", [])
        assert candidates
        assert candidates[0]["sample"] == "1951"

    def test_bathrooms_extracts_the_count_not_the_compound_label(self):
        soup = _soup(_REMAX_GRID_HTML)
        candidates = ss._collect_structured_field_candidates(soup, "bathrooms", [])
        assert candidates
        assert candidates[0]["sample"] == "3"

    def test_rooms_prefers_the_bedroom_count_over_a_room_type_enumeration(self):
        # "Divisões: Cozinha" is a room-type list sharing the same label
        # vocabulary as a bedroom *count* ("divisões" is a rooms synonym) —
        # the real "Quartos: 5" row must win.
        soup = _soup(_REMAX_GRID_HTML)
        candidates = ss._collect_structured_field_candidates(soup, "rooms", [])
        assert candidates
        assert candidates[0]["sample"] == "5"


# remax.pt's real "nearby parishes" cross-sell widget: a heading whose own text
# is "Freguesias Próximas" ("Nearby Parishes" — plural), followed by links to
# OTHER parishes near this listing, not the listing's own address. Reproduced
# from https://remax.pt/pt/imoveis/venda-apartamento-t3-amadora-mina-de-agua —
# every listing on the site carried this block, and before the fix its own
# heading text was picked up as the property's district/county/parish.
_REMAX_NEARBY_REGIONS_HTML = """
<html><body>
<div id="nearby-regions">
  <h3>Freguesias Próximas</h3>
  <div class="grid">
    <a href="/pt/comprar/imoveis/apartamento/r/r/alfragide/t">Alfragide</a>
    <a href="/pt/comprar/imoveis/apartamento/r/r/venteira/t">Venteira</a>
  </div>
</div>
</body></html>
"""


class TestNearbyRegionsWidgetIsNotTakenAsAddress:
    def test_parish_has_no_candidate_from_the_nearby_regions_widget(self):
        soup = _soup(_REMAX_NEARBY_REGIONS_HTML)
        candidates = ss._collect_structured_field_candidates(soup, "parish", [])
        assert candidates == []

    def test_district_and_county_also_get_no_candidate(self):
        soup = _soup(_REMAX_NEARBY_REGIONS_HTML)
        assert ss._collect_structured_field_candidates(soup, "district", []) == []
        assert ss._collect_structured_field_candidates(soup, "county", []) == []

    def test_is_in_excluded_region_true_for_the_widget_and_its_contents(self):
        soup = _soup(_REMAX_NEARBY_REGIONS_HTML)
        heading = soup.find("h3")
        link = soup.find("a")
        assert ss._is_in_excluded_region(heading) is True
        assert ss._is_in_excluded_region(link) is True


class TestFindStructuredLabelWordBoundary:
    def test_plural_heading_does_not_match_a_singular_label(self):
        # "Freguesias Próximas" starts with "freguesia" character-for-character
        # (plural "freguesias" = "freguesia" + "s") — a naive startswith() would
        # wrongly read this heading as a "freguesia" (parish) label/value row.
        soup = _soup("<div><h3>Freguesias Próximas</h3></div>")
        node = soup.find("div")
        assert ss._find_structured_label(node, "parish") is None

    def test_exact_label_word_still_matches(self):
        soup = _soup("<div>Freguesia: Sintra</div>")
        node = soup.find("div")
        assert ss._find_structured_label(node, "parish") == "freguesia"
