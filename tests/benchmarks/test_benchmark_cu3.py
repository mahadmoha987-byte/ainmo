"""Regression benchmark for official concept CU3-24-2182."""
from __future__ import annotations

import io
import json
from pathlib import Path

from pypdf import PdfReader

import pdf_report
from calc import (
    _PARKING_AREA_ACTIVIDAD_LABELS,
    _calc_cargas_ru,
    _ru_lookup_aisl_post,
    calculate,
    get_usos_suelo,
)

EXPECTED = json.loads((Path(__file__).with_name("cu3_24_2182.json")).read_text())["expected"]


def mock_lookup() -> dict:
    return {
        "input": {"lng": -74.05200009700002, "lat": 4.667958459499999, "vis_en_sitio": False},
        "lote": {"lotcodigo": "008310012021", "area_m2": {"valor": 300.5, "confianza": "alta"}, "geojson_polygon": None},
        "consulta": {"fecha": "2026-09-19", "decreto_version": "D.555/2021"},
        "tratamiento": "RENOVACION",
        "area_actividad": {"codigo": "AAGSM", "nombre": "Grandes Servicios Metropolitanos", "es_receptora_vis": False},
        "edificabilidad": None,
        "antejardin": None,
        "rango": None,
        "ancho_via_gis": {"D_m": 16.85, "n_calzadas": 1, "confianza": "media", "fuente": "Capa 38"},
        "restricciones_bloqueantes": [],
        "warnings": [],
        "hallazgos_cartograficos": {
            "sector_consolidado": {"aplica": True, "estado": "resuelto"},
            "area_desarrollo_naranja": {"aplica": True, "estado": "resuelto", "hallazgo": "Distrito Creativo La 85"},
            "reserva_vial": {"aplica": True, "estado": "resuelto", "area_reserva_m2": 76.6, "porcentaje_lote": 25.5},
            "proteccion_bic_100m": {"aplica": True, "estado": "requiere_concepto"},
        },
    }


def test_official_concept_core_values_and_labels():
    result = calculate(mock_lookup())
    assert result["tratamiento"] == EXPECTED["tratamiento"]
    assert _PARKING_AREA_ACTIVIDAD_LABELS["AAGSM"] == EXPECTED["area_actividad_label"]
    assert result["antejardin"]["exigido"] is EXPECTED["antejardin_exigido"]
    assert result["metrics"]["area_construible_max_sin_manzana_completa_m2"]["ice_max"] == EXPECTED["ice_max_sin_manzana"]
    assert result["parking"]["min_pct"] == EXPECTED["parking_min_pct"]
    assert result["parking"]["max_pct"] == EXPECTED["parking_max_pct"]
    assert result["parking"]["adicional_pct"] == EXPECTED["parking_adicional_pct"]
    assert _ru_lookup_aisl_post(12.0) == EXPECTED["aislamiento_posterior_hasta_12m"]
    assert result["metrics"]["aislamiento_lateral_umbral_m"]["valor"] == EXPECTED["aislamiento_lateral_umbral_m"]


def test_official_concept_overlay_expectations_are_preserved():
    findings = calculate(mock_lookup())["hallazgos_cartograficos"]
    assert findings["sector_consolidado"]["aplica"] is EXPECTED["sector_consolidado"]
    assert EXPECTED["area_desarrollo_naranja"] in findings["area_desarrollo_naranja"]["hallazgo"]
    assert findings["reserva_vial"]["aplica"] is EXPECTED["reserva_vial_parcial"]
    assert 0 < findings["reserva_vial"]["area_reserva_m2"] < 300.5
    assert findings["proteccion_bic_100m"]["aplica"] is EXPECTED["proteccion_bic_100m"]


def test_official_concept_uses_and_loads():
    uses = get_usos_suelo("AAGSM")["tabla"]
    assert uses["residencial"]["multifamiliar_colectiva"]["status"] == EXPECTED["usos"]["multifamiliar_colectiva"]
    assert uses["comercio_servicios"]["comercios_basicos_tipo_3_mayor_4000m2"]["status"] == EXPECTED["usos"]["comercios_basicos_tipo_3_mayor_4000m2"]
    assert uses["industrial"]["industria_pesada"]["status"] == EXPECTED["usos"]["industria_pesada"]
    assert uses["industrial"]["industria_liviana"]["status"] == EXPECTED["usos"]["industria_liviana"]
    loads = _calc_cargas_ru(300.5, 5.0, "2026-09-19")
    assert round(loads["cesion_suelo_m2"] / 300.5 * 100) == EXPECTED["cargas_ice5_cs_pct"]
    assert "UAECD" in loads["nota_vref"]


def test_facade_height_uses_canonical_name_without_losing_legacy_alias():
    metrics = calculate(mock_lookup())["metrics"]
    assert metrics["altura_limite_fachada_A_m"]["valor"] == 42.12
    assert metrics["retroceso_fachada_A_m"]["alias_de"] == "altura_limite_fachada_A_m"
    assert metrics["altura_limite_fachada_A_m"]["nota"].startswith("A es una altura")


def test_pdf_page_one_identifies_the_benchmarked_cl85_lot():
    """Prevent a stale or mismatched property from passing as this report."""
    lookup = mock_lookup()
    lookup["localidad"] = "Chapinero"
    lookup["hallazgos_cartograficos"]["altura_aeronautica"] = {
        "aplica": True,
        "estado": "resuelto",
        "etiqueta": "Altura aeronáutica",
        "hallazgo": "Altura de referencia aeronáutica: 109 m.",
    }
    result = calculate(lookup)
    pdf_bytes = pdf_report.generate_pdf(result, lookup, "CL 85 # 11-35")
    page_one = PdfReader(io.BytesIO(pdf_bytes)).pages[0].extract_text()

    assert "008310012021" in page_one
    assert "CL 85" in page_one
    assert "Chapinero" in page_one


def test_pdf_ru_cabida_uses_numeric_ice5_headline():
    """RU must headline the Art. 304 ICe area, never the generic IC gap."""
    lookup = mock_lookup()
    lookup["localidad"] = "Chapinero"
    result = calculate(lookup)
    pdf_bytes = pdf_report.generate_pdf(result, lookup, "CL 85 # 11-35")
    pages = [page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages]
    cabida_page = next(text for text in pages if "CABIDA PRELIMINAR" in text)

    assert "1.502,5" in cabida_page
    assert "No calculable" not in cabida_page
