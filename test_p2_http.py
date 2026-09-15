import pytest

import p2_lookup


class _JsonResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @staticmethod
    def read():
        return b'{"features": [{"attributes": {"ok": true}}]}'


def test_fetch_json_retries_one_direct_timeout(monkeypatch):
    calls = 0

    def flaky_urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("timed out")
        return _JsonResponse()

    monkeypatch.setattr(p2_lookup, "urlopen", flaky_urlopen)
    monkeypatch.setattr(p2_lookup.time, "sleep", lambda _seconds: None)
    payload = p2_lookup._fetch_json("https://example.test/query", timeout_s=1)
    assert calls == 2
    assert payload["features"][0]["attributes"]["ok"] is True


def test_fetch_json_maps_repeated_timeout_to_layer_error(monkeypatch):
    monkeypatch.setattr(
        p2_lookup,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    monkeypatch.setattr(p2_lookup.time, "sleep", lambda _seconds: None)
    with pytest.raises(p2_lookup.BuildabilityLookupError, match="Network timeout"):
        p2_lookup._fetch_json("https://example.test/query", timeout_s=1)


def test_road_width_falls_back_to_lot_boundary_for_large_lot(monkeypatch):
    calls = []

    def fake_query(_layer, lng, lat, **kwargs):
        calls.append((lng, lat, kwargs.get("distance_m")))
        if kwargs.get("distance_m") == p2_lookup._CALZADA_RADIUS_M:
            raise p2_lookup.ZeroFeaturesError("no road at center")
        if lng == -74.084:
            return [{"attributes": {"ANCHO": 9.63, "CODIGO_IDENTIFICACION_VIAL": 11003560}}]
        raise p2_lookup.ZeroFeaturesError("no road at boundary point")

    monkeypatch.setattr(p2_lookup, "query_fs", fake_query)
    rings = [[
        [-74.086, 4.744], [-74.084, 4.744],
        [-74.084, 4.746], [-74.086, 4.746], [-74.086, 4.744],
    ]]
    result = p2_lookup._query_ancho_via_gis(-74.085, 4.745, rings)
    assert result["D_m"] == 9.63
    assert "junto al lindero" in result["fuente"]
    assert any(distance == 15 for _, _, distance in calls)
