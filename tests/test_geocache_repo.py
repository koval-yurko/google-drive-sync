from photolib.db.geocache_repo import MISSING, GeocacheRepo


def test_get_returns_missing_for_an_unknown_key(conn):
    assert GeocacheRepo(conn).get("50.00,30.00") is MISSING


def test_put_then_get_round_trips_the_country(conn):
    repo = GeocacheRepo(conn)
    repo.put("50.00,30.00", "Ukraine", {"results": []})
    assert repo.get("50.00,30.00") == "Ukraine"


def test_a_cached_none_is_a_hit_not_a_miss(conn):
    """The API answering 'no country here' must not be re-requested."""
    repo = GeocacheRepo(conn)
    repo.put("0.00,0.00", None, {"results": []})
    assert repo.get("0.00,0.00") is None
    assert repo.get("0.00,0.00") is not MISSING


def test_put_overwrites_an_existing_key(conn):
    repo = GeocacheRepo(conn)
    repo.put("50.00,30.00", "Ukraine", {"v": 1})
    repo.put("50.00,30.00", "Poland", {"v": 2})
    assert repo.get("50.00,30.00") == "Poland"
