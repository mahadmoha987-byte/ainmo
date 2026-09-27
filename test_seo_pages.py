import json
import re
import xml.etree.ElementTree as ET

from fastapi.testclient import TestClient

from api import app
import seo_pages


client = TestClient(app)


def test_all_curated_pages_render_server_side_with_core_seo_fields():
    for page in seo_pages.PAGES.values():
        response = client.get(page["path"])
        assert response.status_code == 200, page["path"]
        assert page["title"] in response.text
        assert f'<link rel="canonical" href="{seo_pages.SITE_URL}{page["path"]}">' in response.text
        assert 'name="description"' in response.text
        assert 'name="q"' in response.text
        assert "Analizar gratis" in response.text
        assert "Prefactibilidad urbanística" in response.text


def test_structured_data_matches_visible_faqs():
    page = seo_pages.get_page("localidad", "chapinero")
    response = client.get(page["path"])
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', response.text, re.S)
    assert match
    payload = json.loads(match.group(1))
    graph = payload["@graph"]
    faq = next(item for item in graph if item["@type"] == "FAQPage")
    assert len(faq["mainEntity"]) == len(page["faqs"])
    for item in page["faqs"]:
        assert item["question"] in response.text


def test_unknown_seo_slug_is_not_indexable():
    response = client.get("/bogota/pagina-inventada")
    assert response.status_code == 404
    assert response.headers["x-robots-tag"] == "noindex"

    sector_response = client.get("/bogota/chapinero/sector-inventado")
    assert sector_response.status_code == 404
    assert sector_response.headers["x-robots-tag"] == "noindex"


def test_sitemap_lists_every_curated_page_once():
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(response.text)
    locations = [node.text for node in root.findall("{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
    for page in seo_pages.PAGES.values():
        url = f"{seo_pages.SITE_URL}{page['path']}"
        assert locations.count(url) == 1


def test_robots_points_to_sitemap_and_blocks_api_crawling():
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /api/" in response.text
    assert f"Sitemap: {seo_pages.SITE_URL}/sitemap.xml" in response.text


def test_index_pages_link_to_the_curated_library():
    for kind, route in (("localidad", "/bogota"), ("guia", "/guias")):
        response = client.get(route)
        assert response.status_code == 200
        for page in seo_pages.pages_for_kind(kind):
            assert f'href="{page["path"]}"' in response.text

    response = client.get("/bogota")
    for page in seo_pages.pages_for_kind("sector"):
        assert f'href="{page["path"]}"' in response.text


def test_catalog_contains_all_localities_and_only_reviewed_sectors():
    assert len(seo_pages.pages_for_kind("localidad")) == 20
    assert len(seo_pages.pages_for_kind("sector")) == 16
    assert seo_pages.get_page("sector", "chapinero/quinta-camacho") is not None
