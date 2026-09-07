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
