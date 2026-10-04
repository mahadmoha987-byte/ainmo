"""Public Observatorio pages must remain factual, indexable and reproducible."""

from fastapi.testclient import TestClient

from api import app


client = TestClient(app)
SLUG = "ainmo-frente-concepto-urbanistico-cl-85"


def test_observatorio_index_lists_verified_case():
    response = client.get("/observatorio")
    assert response.status_code == 200
    assert "Observatorio Ainmo" in response.text
    assert f'/observatorio/{SLUG}' in response.text
    assert "Evidencia antes que promesas" in response.text


def test_observatorio_article_keeps_case_identity_and_scope():
    response = client.get(f"/observatorio/{SLUG}")
    assert response.status_code == 200
    for expected in (
        "CL 85 # 11-35",
        "Chapinero",
        "008310012021",
        "CU3-24-2182",
        "76,6 m²",
        "Distrito Creativo La 85",
        "Prefactibilidad, no concepto oficial ni licencia",
    ):
        assert expected in response.text
    assert 'rel="canonical" href="https://ainmo.uk/observatorio/' in response.text
    assert '"@type": "Article"' in response.text
    assert "/app?lat=4.667958459499999&amp;lng=-74.05200009700002" in response.text


def test_unknown_observatorio_article_is_not_indexable():
    response = client.get("/observatorio/no-existe")
    assert response.status_code == 404
    assert response.headers["x-robots-tag"] == "noindex"


def test_observatorio_is_in_sitemap():
    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert "https://ainmo.uk/observatorio</loc>" in response.text
    assert f"https://ainmo.uk/observatorio/{SLUG}</loc>" in response.text
