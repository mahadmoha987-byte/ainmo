import asyncio

import api
import geocode
from tools.import_address_index import (
    TreatmentGrid,
    TreatmentPolygon,
    deduplicate_records,
    searchable_record,
)


def test_search_normalization_reuses_bogota_aliases_and_ignores_separators():
    assert geocode.normalize_address_search("Cl. 85 # 11-53") == "CL 85 11 53"
    assert geocode.normalize_address_search("Calle 85 11 53") == "CL 85 11 53"
    assert geocode.normalize_address_search("Av. Cra. 68 # 40-15") == "AK 68 40 15"
    assert geocode.normalize_address_search("Avenida Boyacá # 63-20") == "AK 72 63 20"


def test_catastro_display_address_formats_the_door_number():
    assert (
        geocode.canonical_catastro_address("CL 85 ", "11 53")
        == "CL 85 # 11-53"
    )
    assert (
        geocode.canonical_catastro_address("KR 1A", "6C 50 S")
        == "KR 1A # 6C-50 S"
    )


def test_treatment_grid_returns_one_value_and_flags_conflicts():
    outer = TreatmentPolygon(
        "CONSOLIDACION",
        [[[-74.1, 4.6], [-74.0, 4.6], [-74.0, 4.7], [-74.1, 4.7]]],
        -74.1,
        4.6,
        -74.0,
        4.7,
    )
    overlap = TreatmentPolygon(
        "RENOVACION",
        [[[-74.06, 4.64], [-74.04, 4.64], [-74.04, 4.66], [-74.06, 4.66]]],
        -74.06,
        4.64,
        -74.04,
        4.66,
    )
    grid = TreatmentGrid([outer, overlap])
    assert grid.classify(-74.08, 4.62) == ("CONSOLIDACION", False)
    assert grid.classify(-74.05, 4.65) == (None, True)
    assert grid.classify(-73.9, 4.9) == (None, False)


def test_import_record_uses_the_same_search_normalizer():
    feature = {
        "attributes": {
            "OBJECTID": 42,
            "PDOCODIGO": "ABC123",
            "PDONVIAL": "CL 85 ",
            "PDOTEXTO": "11 53",
            "PDOCLOTE": "001234567890",
            "PDOTIPO": 1,
        },
        "geometry": {"x": -74.052, "y": 4.668},
    }
    row, note = searchable_record(feature, None, "2026-09-08")
    assert note is None
    assert row["address"] == "CL 85 # 11-53"
    assert row["normalized_address"] == "CL 85 11 53"
    assert row["lot_code"] == "001234567890"


def test_import_deduplicates_catastro_address_ids_before_upsert():
    rows = [
        {"source_address_id": "A", "source_object_id": 10},
        {"source_address_id": "B", "source_object_id": 11},
        {"source_address_id": "A", "source_object_id": 12},
    ]
    deduplicated, duplicate_count = deduplicate_records(rows)
    assert duplicate_count == 1
    assert deduplicated == [
        {"source_address_id": "A", "source_object_id": 12},
        {"source_address_id": "B", "source_object_id": 11},
    ]


def test_incomplete_suggestion_query_never_hits_database(monkeypatch):
    async def unexpected(*_args, **_kwargs):
        raise AssertionError("short queries must not hit Supabase")

    monkeypatch.setattr(api.db, "search_address_index", unexpected)
    for query in ("Cl", "Calle", "85", "Bogotá"):
        result = asyncio.run(
            api.address_suggest_endpoint(
                q=query, lat=None, lng=None, recent_lots=""
            )
        )
        assert result["ok"] is True
        assert result["suggestions"] == []


def test_suggestion_endpoint_normalizes_and_forwards_valid_ranking_context(monkeypatch):
    captured = {}

    async def fake_search(query, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return [
            {
                "address": "CL 85 # 11-53",
                "normalized_address": "CL 85 11 53",
                "lot_code": "001234567890",
                "treatment": "CONSOLIDACION",
                "lat": 4.668,
                "lng": -74.052,
                "locality": None,
                "neighborhood": None,
            }
        ], True

    monkeypatch.setattr(api.db, "search_address_index", fake_search)
    result = asyncio.run(
        api.address_suggest_endpoint(
            q="Cl. 85 # 11",
            lat=4.668,
            lng=-74.052,
            recent_lots="001234567890,bad-value",
        )
    )
    assert result["index_ready"] is True
    assert result["suggestions"][0]["lot_code"] == "001234567890"
    assert captured == {
        "query": "CL 85 11",
        "lat": 4.668,
        "lng": -74.052,
        "recent_lot_codes": ["001234567890"],
        "limit": 8,
    }


def test_suggestion_endpoint_drops_viewport_outside_bogota(monkeypatch):
    captured = {}

    async def fake_search(_query, **kwargs):
        captured.update(kwargs)
        return [], True

    monkeypatch.setattr(api.db, "search_address_index", fake_search)
    asyncio.run(
        api.address_suggest_endpoint(
            q="Calle 85",
            lat=40.7128,
            lng=-74.006,
            recent_lots="",
        )
    )
    assert captured["lat"] is None
    assert captured["lng"] is None


def test_suggestion_database_incident_preserves_existing_search(monkeypatch):
    async def broken_search(*_args, **_kwargs):
        raise RuntimeError("temporary database incident")

    monkeypatch.setattr(api.db, "search_address_index", broken_search)
    result = asyncio.run(
        api.address_suggest_endpoint(
            q="Calle 85", lat=4.668, lng=-74.052, recent_lots=""
        )
    )
    assert result["ok"] is True
    assert result["suggestions"] == []
    assert result["index_ready"] is False
