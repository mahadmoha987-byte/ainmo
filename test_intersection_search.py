import os

import pytest

import api
import geocode


@pytest.mark.parametrize("query", [
    "Calle 82 con Carrera 15",
    "Cll 82 # 15",
    "CL 82 con KR 15",
    "calle 82 y carrera 15",
    "Calle 60 Carrera 7",
    "esquina Av. Primero de Mayo con Carrera 50",
])
def test_corner_formats_are_parsed_without_becoming_door_addresses(query):
    parsed = geocode.parse_intersection_query(query)
    assert parsed is not None
    assert parsed["kind"] == "corner"
    assert geocode._is_intersection_query(query)


def test_between_streets_format_is_parsed():
    parsed = geocode.parse_intersection_query(
        "Carrera 100 entre Calle 13 y Avenida La Esperanza"
    )
    assert parsed is not None
    assert parsed["kind"] == "between"
    assert parsed["roads"][0]["labels"] == ["KR 100", "AK 100"]
    assert "AC 24" in parsed["roads"][2]["labels"]


def test_full_door_address_is_not_treated_as_intersection():
    assert not geocode._is_intersection_query("Calle 49 # 14-51")
    assert geocode.parse_intersection_query("Calle 49 # 14-51") is None


def test_area_hint_ranks_close_area_before_distance(monkeypatch):
    monkeypatch.setattr(geocode, "_road_axis_geometry", lambda _road: object())
    monkeypatch.setattr(
        geocode,
        "_road_intersection_points",
        lambda _first, _second: [(-74.0564, 4.6676)],
    )

    captured = {}

    def fake_candidates(points, **hints):
        captured.update(hints)
        return [{
            "lotcodigo": "008313014010",
            "lat": 4.6676,
            "lng": -74.0564,
            "label": "CL 82 15 35",
            "area_m2": 331.2,
            "area_within_10pct": True,
        }]

    monkeypatch.setattr(geocode, "_intersection_lot_candidates", fake_candidates)
    result = geocode.resolve_intersection(
        "Calle 82 con Carrera 15",
        listing_area_m2=332,
        frontage_m=12,
        depth_m=37,
    )
    assert result["resolution"] == "intersection_candidates"
    assert result["candidates"][0]["area_within_10pct"] is True
    assert captured == {"listing_area_m2": 332, "frontage_m": 12, "depth_m": 37}


def test_area_ranking_is_area_difference_then_distance_and_only_three_likely():
    candidates = [
        {
            "lotcodigo": str(index),
            "area_m2": area,
            "area_difference_m2": abs(area - 332),
            "area_difference_pct": abs(area - 332) / 332 * 100,
            "area_within_10pct": True,
            "distance_m": distance,
        }
        for index, area, distance in [
            ("far-exact", 332.0, 120.0),
            ("near-exact", 332.0, 40.0),
            ("third", 331.8, 20.0),
            ("fourth", 332.3, 5.0),
            ("fifth", 331.6, 1.0),
            ("sixth", 332.5, 1.0),
            ("seventh", 331.4, 1.0),
            ("eighth", 332.7, 1.0),
        ]
    ]
    ranked = geocode._rank_intersection_candidates(
        candidates, listing_area_m2=332
    )
    assert [item["lotcodigo"] for item in ranked[:4]] == [
        "near-exact", "far-exact", "third", "fourth"
    ]
    assert [item["ranking_score"]["rank"] for item in ranked] == list(range(1, 9))
    assert [item["likely_match"] for item in ranked] == [
        True, True, True, False, False, False, False, False
    ]


def test_between_streets_samples_the_complete_span(monkeypatch):
    parsed_axes = [object(), object(), object()]
    monkeypatch.setattr(geocode, "_road_axis_geometry", lambda _road: parsed_axes.pop(0))
    endpoints = iter([
        [(-74.155, 4.668)],
        [(-74.134, 4.681)],
    ])
    monkeypatch.setattr(geocode, "_road_intersection_points", lambda *_args: next(endpoints))
    captured = {}

    def fake_candidates(points, **_hints):
        captured["points"] = points
        return []

    monkeypatch.setattr(geocode, "_intersection_lot_candidates", fake_candidates)
    result = geocode.resolve_intersection(
        "Carrera 100 entre Calle 13 y Avenida La Esperanza",
        listing_area_m2=1000,
    )
    sampled = captured["points"]
    assert len(sampled) > 2
    assert sampled[0] == (-74.155, 4.668)
    assert sampled[-1] == (-74.134, 4.681)
    assert result["intersection"]["sampled_point_count"] == len(sampled)
    assert len(result["intersection"]["points"]) == 2


def test_intersection_identity_is_persistent_and_explicit():
    result = {"lote": {"lotcodigo": "008313014010"}}
    api._apply_address_identity(
        result,
        address="CL 82 15 35",
        searched_address="Calle 82 con Carrera 15",
        resolved_address="CL 82 15 35",
        search_mode="intersection",
    )
    assert result["direccion"] == "CL 82 15 35"
    resolution = result["address_resolution"]
    assert resolution["method"] == "intersection"
    assert resolution["approximate_identification"] is True
    assert "Confirme" in resolution["motivo"]


@pytest.mark.skipif(
    os.getenv("AINMO_LIVE_GIS_TESTS") != "1",
    reason="requires live Catastro and Mapa de Referencia services",
)
def test_live_real_listing_and_exact_address_regressions():
    geocode._cache.clear()
    listing = geocode.geocode_detailed(
        "Calle 82 con Carrera 15",
        listing_area_m2=332,
        frontage_m=12,
        depth_m=37,
    )
    assert listing["resolution"] == "intersection_candidates"
    assert any(candidate.get("area_within_10pct") for candidate in listing["candidates"])

    between = geocode.geocode_detailed(
        "Carrera 100 entre Calle 13 y Avenida La Esperanza"
    )
    assert between["resolution"] == "intersection_candidates"
    assert between["candidates"]

    exact = geocode.geocode_detailed("Calle 49 # 14-51")
    assert exact["resolution"] == "exact_address"
    assert exact["candidates"][0]["lotcodigo"] == "007204006020"
    properties = geocode.catastro_properties_for_lot("007204006020")
    assert any(item["chip"] == "AAA0084DEZE" for item in properties)
