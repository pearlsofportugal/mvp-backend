"""Tests for parser service — HTML parsing logic."""
import pytest

from app.services.parser_service import (
    parse_listing_links,
    parse_next_page,
    parse_listing_page,
    _parse_images,
    _parse_seo,
)


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
