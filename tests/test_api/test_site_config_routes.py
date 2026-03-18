"""Tests for selector suggestion and preview routes."""

import pytest
from httpx import AsyncClient


async def test_site_config_suggest_returns_ranked_candidates(client: AsyncClient, monkeypatch):
    """POST /api/v1/sites/preview/selector-suggestions returns field candidates from HTML."""
    html = """
    <html>
      <head>
        <script type="application/ld+json">
          {
            "@type": "RealEstateListing",
            "name": "Moradia T3",
            "price": "250000 EUR",
            "address": {"addressLocality": "Lisboa"}
          }
        </script>
      </head>
      <body>
        <h1 class="titulo">Moradia T3</h1>
        <div class="price"><strong>250.000 €</strong></div>
        <div class="area">120 m2</div>
        <div class="quartos">3 quartos</div>
        <address>Lisboa</address>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post("/api/v1/sites/preview/selector-suggestions", json={"url": "https://example.pt/imoveis"})

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["source"] == "json-ld"
    assert body["data"]["candidates"]["price"]
    assert body["data"]["candidates"]["title"]
    assert body["data"]["candidates"]["county"]


async def test_site_config_preview_returns_empty_for_invalid_selector(client: AsyncClient, monkeypatch):
    """POST /api/v1/sites/preview/selector never raises on invalid selectors."""
    html = """
    <html>
      <body>
        <div class="price">250.000 €</div>
        <div class="price">180.000 €</div>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post(
        "/api/v1/sites/preview/selector",
        json={"url": "https://example.pt/imoveis", "selector": "div["},
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"matches": 0, "preview": []}


async def test_site_config_suggest_filters_generic_false_positives_and_finds_address_parts(client: AsyncClient, monkeypatch):
    """Suggestions should avoid generic UI labels and infer PT district/county rows."""
    html = """
    <html>
      <body>
        <div class="box-titulo">Favoritos</div>
        <div class="mbs_titulo">Moradia Isolada T5</div>
        <div class="bnr_preco">850.000 €</div>
        <div id="quarto">5</div>
        <div id="wcs">1</div>
        <ul class="summary">
          <li><b>Distrito</b> Aveiro</li>
          <li><b>Concelho</b> Ovar</li>
        </ul>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post("/api/v1/sites/preview/selector-suggestions", json={"url": "https://example.pt/imoveis"})

    assert response.status_code == 200
    data = response.json()["data"]
    price_selectors = [candidate["selector"] for candidate in data["candidates"]["price"]]
    title_samples = [candidate["sample"] for candidate in data["candidates"]["title"]]

    assert "div.bnr_preco" in price_selectors
    assert "#quarto" not in price_selectors
    assert "#wcs" not in price_selectors
    assert "Favoritos" not in title_samples
    assert data["candidates"]["district"]
    assert data["candidates"]["county"]
    assert data["candidates"]["district"][0]["sample"] == "Aveiro"
    assert data["candidates"]["county"][0]["sample"] == "Ovar"


async def test_site_config_suggest_extracts_structured_property_fields(client: AsyncClient, monkeypatch):
    """Suggestions should infer structured property metadata from summary rows and icon blocks."""
    html = """
    <html>
      <body>
        <ul class="summary">
          <li><b>Objectivo</b> Venda</li>
          <li><b>Estado</b> Usado</li>
          <li><b>Tipo</b> Moradia Isolada</li>
          <li><b>Tipologia</b> T5</li>
        </ul>
        <li id="icon-wcs" class="li-bloco-icones">
          <span class="icon_label">WC's</span>
          <span class="lbl_valor">3</span>
        </li>
        <li id="icon-terreno" class="li-bloco-icones">
          <span class="icon_label">Área Terreno</span>
          <span class="lbl_valor">9000 m²</span>
        </li>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post("/api/v1/sites/preview/selector-suggestions", json={"url": "https://example.pt/imoveis"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["candidates"]["business_type"][0]["sample"] == "Venda"
    assert data["candidates"]["condition"][0]["sample"] == "Usado"
    assert data["candidates"]["property_type"][0]["sample"] == "Moradia Isolada"
    assert data["candidates"]["typology"][0]["sample"] == "T5"
    assert data["candidates"]["bathrooms"][0]["sample"] == "3"
    assert data["candidates"]["bathrooms"][0]["selector"] == "#icon-wcs span.lbl_valor"
    assert data["candidates"]["land_area"][0]["sample"] == "9000 m²"
    assert data["candidates"]["land_area"][0]["selector"] == "#icon-terreno span.lbl_valor"


async def test_site_config_suggest_anchors_repeated_structured_value_selectors(client: AsyncClient, monkeypatch):
    """Suggestions should anchor repeated value selectors to a stable parent row."""
    html = """
    <html>
      <body>
        <div class="feature-row" id="business-row">
          <span class="label">Natureza</span>
          <span class="value-pill">Venda</span>
        </div>
        <div class="feature-row" id="type-row">
          <span class="label">Tipo</span>
          <span class="value-pill">Apartamento</span>
        </div>
        <div class="feature-row" id="district-row">
          <strong>Distrito</strong>: Braga
        </div>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post("/api/v1/sites/preview/selector-suggestions", json={"url": "https://example.pt/imoveis"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["candidates"]["business_type"][0]["sample"] == "Venda"
    assert data["candidates"]["business_type"][0]["selector"] == "#business-row span.value-pill"
    assert data["candidates"]["property_type"][0]["sample"] == "Apartamento"
    assert data["candidates"]["property_type"][0]["selector"] == "#type-row span.value-pill"
    assert data["candidates"]["district"][0]["sample"] == "Braga"


async def test_site_config_suggest_prefers_value_elements_and_filters_modal_titles(client: AsyncClient, monkeypatch):
    """Suggestions should prefer value selectors and reject modal/legal headings."""
    html = """
    <html>
      <body>
        <h1 class="left-section-heading">Apartamento T2 na Estrada Monumental, Funchal</h1>
        <h5 class="modal-title">Centros de Resolução de Litígios</h5>
        <div class="col-12 mb-2">
          <p class="info-item-label float-left">Natureza</p>
          <p class="info-item float-right cor-destaque natureza"><span>Venda</span></p>
        </div>
        <div class="col-12 mb-2">
          <p class="info-item-label float-left">Área Útil</p>
          <p class="info-item float-right cor-destaque notranslate"><span>92 m²</span></p>
        </div>
        <div class="col-12 mb-2">
          <p class="info-item-label float-left">Estado</p>
          <p class="info-item float-right cor-destaque"><span>Excelente</span></p>
        </div>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post("/api/v1/sites/preview/selector-suggestions", json={"url": "https://example.pt/imoveis"})

    assert response.status_code == 200
    data = response.json()["data"]
    title_samples = [candidate["sample"] for candidate in data["candidates"]["title"]]
    assert "Centros de Resolução de Litígios" not in title_samples
    assert data["candidates"]["business_type"][0]["sample"] == "Venda"
    assert data["candidates"]["business_type"][0]["selector"].startswith("p.info-item.float-right")
    assert data["candidates"]["area"][0]["sample"] == "92 m²"
    assert data["candidates"]["area"][0]["selector"].startswith("p.info-item.float-right")
    assert data["candidates"]["condition"][0]["sample"] == "Excelente"


async def test_site_config_suggest_prefers_specific_summary_rows_and_clean_title_for_habinedita(client: AsyncClient, monkeypatch):
    """Suggestions should avoid noisy lead text, prefer clean title, and anchor structured summary rows."""
    html = """
    <html>
      <body>
        <h1 id="ContentPlaceHolder1_h1_show_imovel">Moradia Isolada T5 Aveiro Ovar Venda 850.000 Ref. HBMR11466V</h1>
        <div class="mbs_titulo">Moradia Isolada T5</div>
        <span id="ContentPlaceHolder1_lbl_ang_nome">Clara Portela</span>
        <span id="ContentPlaceHolder1_modulocaracteristicas_lbl_caracteristicas_gerais">Gerais</span>
        <div id="ContentPlaceHolder1_modulopedidoinformacao_lbl_subtitulo">Para mais informações ou marcar uma visita</div>
        <div id="ContentPlaceHolder1_modulodadosresumidos_module_holder" class="modulo-dados-resumidos">
          <ul class="bloco-dados">
            <li><b>Objectivo</b> Venda</li>
            <li><b>Estado</b> Usado</li>
            <li><b>Tipo</b> Moradia Isolada</li>
            <li><b>Tipologia</b> T5</li>
            <li><b>Distrito</b> Aveiro</li>
            <li><b>Concelho</b> Ovar</li>
            <li><b>Freguesia</b> Ovar, São João, Arada e São Vicente de Pereira Jusã</li>
            <li><b>Zona</b> N/D</li>
          </ul>
        </div>
        <div class="div_imovel_descricao">
          <ul>
            <li>Áreas ideais para refeições ao ar livre, eventos ou convívio familiar.</li>
          </ul>
        </div>
        <div id="ContentPlaceHolder1_modulodadosicones_div_areas">
          <ul class="bloco-icones">
            <li id="ContentPlaceHolder1_modulodadosicones_li_area_bruta">
              <span class="icon_label">Área Bruta</span>
              <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_area_bruta">500 m²</span>
            </li>
            <li id="ContentPlaceHolder1_modulodadosicones_li_area">
              <span class="icon_label">Área Útil</span>
              <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_area">400 m²</span>
            </li>
            <li id="ContentPlaceHolder1_modulodadosicones_li_area_terreno">
              <span class="icon_label">Área Terreno</span>
              <span id="ContentPlaceHolder1_modulodadosicones_lbl_valor_area_terreno">9000 m²</span>
            </li>
          </ul>
        </div>
      </body>
    </html>
    """

    async def fake_get_cached_html(url: str, fetcher):
        return html

    monkeypatch.setattr("app.crawler.selector_suggester.get_cached_html", fake_get_cached_html)

    response = await client.post("/api/v1/sites/preview/selector-suggestions", json={"url": "https://example.pt/imoveis"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["candidates"]["title"][0]["sample"] == "Moradia Isolada T5"
    title_samples = [candidate["sample"] for candidate in data["candidates"]["title"]]
    assert "Para mais informações ou marcar uma visita" not in title_samples
    assert "Clara Portela" not in title_samples
    assert "Gerais" not in title_samples
    assert data["candidates"]["business_type"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(1)"
    assert data["candidates"]["condition"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(2)"
    assert data["candidates"]["property_type"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(3)"
    assert data["candidates"]["typology"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(4)"
    assert data["candidates"]["district"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(5)"
    assert data["candidates"]["county"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(6)"
    assert data["candidates"]["parish"][0]["selector"] == "#ContentPlaceHolder1_modulodadosresumidos_module_holder li:nth-of-type(7)"
    assert data["candidates"]["parish"][0]["sample"] == "Ovar, São João, Arada e São Vicente de Pereira Jusã"
    assert data["candidates"]["area"][0]["sample"] == "400 m²"
    area_samples = [candidate["sample"] for candidate in data["candidates"]["area"]]
    assert "Áreas ideais para refeições ao ar livre, eventos ou convívio familiar." not in area_samples
    assert data["candidates"]["land_area"][0]["sample"] == "9000 m²"
