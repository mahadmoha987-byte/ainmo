import pytest

import p2_lookup


def feature(tratamiento, altura, tipologia=""):
    return {"attributes": {
        "TRATAMIENTO": tratamiento,
        "TIPOLOGIA": tipologia,
        "ALTURA_MAXIMA": altura,
        "OBSERVACION": None,
        "ACTO_ADMINISTRATIVO": None,
    }}


def test_identical_layer_15_overlaps_are_safe_to_deduplicate():
    features = [feature("CONSOLIDACION", "3"), feature("CONSOLIDACION", "3")]
    selected = p2_lookup._select_edificabilidad_feature(features)
    assert selected["TRATAMIENTO"] == "CONSOLIDACION"


def test_conflicting_layer_15_overlaps_are_never_selected_silently():
    features = [feature("CONSOLIDACION", "UNE"), feature("DESARROLLO", "Rg 3")]
    with pytest.raises(p2_lookup.AmbiguousRegulationError) as caught:
        p2_lookup._select_edificabilidad_feature(features)
    assert {option["TRATAMIENTO"] for option in caught.value.options} == {"CONSOLIDACION", "DESARROLLO"}


def test_expected_address_lot_disambiguates_overlapping_catastro_polygons(monkeypatch):
    features = [
        {"attributes": {"LOTCODIGO": "000000000001"}, "geometry": {"rings": [[[0, 0], [1, 0], [0, 1]]] }},
        {"attributes": {"LOTCODIGO": "000000000002"}, "geometry": {"rings": [[[0, 0], [2, 0], [0, 2]]] }},
    ]
    monkeypatch.setattr(p2_lookup, "_query_catastro", lambda _params: features)
    selected, distance = p2_lookup.query_catastro_lote(0, 0, "000000000002")
    assert selected["attributes"]["LOTCODIGO"] == "000000000002"
    assert distance is None
