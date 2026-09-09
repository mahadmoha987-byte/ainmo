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
