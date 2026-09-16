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


def test_pdf_separates_chip_from_lot_and_preserves_searched_chip():
    calculated, lookup = _fixture_payload()
    calculated["lote"]["identidad_predial"] = {
        "codigo_lote": "009241036001",
        "chip_consultado": "AAA0002BBBB",
        "total_chips": 2,
        "chips": [
            {"chip": "AAA0001AAAA", "direccion": "CL 1 1 01", "lotcodigo": "009241036001"},
            {"chip": "AAA0002BBBB", "direccion": "CL 1 1 02", "lotcodigo": "009241036001"},
        ],
        "estado": "resuelto",
    }
    html = pdf_report.generate_html_preview(calculated, lookup, "CL 1 # 1-02")
    assert "Código de lote (LOTCODIGO)" in html
    assert "Identificación predial (CHIP)" in html
    assert "Informe generado para CHIP:" in html
    assert "AAA0002BBBB" in html
    assert "009241036001" in html
    assert "CHIP / código de lote" not in html
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


def test_pdf_source_register_tracks_the_actual_treatment():
    calculated, lookup = _fixture_payload()
    calculated["tratamiento"] = lookup["tratamiento"] = "RENOVACION"
    html = pdf_report.generate_html_preview(calculated, lookup, "Predio Bogotá")
    assert "Art. 304 (tratamiento de Renovación Urbana)" in html
    assert "Art. 310 (tratamiento de Consolidación)" not in html


def test_pdf_stops_profile_and_units_for_out_of_range_area():
    calculated, lookup = _fixture_payload()
    calculated["metrics"]["area_construible_max_m2"] = {
        "valor": None, "estado": "no_aplica", "motivo": "IC resultante."
    }
    calculated["metrics"]["area_construible_estimada"] = {
        "valor_m2": 2_714_320,
        "estado": "requiere_concepto",
        "fuera_de_rango": True,
        "motivo": "El lote supera el rango del modelo simplificado.",
    }
    html = pdf_report.generate_html_preview(calculated, lookup, "Predio fuera de rango")
    assert "Perfil no generado" in html
    assert "El lote supera el rango del modelo simplificado" in html
    assert "2.714.320" not in html


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


def test_property_identity_keeps_chip_separate_from_lot_and_detects_ph(monkeypatch):
    calculated, lookup = _fixture_payload()
    monkeypatch.setattr(api.geocode, "catastro_properties_for_lot", lambda _code: [
        {"chip": "AAA0001AAAA", "direccion": "CL 1 1 01", "lotcodigo": "009241036001"},
        {"chip": "AAA0002BBBB", "direccion": "CL 1 1 02", "lotcodigo": "009241036001"},
    ])
    asyncio.run(api._attach_property_identity(
        calculated, lookup, searched_chip="CHIP AAA0002BBBB",
    ))
    identity = calculated["lote"]["identidad_predial"]
    assert identity["codigo_lote"] == "009241036001"
    assert identity["chip_consultado"] == "AAA0002BBBB"
    assert identity["total_chips"] == 2
    assert identity["multiples_unidades_prediales"] is True
    assert [item["chip"] for item in identity["chips"]] == ["AAA0001AAAA", "AAA0002BBBB"]


def test_property_identity_rejects_chip_from_another_lot(monkeypatch):
    calculated, lookup = _fixture_payload()
    monkeypatch.setattr(api.geocode, "catastro_properties_for_lot", lambda _code: [
        {"chip": "AAA0001AAAA", "direccion": "CL 1 1 01", "lotcodigo": "009241036001"},
    ])
    try:
        asyncio.run(api._attach_property_identity(
            calculated, lookup, searched_chip="AAA0002BBBB",
        ))
    except api.ChipLotMismatchError as exc:
        assert exc.chip == "AAA0002BBBB"
        assert exc.lotcodigo == "009241036001"
    else:
        raise AssertionError("a mismatched CHIP must never be attached to a different lot")


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


def test_pdf_humanizes_nulls_no_aplica_and_internal_trace_names():
    calculated, lookup = _fixture_payload()
    calculated['metrics']['aislamiento_lateral_m'] = {
        'valor': 0,
        'estado': 'no_aplica',
        'motivo': 'No se exige aislamiento lateral en este escenario.',
    }
    calculated['formula_trace'] = [{
        'paso': 1,
        'descripcion': 'Área de referencia',
        'expresion': 'area_construible_max_m2 = anu_m2_supplied',
        'resultado': None,
        'nota': 'tabla_aisl_posterior no aplica',
        'estado': 'no_aplica',
    }, {
        'paso': 2,
        'descripcion': 'Aislamiento lateral',
        'expresion': 'tabla_aisl_posterior[pisos_max]',
        'resultado': 0,
        'unidad': 'm',
        'nota': 'No exigido',
        'estado': 'no_aplica',
    }, {
        'paso': 3,
        'descripcion': 'Estacionamientos',
        'expresion': 'min = 8% × B',
        'valores': {'area_actividad': 'AAPRSU', 'B_proxy_m2': None},
        'resultado': None,
        'nota': 'área_construible_max_m2 no disponible',
        'estado': 'insuficiente',
    }]
    html = pdf_report.generate_html_preview(calculated, lookup, 'Predio Bogotá')
    assert 'No exigido' in html
    assert 'area_construible_max_m2' not in html
    assert 'anu_m2_supplied' not in html
    assert 'tabla_aisl_posterior' not in html
    assert '>None<' not in html
    assert '= 0 m' not in html
    assert 'área_construible_max_m2' not in html
    assert 'Pisos ref' not in html
