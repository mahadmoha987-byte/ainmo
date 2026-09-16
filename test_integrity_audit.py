import asyncio
import io
import json

from starlette.requests import Request

import api
import dxf_export
import ezdxf
import p2_lookup


def test_near_match_never_falls_back_to_the_typed_address():
    result = {"lote": {"lotcodigo": "008109012006"}}
    api._apply_address_identity(
        result,
        address="KR 7 # 32-16",
        searched_address="KR 7 # 32-16",
        resolved_address="",
        near_match=True,
    )
    assert result["direccion"] == "Predio 008109012006"
    assert result["direccion"] != "KR 7 # 32-16"


def test_direct_coordinate_lookup_gets_explicit_coordinate_title():
    result = {"lote": {"lotcodigo": "004514061017"}}
    api._apply_address_identity(result)
    assert result["direccion"] == "Consultado por coordenada"


def test_risk_endpoint_never_turns_unconfigured_sources_into_no_risk(monkeypatch):
    async def fake_slope(_lat, _lng):
        return 4.2

    monkeypatch.setattr(api, "_calc_slope_pct", fake_slope)
    payload = asyncio.run(api.risk_hazards_endpoint(lat=4.628, lng=-74.15))
    data = payload["data"]
    assert data["estado"] == "insuficiente"
    assert data["inundacion"] is None and data["deslizamiento"] is None
    assert data["verificaciones"]["inundacion"]["estado"] == "insuficiente"
    assert data["fuente_amenaza"] is None


def test_vis_comparison_uses_explicit_same_financial_assumptions():
    payload = asyncio.run(api.proforma_vis_comparison(
        area_m2=1000,
        area_vis_m2=1200,
        lng=None,
        lat=None,
        precio_lote_cop=None,
        precio_venta_cop_m2=6_000_000,
        precio_venta_vis_cop_m2=5_000_000,
        costo_construccion_cop_m2=3_000_000,
        costos_blandos_pct=0.18,
        margen_objetivo_pct=0.20,
    ))
    assert payload["data"]["base"] is not None
    assert payload["data"]["vis"] is not None
    assert any("supuesto financiero, no una regla" in note for note in payload["data"]["vis_notas"])


def test_vis_comparison_does_not_invent_a_sale_price():
    payload = asyncio.run(api.proforma_vis_comparison(
        area_m2=1000,
        area_vis_m2=None,
        lng=None,
        lat=None,
        precio_lote_cop=None,
        precio_venta_cop_m2=None,
        precio_venta_vis_cop_m2=None,
        costo_construccion_cop_m2=None,
        costos_blandos_pct=None,
        margen_objetivo_pct=None,
    ))
    assert payload["data"]["vis"] is None
    assert any("Precio de venta VIS pendiente" in note for note in payload["data"]["vis_notas"])


def test_report_html_returns_structured_missing_input(monkeypatch):
    async def blocked(**_kwargs):
        raise api.calc.InputRequired("Se requiere anu_m2")

    monkeypatch.setattr(api, "_calculate_current_lot", blocked)
    response = asyncio.run(api.report_html_endpoint(
        lng=-74.15, lat=4.628, vis_en_sitio=False, anu_m2=None,
        frente_m=None, ancho_via_m=None, address="", searched_address="",
        resolved_address="", near_match=False, expected_lotcodigo=None,
    ))
    body = json.loads(response.body)
    assert body["error"] == "input_required"
    assert "Área Neta Urbanizable" in body["que_se_necesita"]


def test_dxf_inset_shrinks_ccw_polygon_and_stops_invalid_envelope():
    lot = [(0, 0), (10, 0), (10, 10), (0, 10)]
    inset = dxf_export._inset_polygon(lot, 1)
    assert 0 < abs(dxf_export._signed_area(inset)) < abs(dxf_export._signed_area(lot))

    ring = [[-74.15, 4.628], [-74.1499, 4.628], [-74.1499, 4.6281], [-74.15, 4.6281], [-74.15, 4.628]]
    output_bytes = dxf_export.generate_dxf(
        calc_result={"metrics": {
            "altura_base_pisos": {"valor": 5},
            "area_construible_estimada": {"valor_m2": 50000, "estado": "requiere_concepto", "fuera_de_rango": True, "motivo": "Fuera del rango validado."},
        }, "antejardin": {"dimension_m": 3}},
        lookup_snapshot={"lote": {"geojson_polygon": [ring]}},
    )
    output = output_bytes.decode("utf-8")
    assert "Fuera del rango validado." in output
    doc = ezdxf.read(io.StringIO(output))
    assert any(entity.dxf.layer == "LOT" for entity in doc.modelspace())
    assert not any(entity.dxf.layer in {"FLOORS", "FOOTPRINT"} for entity in doc.modelspace())


def test_failed_restriction_query_is_not_reported_as_a_clean_consultation(monkeypatch):
    def failed_bic(_lng, _lat):
        raise RuntimeError("service unavailable")

    monkeypatch.setattr(p2_lookup, "_check_bic_restriction", failed_bic)
    active, coverage = p2_lookup.check_blocking_restrictions(
        -74.15, 4.628, include_coverage=True
    )
    assert active == []
    assert coverage["bic"] == "consulta_fallida"
    assert coverage["aerocivil"] == "sin_validacion_espacial"
