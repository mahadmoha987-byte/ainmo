import asyncio

import api
import calc
import pytest


KENNEDY_RING = [[
    [-74.14994017700002, 4.6279735040000105], [-74.14997669000002, 4.627933512000027],
    [-74.14999735200001, 4.627952365999988], [-74.15000135100001, 4.627956014999995],
    [-74.15003667600001, 4.627988247000019], [-74.15005200600001, 4.628002234000007],
    [-74.15005867100001, 4.628008314999988], [-74.15007866600001, 4.628026561000013],
    [-74.15004215200003, 4.6280665529999965], [-74.15001549099998, 4.628042225999991],
    [-74.14996483800002, 4.627996005999989], [-74.14996083900002, 4.627992356999982],
    [-74.14994017700002, 4.6279735040000105],
]]

SUBA_RING = [[
    [-74.08283610000001, 4.745004345999973], [-74.08286459599998, 4.74486256900002],
    [-74.08295399399998, 4.744887749999975], [-74.08323300900003, 4.744976166000015],
    [-74.08320454900002, 4.745117950999997], [-74.083120318, 4.745091979999984],
    [-74.08309448, 4.745084012999996], [-74.08298165399998, 4.7450492250000025],
    [-74.082954094, 4.745040727000003], [-74.08283610000001, 4.745004345999973],
]]

USAQUEN_RING = [[
    [-74.03611163, 4.704852412], [-74.036080256, 4.704691272],
    [-74.036079289, 4.704679889], [-74.036085268, 4.704658238],
    [-74.036101933, 4.704645061], [-74.036117974, 4.704643892],
    [-74.036294361, 4.704684439], [-74.036264432, 4.704797849],
    [-74.036240595, 4.70488551], [-74.03611163, 4.704852412],
]]


def _lookup(code, area, ring, lng, lat, *, snap=False):
    lot = {"lotcodigo": code, "area_m2": {"valor": area}, "geojson_polygon": ring}
    if snap:
        lot["consulta_catastro"] = {"metodo": "predio_mas_cercano", "distancia_m": 4.2}
    return {
        "input": {"lng": lng, "lat": lat, "vis_en_sitio": False},
        "consulta": {"fecha": "2026-09-12"},
        "lote": lot,
    }


def _metrics(floors, posterior, *, retro_confidence="alta"):
    return {
        "area_construible_max_m2": {"valor": None},
        "altura_base_pisos": {
            "valor": floors,
            "confianza": "alta",
            "fuente": "Layer 15 campo ALTURA_MAXIMA",
            "articulo": "Art. 310 Decreto 555/2021",
        },
        "aislamiento_posterior_m": {"valor": posterior, "confianza": "alta"},
        "aislamiento_lateral_m": {"valor": 0, "confianza": "alta"},
        "retroceso_fachada_A_m": {"valor": 40, "confianza": retro_confidence},
    }


def _derive(lookup, metrics, antejardin):
    counter = [0]

    def next_step():
        counter[0] += 1
        return counter[0]

    return calc._derive_consolidacion_area(lookup, metrics, antejardin, [], next_step)


def test_live_fixture_kennedy_derives_area_but_keeps_regulatory_max_null():
    lookup = _lookup("004514061017", 91.6, KENNEDY_RING, -74.1499994, 4.628)
    metrics = _metrics(5, 5, retro_confidence="media")
    estimate = _derive(lookup, metrics, {"dimension_m": 3.5, "confianza": "alta"})

    assert metrics["area_construible_max_m2"]["valor"] is None
    assert estimate["metodo"] == "huella_x_pisos"
    assert estimate["valor_m2"] == 204.0
    assert estimate["entradas"]["huella_calculada"] == 40.8
    assert estimate["valor_m2"] == estimate["entradas"]["huella_calculada"] * 5
    assert "no fija IC/IO" in estimate["advertencia"]


def test_live_fixture_suba_missing_antejardin_returns_explicit_range():
    lookup = _lookup("009212014064", 685.1, SUBA_RING, -74.08303, 4.74499)
    estimate = _derive(lookup, _metrics(3, 5), {"dimension_m": None, "confianza": "requiere_input"})

    assert estimate["valor_m2"] is None
    assert estimate["rango_m2"][0] < estimate["rango_m2"][1]
    assert estimate["rango_m2"] == pytest.approx([
        value * estimate["entradas"]["pisos"]
        for value in estimate["entradas"]["rango_huella_m2"]
    ])
    assert estimate["confianza"] == "baja"
    assert any("5,0 m" in item and "no es una dimensión normativa" in item for item in estimate["supuestos"])


def test_live_fixture_usaquen_snap_forces_low_confidence():
    lookup = _lookup("008403014001", 450.8, USAQUEN_RING, -74.03618, 4.70476, snap=True)
    estimate = _derive(lookup, _metrics(3, 4), {"dimension_m": 0, "confianza": "alta"})

    assert estimate["valor_m2"] is not None
    assert estimate["confianza"] == "baja"
    assert any("snap" in item for item in estimate["supuestos"])


def test_large_irregular_consolidacion_polygon_keeps_a_positive_footprint():
    """Regression: cadastral bends must not be treated as extra façades."""
    lon0, lat0 = -74.0850, 4.7450
    meters = [
        (0, 0), (25, 0), (50, 1), (75, 0), (100, 0),
        (100, 30), (82, 30), (82, 55), (100, 55), (100, 80),
        (72, 80), (48, 79), (20, 80), (0, 80),
        (0, 52), (12, 52), (12, 28), (0, 28), (0, 0),
    ]
    cos_lat = __import__('math').cos(__import__('math').radians(lat0))
    ring = [[[
        lon0 + x / (111_319.49 * cos_lat),
        lat0 + y / 111_319.49,
    ] for x, y in meters]]
    lookup = _lookup("009241036001", 7771.7, ring, lon0, lat0)
    footprint, meta = calc._footprint_from_polygon(
        lookup, antejardin_m=5, posterior_m=5, lateral_m=4,
    )

    assert footprint is not None
    assert footprint > 0
    assert footprint < 7771.7
    assert meta["clipped_area_local_m2"] > 0


def test_megalot_is_stopped_before_area_units_or_financial_derivation():
    """Lots over one hectare require master-planning, not huella × pisos."""
    lookup = _lookup("MEGALOTE001", 547_000.0, KENNEDY_RING, -74.15, 4.55)
    estimate = _derive(lookup, _metrics(5, 5), {"dimension_m": 0, "confianza": "alta"})

    assert estimate["valor_m2"] is None
    assert estimate["rango_m2"] is None
    assert estimate["estado"] == "requiere_concepto"
    assert estimate["fuera_de_rango"] is True
    assert estimate["limite_modelo_m2"] == 10_000.0
    assert estimate["entradas"]["huella_calculada"] is None
    assert "10.000 m²" in estimate["motivo"]
    assert "unidades ni valor residual" in estimate["advertencia"]


def test_live_fixture_chapinero_renovacion_ice_is_unchanged():
    counter = [0]

    def next_step():
        counter[0] += 1
        return counter[0]

    metrics, binding = calc._calc_renovacion_urbana(
        {"lote": {"area_m2": {"valor": 87.7}}, "consulta": {"fecha": "2026-09-12"}},
        87.7,
        [],
        next_step,
    )
    assert binding == "total_area"
    assert metrics["area_construible_max_sin_manzana_completa_m2"]["valor"] == 438.5
    assert "area_construible_estimada" not in metrics
    assert metrics["altura_maxima_pisos"]["valor"] is None
    assert metrics["altura_maxima_pisos"]["articulo"]


def test_live_fixture_candelaria_conservacion_remains_blocked_without_estimate():
    metrics, binding = calc._calc_conservacion(
        {"lote": {"lotcodigo": "003106012001", "area_m2": {"valor": 9824.6}}},
        [],
        lambda: 1,
    )
    assert metrics is None
    assert binding == "conservacion_no_soportado"


def test_proforma_marks_lrv_and_sensitivity_as_based_on_estimated_area():
    response = asyncio.run(api.proforma_endpoint(
        area_m2=204.0,
        area_estimada=True,
        lng=None,
        lat=None,
        precio_lote_cop=None,
        precio_venta_cop_m2=7_500_000,
        costo_construccion_cop_m2=2_800_000,
        eficiencia_vendible_pct=0.82,
        costos_blandos_pct=0.18,
        margen_objetivo_pct=0.20,
    ))
    assert response["ok"] is True
    assert response["data"]["basado_en_area_estimada"] is True
    assert "VRL" in response["data"]["advertencia_area"]
    assert len(response["data"]["sensibilidad"]["matriz"]) == 3
