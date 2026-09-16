import geocode
import pytest


def test_south_qualifier_on_cross_address_is_preserved_for_catastro():
    normalized = geocode.normalize_address("KR 1A 6C 50 SUR")
    assert normalized == "KR 1A # 6C-50 S"
    assert geocode._parse_address(normalized) == ("KR 1A", "6C 50 S")


def test_south_and_east_qualifiers_on_both_streets_are_preserved():
    normalized = geocode.normalize_address("CL 11 SUR 1 60 ESTE")
    assert normalized == "CL 11 S # 1-60 E"
    assert geocode._parse_address(normalized) == ("CL 11 S", "1 60 E")


def test_transversal_south_address_from_official_catastro_export():
    normalized = geocode.normalize_address("TV 5U 48J 08 SUR")
    assert normalized == "TV 5U # 48J-08 S"
    assert geocode._parse_address(normalized) == ("TV 5U", "48J 08 S")


def test_av_cra_round_trips_through_canonical_parser():
    normalized = geocode.normalize_address("Av. Cra. 68 # 40-15")
    assert normalized == "AK 68 # 40-15"
    assert geocode._parse_address(normalized) == ("AK 68", "40 15")


def test_named_bogota_avenues_normalize_to_catastro_codes():
    assert geocode.normalize_address("Avenida Boyacá # 63-20") == "AK 72 # 63-20"
    assert geocode.normalize_address("Av. Caracas # 53-20") == "AK 14 # 53-20"
    assert geocode.normalize_address("NQS # 45A-55") == "AK 30 # 45A-55"
    assert geocode.normalize_address("Av. El Dorado # 69-76") == "AC 26 # 69-76"
    assert geocode.normalize_address("Autopista Norte # 100-20") == "AK 45 # 100-20"


def test_incomplete_street_input_is_rejected_before_external_lookup(monkeypatch):
    geocode._cache.clear()

    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("an incomplete address must not call Catastro or Nominatim")

    monkeypatch.setattr(geocode.urllib.request, "urlopen", unexpected_request)
    result = geocode.geocode_detailed("Carrera")
    assert result == {"candidates": [], "resolution": "incomplete_address"}


def test_complete_plate_validation_accepts_directional_and_bis_addresses():
    assert geocode._is_complete_street_plate("CL 85 # 11-53")
    assert geocode._is_complete_street_plate("KR 13BIS E # 75B-16 S")
    assert not geocode._is_complete_street_plate("CL 85")
    assert not geocode._is_complete_street_plate("BOGOTA")


def test_chip_is_recognized_before_street_plate_validation(monkeypatch):
    geocode._cache.clear()
    monkeypatch.setattr(geocode, "_catastro_chip_query", lambda chip: [{
        "lat": 4.628,
        "lng": -74.15,
        "label": "KR 78K 6 35 SUR",
        "source": "catastro",
        "lotcodigo": "004514061017",
        "chip": chip,
        "match_type": "exact_chip",
        "match_confidence": "alta",
    }])

    result = geocode.geocode_detailed("aaa-0044-odrj")
    assert result["resolution"] == "exact_chip"
    assert result["candidates"][0]["chip"] == "AAA0044ODRJ"
    assert result["candidates"][0]["lotcodigo"] == "004514061017"
    assert geocode._normalize_chip("CALLEBOGOTA") is None


def test_real_colombian_address_outside_bogota_gets_coverage_resolution():
    geocode._cache.clear()
    result = geocode.geocode_detailed("Carrera 43A # 1-50, Medellín")
    assert result["candidates"] == []
    assert result["resolution"] == "outside_bogota"
    assert result["locality"] == "Medellín"


@pytest.mark.parametrize(("raw", "locality"), [
    ("Cra 7 # 15-20, Soacha", "Soacha"),
    ("Carrera 9 # 12-30 Chía, Cundinamarca", "Chía"),
])
def test_explicit_nearby_city_stops_before_bogota_lookup(monkeypatch, raw, locality):
    geocode._cache.clear()

    def unexpected(*_args, **_kwargs):
        raise AssertionError("an explicit non-Bogotá city must not query Bogotá Catastro")

    monkeypatch.setattr(geocode, "_catastro_query", unexpected)
    monkeypatch.setattr(geocode, "_nominatim_query", unexpected)
    result = geocode.geocode_detailed(raw)
    assert result == {
        "candidates": [],
        "resolution": "outside_bogota",
        "locality": locality,
    }


@pytest.mark.parametrize("raw", [
    "CHIP AAA0044ODRJ",
    "chipAAA0044ODRJ",
    "Chip: AAA-0044-ODRJ",
])
def test_chip_label_prefix_is_ignored(raw):
    assert geocode._normalize_chip(raw) == "AAA0044ODRJ"


def test_compound_avenue_without_hash_is_not_mistaken_for_intersection():
    assert not geocode._is_intersection_query("Avenida Carrera 11 109 32")
    assert not geocode._is_intersection_query("Avenida Calle 13 16A 12")
    assert geocode._is_intersection_query("Calle 60 Carrera 7")


def test_multiletter_bis_tokens_accept_missing_hash_format():
    assert geocode.normalize_address("Carrera 72MBIS 10 02") == "KR 72MBIS # 10-02"
    assert geocode.normalize_address("CL 76BISA 94A 13") == "CL 76BISA # 94A-13"


def test_polygon_interior_point_stays_inside_concave_lot():
    rings = [[
        [0.0, 0.0], [4.0, 0.0], [4.0, 1.0], [1.0, 1.0],
        [1.0, 4.0], [0.0, 4.0], [0.0, 0.0],
    ]]
    point = geocode._polygon_interior_point(rings)
    assert point is not None
    assert geocode._point_in_rings(*point, rings)


def test_catastro_candidate_preserves_linked_lot_code(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(geocode.urllib.request, "urlopen", lambda *_a, **_kw: Response())
    monkeypatch.setattr(geocode.json, "load", lambda _response: {
        "features": [{
            "attributes": {
                "PDONVIAL": "KR 19 E",
                "PDOTEXTO": "14 99 S",
                "PDOCLOTE": "001116041084",
            },
            "geometry": {"x": -74.07412, "y": 4.56754},
        }]
    })

    result = geocode._catastro_query("KR 19 E", "14 99 S")
    assert result[0]["lotcodigo"] == "001116041084"


def test_nominatim_rejects_city_level_result(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(geocode.urllib.request, "urlopen", lambda *_a, **_kw: Response())
    monkeypatch.setattr(geocode.json, "load", lambda _response: [{
        "lat": "4.6533817",
        "lon": "-74.0836331",
        "display_name": "Bogotá ciudad, Distrito Capital, Colombia",
        "class": "place",
        "type": "city",
        "addresstype": "city",
        "address": {"city": "Bogotá"},
    }])

    assert geocode._nominatim_query("Av. Cra. 68 # 40-15") == []


def test_nominatim_keeps_property_level_result(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(geocode.urllib.request, "urlopen", lambda *_a, **_kw: Response())
    monkeypatch.setattr(geocode.json, "load", lambda _response: [{
        "lat": "4.668",
        "lon": "-74.052",
        "display_name": "Calle 85 11-53, Bogotá, Colombia",
        "class": "place",
        "type": "house",
        "addresstype": "house",
        "address": {"house_number": "11-53", "road": "Calle 85"},
    }])

    result = geocode._nominatim_query("Cl. 85 # 11-53")
    assert result[0]["match_type"] == "osm_address"
    assert result[0]["match_confidence"] == "baja"


def test_real_street_is_not_mislabeled_when_first_like_row_is_a_variant(monkeypatch):
    geocode._cache.clear()
    calls = []

    def fake_query(via, text, limit=20, *, exact=True):
        calls.append((via, text, limit, exact))
        if not text and via == "CL 106" and limit >= 100:
            return [{"label": "CL 106 # 7-10"}]
        return []

    monkeypatch.setattr(geocode, "_catastro_query", fake_query)
    monkeypatch.setattr(geocode, "_catastro_near", lambda *_args: [])
    monkeypatch.setattr(geocode, "_nominatim_query", lambda *_args: [])
    result = geocode.geocode_detailed("Calle 106 # 5-40")
    assert result["resolution"] == "street_recognized"
    assert any(via == "CL 106" and not text and limit >= 100 for via, text, limit, _ in calls)
