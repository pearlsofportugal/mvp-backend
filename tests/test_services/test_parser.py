"""Tests for parser service — HTML parsing logic."""
import pytest

from app.services.parser_service import (
    extract_listing_links,
    parse_listing_links,
    parse_next_page,
    parse_listing_page,
    _parse_images,
    _parse_seo,
    _extract_energy_certificate_value,
    _infer_property_type_from_title,
    _match_field_key,
    _keyword_sentiment,
    _assign_feature_matches,
    _get_feature_map,
)


class TestEnergyCertificateExtraction:
    def test_does_not_infer_a_rating_from_arbitrary_description_text(self):
        assert _extract_energy_certificate_value(
            "Terreno com acesso a estrada e boa exposição solar."
        ) is None

    @pytest.mark.parametrize(("raw_value", "expected"), [
        ("A+", "A+"),
        ("B-", "B-"),
        ("Certificado energético: A+", "A+"),
        ("Classe energética B-", "B-"),
        ("Isento", None),
        ("N/A", None),
    ])
    def test_extracts_only_explicit_or_standalone_ratings(self, raw_value, expected):
        assert _extract_energy_certificate_value(raw_value) == expected


class TestMatchFieldKey:
    """A specific label (e.g. 'tipo de imóvel') must win over a shorter,
    more generic one (e.g. 'tipo') that happens to be a substring of it —
    dict iteration order must not decide the outcome."""

    def test_prefers_longest_substring_match(self):
        field_map = {"tipo": "typology", "tipo de imóvel": "property_type"}
        assert _match_field_key("tipo de imóvel", field_map) == "tipo de imóvel"

    def test_exact_match_wins_even_if_a_substring_key_exists(self):
        field_map = {"tipo": "typology", "tipologia do imóvel": "typology"}
        assert _match_field_key("tipo", field_map) == "tipo"

    def test_generic_value_keyword_does_not_swallow_unrelated_label(self):
        field_map = {"valor": "price", "valor do condomínio": "condo_fee"}
        assert _match_field_key("valor do condomínio", field_map) == "valor do condomínio"

    def test_no_match_returns_none(self):
        assert _match_field_key("something else", {"tipo": "typology"}) is None


class TestInferPropertyTypeFromTitle:
    def test_matches_typology_token_case_insensitively(self):
        assert _infer_property_type_from_title("Vendo T3 remodelado") == "Apartamento"
        assert _infer_property_type_from_title("V4 com jardim") == "Moradia"

    def test_matches_named_property_types(self):
        assert _infer_property_type_from_title("Moradia isolada com piscina") == "Moradia"


class TestFeatureNegation:
    """'Sem garagem' must not set has_garage=True — and a genuine mention
    elsewhere must still win over an unrelated negation in the same text."""

    def test_negated_mention_returns_no(self):
        assert _keyword_sentiment("garagem", "Apartamento sem garagem, ótima localização.") == "no"

    def test_affirmed_mention_returns_yes(self):
        assert _keyword_sentiment("garagem", "Apartamento com garagem fechada.") == "yes"

    def test_absent_keyword_returns_none(self):
        assert _keyword_sentiment("garagem", "Apartamento moderno no centro.") is None

    def test_negation_does_not_bleed_onto_a_later_affirmed_feature(self):
        # "sem" negates "elevador" only — "garagem" is still affirmed further
        # along in the same sentence.
        text = "Sem elevador, mas tem garagem e piscina."
        assert _keyword_sentiment("elevador", text) == "no"
        assert _keyword_sentiment("garagem", text) == "yes"
        assert _keyword_sentiment("piscina", text) == "yes"

    def test_assign_feature_matches_sets_no_for_negated_and_yes_for_affirmed(self):
        data: dict = {}
        _assign_feature_matches(
            "Sem garagem. Tem elevador e piscina privada.", data, _get_feature_map()
        )
        assert data["garage"] == "No"
        assert data["elevator"] == "Yes"
        assert data["swimming_pool"] == "Yes"

    def test_affirmed_mention_from_a_later_call_overrides_earlier_negation(self):
        feature_map = _get_feature_map()
        data: dict = {}
        _assign_feature_matches("Sem garagem.", data, feature_map)
        assert data["garage"] == "No"
        _assign_feature_matches("Dispõe de garagem fechada para 2 carros.", data, feature_map)
        assert data["garage"] == "Yes"


class TestFeatureFallbackIgnoresChrome:
    """The full-page keyword fallback must not pick up nav/footer/script
    chrome — only content that plausibly describes the listing itself."""

    def test_nav_link_does_not_set_unrelated_feature_flag(self):
        html = """
        <html><body>
            <nav><a href="/lift">Lift</a></nav>
            <h1>Terreno rústico na Calheta</h1>
            <p>Terreno agrícola sem quaisquer comodidades.</p>
        </body></html>
        """
        data = parse_listing_page(html, "https://example.com/p/terreno", {"title_selector": "h1"}, "direct")
        assert data.get("elevator") is None

    def test_script_content_does_not_set_feature_flag(self):
        html = """
        <html><body>
            <script>var config = {"tracking": "garage-door-analytics"};</script>
            <h1>Apartamento simples</h1>
            <p>Apartamento moderno no centro da cidade, perto de tudo.</p>
        </body></html>
        """
        data = parse_listing_page(html, "https://example.com/p/apt", {"title_selector": "h1"}, "direct")
        assert data.get("garage") is None


class TestGardenFeatureExtraction:
    def test_jardim_keyword_sets_garden_field(self):
        html = """
        <html><body>
            <h1>Moradia com jardim</h1>
            <p>Moradia V3 com amplo jardim privativo e churrasqueira.</p>
        </body></html>
        """
        data = parse_listing_page(html, "https://example.com/p/moradia", {"title_selector": "h1"}, "direct")
        assert data.get("garden") == "Yes"

    def test_negated_jardim_sets_no(self):
        html = """
        <html><body>
            <h1>Apartamento T2</h1>
            <p>Apartamento T2 sem jardim, com garagem incluída.</p>
        </body></html>
        """
        data = parse_listing_page(html, "https://example.com/p/apt2", {"title_selector": "h1"}, "direct")
        assert data.get("garden") == "No"


class TestParseListingLinks:
    def test_extracts_links(self):
        html = """
        <html><body>
            <a class="property-link" href="/property/1">Property 1</a>
            <a class="property-link" href="/property/2">Property 2</a>
            <a class="other-link" href="/other">Other</a>
        </body></html>
        """
        selectors = {
            "listing_link_selector": "a.property-link",
        }
        links = parse_listing_links(html, "https://example.com", selectors)
        assert len(links) == 2
        assert "https://example.com/property/1" in links
        assert "https://example.com/property/2" in links

    def test_filters_by_pattern(self):
        html = """
        <html><body>
            <a class="link" href="/property/1">Prop</a>
            <a class="link" href="/other/2">Other</a>
        </body></html>
        """
        selectors = {
            "listing_link_selector": "a.link",
            "listing_link_pattern": r"/property/",
        }
        links = parse_listing_links(html, "https://example.com", selectors)
        assert len(links) == 1

    def test_deduplicates(self):
        html = """
        <html><body>
            <a class="link" href="/property/1">A</a>
            <a class="link" href="/property/1">B</a>
        </body></html>
        """
        selectors = {"listing_link_selector": "a.link"}
        links = parse_listing_links(html, "https://example.com", selectors)
        assert len(links) == 1

    def test_extracts_links_from_onclick_window_location(self):
        html = """
        <html><body>
            <div class="card-wrapper pointer" onclick="window.location.href='property/1'"></div>
            <div class="card-wrapper pointer" onclick="window.location.href='/property/2'"></div>
        </body></html>
        """
        selectors = {"listing_link_selector": ".card-wrapper.pointer"}
        links = parse_listing_links(html, "https://example.com", selectors)
        assert links == ["https://example.com/property/1", "https://example.com/property/2"]

    def test_extracts_matched_and_rejected_links_from_onclick_window_location(self):
        html = """
        <html><body>
            <div class="card-wrapper pointer" onclick="window.location.href='property/1'"></div>
            <div class="card-wrapper pointer" onclick="window.location.href='/property/2'"></div>
            <div class="card-wrapper pointer" onclick="window.location.href='/other/3'"></div>
        </body></html>
        """
        selectors = {
            "listing_link_selector": ".card-wrapper.pointer",
            "listing_link_pattern": r"/property/",
        }
        matched, rejected = extract_listing_links(html, "https://example.com", selectors)
        assert matched == ["https://example.com/property/1", "https://example.com/property/2"]
        assert rejected == ["https://example.com/other/3"]


class TestParseNextPage:
    def test_finds_next_page(self):
        html = '<html><body><a class="next" href="/page/2">Next</a></body></html>'
        selectors = {"next_page_selector": "a.next"}
        result = parse_next_page(html, "https://example.com", selectors)
        assert result == "https://example.com/page/2"

    def test_no_next_page(self):
        html = "<html><body><p>No pagination</p></body></html>"
        selectors = {"next_page_selector": "a.next"}
        result = parse_next_page(html, "https://example.com", selectors)
        assert result is None


class TestParseListingPage:
    def test_direct_mode(self):
        html = """
        <html><body>
            <h1 class="title">Nice Apartment</h1>
            <span class="price">250 000 €</span>
            <div class="desc">A great place to live with plenty of natural light, storage, and a quiet street.</div>
            <title>Page Title</title>
            <meta name="description" content="Meta desc">
        </body></html>
        """
        selectors = {
            "title_selector": "h1.title",
            "price_selector": "span.price",
            "description_selector": "div.desc",
        }
        data = parse_listing_page(html, "https://example.com/p/1", selectors, "direct")
        assert data["title"] == "Nice Apartment"
        assert data["price"] == "250 000 €"
        assert data["raw_description"] == "A great place to live with plenty of natural light, storage, and a quiet street."
        assert data["url"] == "https://example.com/p/1"
        assert data["page_title"] == "Page Title"
        assert data["meta_description"] == "Meta desc"

    def test_section_mode(self):
        html = """
        <html><body>
            <h1 class="property-title">Villa T4</h1>
            <section id="details">
                <div class="detail">
                    <span class="name">Price</span>
                    <span class="value">500 000 €</span>
                </div>
                <div class="detail">
                    <span class="name">Typology</span>
                    <span class="value">T4</span>
                </div>
            </section>
        </body></html>
        """
        selectors = {
            "title_selector": "h1.property-title",
            "details_section": "section#details",
            "detail_item_selector": ".detail",
            "detail_name_selector": ".name",
            "detail_value_selector": ".value",
        }
        data = parse_listing_page(html, "https://example.com/p/2", selectors, "section")
        assert data["title"] == "Villa T4"
        assert data["price"] == "500 000 €"
        assert data["typology"] == "T4"

    def test_direct_mode_extracts_summary_pairs(self):
        html = """
        <html><body>
            <div class="summary">
                <ul>
                    <li><b>Objectivo</b> Venda</li>
                    <li><b>Tipo</b> Apartamento</li>
                    <li><b>Tipologia</b> T0</li>
                    <li><b>Distrito</b> Porto</li>
                    <li><b>Concelho</b> Matosinhos</li>
                    <li><b>Freguesia</b> Sao Mamede</li>
                </ul>
            </div>
        </body></html>
        """
        selectors = {
            "summary_section": ".summary",
            "summary_item_selector": "li",
            "summary_label_selector": "b",
        }
        data = parse_listing_page(html, "https://example.com/p/3", selectors, "direct")
        assert data["business_type"] == "Venda"
        assert data["property_type"] == "Apartamento"
        assert data["typology"] == "T0"
        assert data["district"] == "Porto"
        assert data["county"] == "Matosinhos"
        assert data["parish"] == "Sao Mamede"

    def test_direct_mode_property_type_selector_can_match_detailitem_natureza(self):
        html = """
        <html><body>
            <ul class="propertyDetails">
                <li class="detailItem">
                    <span class="label">Natureza</span>
                    <h4><span class="value">Apartamento</span></h4>
                </li>
            </ul>
        </body></html>
        """
        selectors = {
            "property_type_selector": "li.detailItem:has(span.label:-soup-contains(\"Natureza\")) span.value",
        }
        data = parse_listing_page(html, "https://example.com/p/4", selectors, "direct")
        assert data["property_type"] == "Apartamento"

    def test_direct_mode_extracts_summary_pairs_with_value_elements(self):
        html = """
        <html><body>
            <div class="summary">
                <ul>
                    <li><span class="name">Distrito</span><span class="value">Porto</span></li>
                    <li><span class="name">Concelho</span><span class="value">Porto</span></li>
                    <li><span class="name">Freguesia</span><span class="value">Bonfim</span></li>
                </ul>
            </div>
        </body></html>
        """
        selectors = {
            "summary_section": ".summary",
            "summary_item_selector": "li",
            "summary_label_selector": ".name",
            "summary_value_selector": ".value",
        }
        data = parse_listing_page(html, "https://example.com/p/4", selectors, "direct")
        assert data["district"] == "Porto"
        assert data["county"] == "Porto"
        assert data["parish"] == "Bonfim"

    def test_habinedita_like_detail_extracts_summary_areas_and_seo(self):
        html = """
        <html>
            <head>
                <title>Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HBMR11399V</title>
                <meta name="description" content="Moradia nova com garagem para 2 carros.">
            </head>
            <body>
                <h1 class="imovel-titulo">Moradia em Banda T3</h1>
                <div class="summary">
                    <ul class="bloco-dados">
                        <li><b>Objectivo</b> Venda</li>
                        <li><b>Estado</b> Novo</li>
                        <li><b>Tipo</b> Moradia em Banda</li>
                        <li><b>Tipologia</b> T3</li>
                        <li><b>Distrito</b> Porto</li>
                        <li><b>Concelho</b> Gondomar</li>
                        <li><b>Freguesia</b> Fânzeres e São Pedro da Cova</li>
                    </ul>
                </div>
                <div class="areas">
                    <div class="area"><span class="name">Área Bruta</span><span class="value">268 m²</span></div>
                    <div class="area"><span class="name">Área Útil</span><span class="value">185 m²</span></div>
                    <div class="area"><span class="name">Área Terreno</span><span class="value">185 m²</span></div>
                </div>
                <div class="descricao">Moradia nova com excelente exposição solar e garagem para dois carros.</div>
            </body>
        </html>
        """
        selectors = {
            "title_selector": "h1.imovel-titulo",
            "description_selector": ".descricao",
            "summary_section": ".summary",
            "summary_item_selector": "li",
            "summary_label_selector": "b",
            "areas_section": ".areas",
            "area_item_selector": ".area",
            "area_name_selector": ".name",
            "area_value_selector": ".value",
        }

        data = parse_listing_page(html, "https://example.com/p/5", selectors, "direct")

        assert data["title"] == "Moradia em Banda T3"
        assert data["business_type"] == "Venda"
        assert data["condition"] == "Novo"
        assert data["property_type"] == "Moradia em Banda"
        assert data["gross_area"] == "268 m²"
        assert data["useful_area"] == "185 m²"
        assert data["land_area"] == "185 m²"
        assert data["page_title"] == "Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HBMR11399V"
        assert data["meta_description"] == "Moradia nova com garagem para 2 carros."

    def test_habinedita_icon_block_fallback_extracts_missing_fields_and_multiple_features(self):
        html = """
        <html>
            <head>
                <title>Moradia em Banda T3 Porto Gondomar Venda 517.500 Ref. HBMR11399V</title>
                <meta name="description" content="Moradia nova com varanda e ar condicionado.">
            </head>
            <body>
                <h1 class="mbs_titulo">Moradia em Banda T3</h1>
                <span class="bnr_preco">517 500 €</span>
                <div class="summary">
                    <ul class="bloco-dados">
                        <li><b>Objectivo</b> Venda</li>
                        <li><b>Estado</b> Novo</li>
                        <li><b>Tipo</b> Moradia em Banda</li>
                        <li><b>Tipologia</b> T3</li>
                        <li><b>Distrito</b> Porto</li>
                        <li><b>Concelho</b> Gondomar</li>
                    </ul>
                </div>
                <div id="ContentPlaceHolder1_modulodadosicones_module_holder">
                    <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_quarto">3</span>
                    <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_wcs">5</span>
                    <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_area_bruta">268 m²</span>
                    <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_area_util">185 m²</span>
                    <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_area_terreno">185 m²</span>
                    <div id="ContentPlaceHolder1_modulodadosicones_div_certificacao">
                        <img src="/images/energy-a.png" alt="A">
                    </div>
                </div>
                <div id="ContentPlaceHolder1_div_imovel_descricao">
                    Moradia nova com garagem fechada, varanda soalheira, ar condicionado completo e certificação energética A.
                    Excelente exposição solar e acabamentos modernos para toda a família.
                </div>
            </body>
        </html>
        """
        selectors = {
            "title_selector": "h1.mbs_titulo",
            "price_selector": ".bnr_preco",
            "description_selector": "#ContentPlaceHolder1_div_imovel_descricao",
            "features_selector": "#ContentPlaceHolder1_div_imovel_descricao",
            "summary_section": ".summary",
            "summary_item_selector": "li",
            "summary_label_selector": "b",
        }

        data = parse_listing_page(html, "https://example.com/p/6", selectors, "direct")

        assert data["bedrooms"] == "3"
        assert data["bathrooms"] == "5"
        assert data["gross_area"] == "268 m²"
        assert data["useful_area"] == "185 m²"
        assert data["land_area"] == "185 m²"
        assert data["energy_certificate"] == "A"
        assert data["garage"] == "Yes"
        assert data["balcony"] == "Yes"
        assert data["air_conditioning"] == "Yes"
