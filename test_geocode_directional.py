import geocode


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
