import httpx

from photolib.places import Geocoder, api_key_from_env, cache_key

RESPONSE = {
    "status": "OK",
    "results": [
        {
            "address_components": [
                {"long_name": "Warsaw", "types": ["locality", "political"]},
                {"long_name": "Poland", "types": ["country", "political"]},
            ]
        }
    ],
}


def geocoder_with(conn, handler, api_key="test-key"):
    return Geocoder(
        conn, api_key, http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_cache_key_rounds_to_about_one_kilometre():
    assert cache_key(52.2312345, 21.0119999) == "52.23,21.01"
    assert cache_key(52.2312345, 21.0119999) == cache_key(52.2349, 21.0121)


def test_lookup_returns_place_and_country(conn):
    geo = geocoder_with(conn, lambda request: httpx.Response(200, json=RESPONSE))
    assert geo.lookup(52.23, 21.01) == ("Warsaw", "Poland")


def test_lookup_caches_and_does_not_call_twice(conn):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=RESPONSE)

    geo = geocoder_with(conn, handler)
    geo.lookup(52.23, 21.01)
    geo.lookup(52.2349, 21.0121)   # same rounded key
    assert calls["n"] == 1


def test_cache_survives_a_new_geocoder(conn):
    geocoder_with(conn, lambda r: httpx.Response(200, json=RESPONSE)).lookup(52.23, 21.01)

    def explode(request):
        raise AssertionError("should have been served from cache")

    assert geocoder_with(conn, explode).lookup(52.23, 21.01) == ("Warsaw", "Poland")


def test_no_api_key_returns_nothing_and_makes_no_call(conn):
    def explode(request):
        raise AssertionError("must not call the API without a key")

    geo = geocoder_with(conn, explode, api_key=None)
    assert geo.lookup(52.23, 21.01) == (None, None)


def test_zero_results_is_cached_as_empty(conn):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})

    geo = geocoder_with(conn, handler)
    assert geo.lookup(1.0, 1.0) == (None, None)
    geo.lookup(1.0, 1.0)
    assert calls["n"] == 1


def test_api_failure_returns_nothing_without_raising(conn):
    geo = geocoder_with(conn, lambda r: httpx.Response(500, text="boom"))
    assert geo.lookup(52.23, 21.01) == (None, None)


def test_api_key_from_env_prefers_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "from-env")
    assert api_key_from_env(tmp_path) == "from-env"


def test_api_key_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    (tmp_path / ".env").write_text("# a comment\nGOOGLE_MAPS_API_KEY=from-file\n")
    assert api_key_from_env(tmp_path) == "from-file"


def test_api_key_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    assert api_key_from_env(tmp_path) is None
