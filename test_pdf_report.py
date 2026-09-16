import asyncio

import api
import pdf_report


def _fixture_payload():
    calculated = {
        "input": {"lat": 4.745, "lng": -74.085, "vis_en_sitio": False},
        "lote": {
            "lotcodigo": "009241036001",
            "area_m2": {"valor": 7771.7, "confianza": "alta"},
            "unidades_predio": 197,
        },
        "tratamiento": "CONSOLIDACION",
        "binding_constraint": "height",
        "metrics": {
            "altura_base_pisos": {"valor": 5, "estado": "resuelto", "fuente": "Layer 15"},
            "retroceso_fachada_A_m": {
                "valor": 42.12,
                "D_m": 16.848,
                "factor": 2.5,
                "estado": "resuelto",
                "confianza": "media",
                "fuente_D": "Layer 38 POT FeatureServer (Calzada)",
            },
            "aislamiento_posterior_m": {"valor": 5, "estado": "resuelto"},
            "aislamiento_lateral_m": {"valor": 4, "estado": "derivado"},
            "area_construible_max_m2": {"valor": None, "estado": "no_aplica"},
            "planta_maxima_m2": {"valor": None, "estado": "no_aplica"},
            "area_construible_estimada": {
                "valor_m2": 29332.5,
                "estado": "derivado",
                "advertencia": "Estimación derivada.",
                "fuentes": [],
            },
        },
        "antejardin": {"dimension_m": 5, "fuente": "Layer 22"},
        "parking": {"min_pct": 5, "max_pct": 20, "adicional_pct": 10},
        "formula_trace": [],
        "warnings": [],
    }
    # Deliberately stale/worse raw snapshot. The PDF must use metrics above.
    lookup = {
        "input": {"lat": 4.745, "lng": -74.085, "vis_en_sitio": False},
        "lote": calculated["lote"],
        "tratamiento": "CONSOLIDACION",
        "tipologia": {"valor": "AISLADA"},
        "ancho_via_gis": {"D_m": None, "fuente": "sin_dato"},
        "area_actividad": {"codigo": "AAPRSU", "nombre": "Proximidad"},
        "edificabilidad": {
            "altura_maxima": {"pisos_min": 5, "pisos_max": 5},
            "indice_construccion": {"valor": None},
            "indice_ocupacion": {"valor": None},
        },
    }
    return calculated, lookup


def test_pdf_uses_calculated_road_width_and_retroceso():
    calculated, lookup = _fixture_payload()
    html = pdf_report.generate_html_preview(calculated, lookup, "Predio Suba")
    assert "42,12 m" in html
    assert "16,85 m" in html
    assert "Sin dato (D no encontrado)" not in html
    assert "Normativa aplicable - exigencia vs. resultado" in html
    assert html.index("Tabla de contenido") < html.index("Resumen del predio")
    assert html.index("Fuentes consultadas") < html.index("Trazabilidad del cálculo")


def test_pdf_discloses_coordinate_lookup_existing_units_and_facade_height():
    calculated, lookup = _fixture_payload()
    html = pdf_report.generate_html_preview(
        calculated, lookup, "Predio 009241036001 · Suba, Bogotá D.C."
    )
    assert "Consultado por coordenada; sin dirección catastral asociada" in html
    assert "Este lote registra 197 unidades prediales" in html
    assert "Supuesto de sitio libre" in html
    assert "Altura máxima de fachada" in html
    assert "no es un retiro horizontal" in html
    assert "Retroceso de fachada</strong>" not in html


def test_pdf_humanizes_restriction_warning_and_groups_sources():
    calculated, lookup = _fixture_payload()
    calculated["warnings"] = [
        "SIN_DATO DE RESTRICCIONES: aerocivil, cerros_orientales, "
        "movimientos_en_masa, inundacion, ronda_hidrica"
    ]
    html = pdf_report.generate_html_preview(calculated, lookup, "Predio Suba")
    assert "SIN DATO DE RESTRICCIONES" in html
    assert "restricciones aeronáuticas" in html
    assert "remoción en masa" in html
    assert "ronda hídrica" in html
    assert "cerros_orientales" not in html
    assert "<h3>Capas GIS</h3>" in html
    assert "<h3>Decretos y artículos</h3>" in html


def test_pdf_uses_resolved_near_match_address_and_discloses_substitution():
    calculated, lookup = _fixture_payload()
    calculated["direccion"] = "KR 7 # 32-12"
    calculated["address_resolution"] = {
        "near_match": True,
        "searched_address": "KR 7 # 32-16",
        "resolved_address": "KR 7 # 32-12",
        "relation": "mismo bloque",
    }
    html = pdf_report.generate_html_preview(calculated, lookup, "KR 7 # 32-12")
    assert "<h1>KR 7 # 32-12</h1>" in html
    assert "Buscó KR 7 # 32-16." in html
    assert "Analizando <strong>KR 7 # 32-12</strong> (mismo bloque)." in html


def test_address_identity_helper_titles_near_match_with_real_plate():
    result = {}
    api._apply_address_identity(
        result,
        address="KR 7 # 32-12",
        searched_address="KR 7 # 32-16",
        resolved_address="KR 7 # 32-12",
        near_match=True,
    )
    assert result["direccion"] == "KR 7 # 32-12"
    assert result["address_resolution"]["searched_address"] == "KR 7 # 32-16"
    assert result["address_resolution"]["relation"] == "mismo bloque"


def test_json_and_pdf_routes_share_calculation_helper(monkeypatch):
    calculated, lookup = _fixture_payload()
    monkeypatch.setattr(api, "_gis_lookup_cached", lambda *_args: lookup)
    monkeypatch.setattr(api.calc, "calculate", lambda lu, **_kwargs: calculated)
    resolved_lookup, result = asyncio.run(api._calculate_current_lot(
        lng=-74.085,
        lat=4.745,
        vis_en_sitio=False,
        anu_m2=None,
        frente_m=None,
        ancho_via_m=None,
        expected_lotcodigo="009241036001",
    ))
    assert resolved_lookup is lookup
    assert result is calculated
