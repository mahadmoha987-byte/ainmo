from fastapi.testclient import TestClient

from api import app


client = TestClient(app)


def test_company_page_is_public_and_canonical():
    response = client.get("/empresa")
    assert response.status_code == 200
    assert 'lang="es-CO"' in response.text
    assert '<link rel="canonical" href="https://ainmo.uk/empresa">' in response.text
    assert "Prefactibilidad, no determinación oficial" in response.text
    assert "Mahad Mohamed" in response.text


def test_company_page_is_in_sitemap():
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "<loc>https://ainmo.uk/empresa</loc>" in response.text
