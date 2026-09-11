"""Tests for the eGO Real Estate platform extractor."""
from app.services.ego_extract_service import (
    ego_source_partner,
    extract_ego_platform,
    looks_like_ego_platform,
)
from app.services.mapper_service import normalize_ego_platform_payload


_EGO_PAGE = """
<html><head>
  <title>Moradia T3 Exclusiva No Centro de Sintra</title>
  <meta name="description" content="No coração de Sintra, uma moradia T3.">
  <script src="//static.egorealestate.com/egoforge/websiteeditor/scripts/x.js"></script>
</head><body>
  <div class="propertyTitle"><h1>Moradia T3 Exclusiva No Centro de Sintra</h1></div>
  <div class="propertyDetails">
    <h2>Detalhes do Imóvel</h2>
    <ul>
      <li class="detailItem"><span class="label">Distrito</span><h4><span class="value">Lisboa</span></h4></li>
      <li class="detailItem"><span class="label">Concelho</span><h4><span class="value">Sintra</span></h4></li>
      <li class="detailItem"><span class="label">Freguesia</span><h4><span class="value">S.Maria e S.Miguel</span></h4></li>
      <li class="detailItem"><span class="label">Estado</span><h4><span class="value">Usado</span></h4></li>
      <li class="detailItem"><span class="label">Referência</span><h4><span class="value">Ref. GE-MC100292-2</span></h4></li>
      <li class="detailItem"><span class="label">Natureza</span><h4><span class="value">Moradia</span></h4></li>
      <li class="detailItem"><span class="label">Tipologia</span><h4><span class="value">T3 Triplex</span></h4></li>
      <li class="detailItem"><span class="label">Área útil</span><h4><span class="value">224 m²</span></h4></li>
      <li class="detailItem"><span class="label">Área Bruta</span><h4><span class="value">336 m²</span></h4></li>
      <li class="detailItem"><span class="label">Área do terreno</span><h4><span class="value">408 m²</span></h4></li>
      <li class="detailItem"><span class="label">Ano construção</span><h4><span class="value">1980</span></h4></li>
      <li class="detailItem"><span class="label">Categoria Energética</span><h4><span class="value"><i class="energyClass E"></i></span></h4></li>
      <li class="detailItem"><span class="label">Venda</span><h4><span class="value">1 300 000 €</span></h4></li>
    </ul>
  </div>
  <div class="characteristics">
    <ul>
      <li class="detailItem"><span class="label">Total quarto(s)</span><span class="value">3</span></li>
      <li class="detailItem"><i class="egoiconfont egoiconfont-chuveiro"></i><span class="value">2</span></li>
    </ul>
  </div>
  <p class="specsText">No coração de Sintra, uma moradia exclusiva com jardim privativo.</p>
  <div class="agentBox"><span class="agentName">Maria do Céu Pinto</span>
    <a href="tel:969363823">969363823</a></div>
  <img class="Streamed" data-imgthumb="https://images.egorealestate.com/Z320x240/S5/C1/P30297842/Tphoto/IDaaaa1111.jpg">
  <img class="Streamed" data-imgthumb="https://images.egorealestate.com/Z1280x960/S5/C1/P30297842/Tphoto/IDaaaa1111.jpg">
  <img class="Streamed" data-imgthumb="https://images.egorealestate.com/Z320x240/S5/C1/P30297842/Tphoto/IDbbbb2222.jpg">
  <img class="Streamed" data-imgthumb="https://images.egorealestate.com/Z320x240/S5/C1/P99999999/Tphoto/IDcccc3333.jpg">
  <img class="Streamed" src="https://media.egorealestate.com/SysV4Websites/logo.svg">
</body></html>
"""


def test_detection_positive_and_negative():
    assert looks_like_ego_platform(_EGO_PAGE) is True
    assert looks_like_ego_platform("<html><body>plain site</body></html>") is False


def test_source_partner_slug():
    assert ego_source_partner("https://www.goldempire.pt/imovel/x/1") == "ego_goldempire_pt"


def test_extract_all_detail_fields():
    raw = extract_ego_platform(_EGO_PAGE, "https://www.goldempire.pt/imovel/x/25962184")
    assert raw["title"] == "Moradia T3 Exclusiva No Centro de Sintra"
    assert raw["district"] == "Lisboa"
    assert raw["county"] == "Sintra"
    assert raw["parish"] == "S.Maria e S.Miguel"
    assert raw["condition"] == "Usado"
    assert raw["property_id"] == "GE-MC100292-2"  # "Ref. " prefix stripped
    assert raw["property_type"] == "Moradia"
    assert raw["typology"] == "T3 Triplex"
    assert raw["useful_area"] == "224 m²"
    assert raw["gross_area"] == "336 m²"
    assert raw["land_area"] == "408 m²"
    assert raw["construction_year"] == "1980"
    assert raw["energy_certificate"] == "E"
    assert raw["price"] == "1 300 000 €"
    assert raw["business_type"] == "sale"
    assert raw["bathrooms"] == "2"
    assert raw["advertiser"] == "Maria do Céu Pinto"
    assert raw["contacts"] == "969363823"
    assert "jardim privativo" in raw["raw_description"]


def test_gallery_dedupes_and_filters_foreign_and_logo():
    raw = extract_ego_platform(_EGO_PAGE, "https://www.goldempire.pt/imovel/x/1")
    imgs = raw["images"]
    # one URL per photo id, biggest variant, only the dominant property number,
    # SVG logo dropped
    assert len(imgs) == 2
    assert all("P30297842" in u for u in imgs)
    assert all("Z1280x960" in u for u in imgs)
    assert not any(u.endswith(".svg") for u in imgs)


def test_feeds_ego_normalizer_end_to_end():
    raw = extract_ego_platform(_EGO_PAGE, "https://www.goldempire.pt/imovel/x/1")
    schema = normalize_ego_platform_payload(raw, ego_source_partner("https://www.goldempire.pt/x/1"))
    assert schema.title.startswith("Moradia T3")
    assert schema.price.amount == 1300000.0
    assert schema.area_useful_m2 == 224.0
    assert schema.area_gross_m2 == 336.0
    assert schema.area_land_m2 == 408.0
    assert schema.construction_year == 1980
    assert schema.bedrooms == 3  # from "T3 Triplex"
    assert schema.bathrooms == 2
    assert schema.energy_certificate == "E"
    assert schema.address.region == "Lisboa"
    assert schema.advertiser == "Maria do Céu Pinto"
    assert schema.contacts == "969363823"
    assert schema.source_partner == "ego_goldempire_pt"
