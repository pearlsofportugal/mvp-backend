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
            <div class="desc">A great place to live.</div>
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
        assert data["raw_description"] == "A great place to live."
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
