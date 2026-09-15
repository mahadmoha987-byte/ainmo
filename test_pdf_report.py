import asyncio

import api
import pdf_report


def _fixture_payload():
    calculated = {
        "input": {"lat": 4.745, "lng": -74.085, "vis_en_sitio": False},
        "lote": {
            "lotcodigo": "009241036001",
            "area_m2": {"valor": 7771.7, "confianza": "alta"},
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

