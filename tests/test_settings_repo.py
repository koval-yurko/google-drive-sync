from photolib.db.settings_repo import PHOTOS_ROOT, FolderRef, SettingsRepo


def test_get_missing_returns_default(conn):
    repo = SettingsRepo(conn)
    assert repo.get("nope") is None
    assert repo.get("nope", "fallback") == "fallback"


def test_set_then_get(conn):
    repo = SettingsRepo(conn)
    repo.set("colour", "blue")
    assert repo.get("colour") == "blue"


def test_set_overwrites(conn):
    repo = SettingsRepo(conn)
    repo.set("colour", "blue")
    repo.set("colour", "green")
    assert repo.get("colour") == "green"
    assert repo.all() == {"colour": "green"}


def test_folder_round_trip(conn):
    repo = SettingsRepo(conn)
    repo.set_folder(PHOTOS_ROOT, FolderRef(id="abc123", name="Photos"))
    got = repo.get_folder(PHOTOS_ROOT)
    assert got == FolderRef(id="abc123", name="Photos")


def test_get_folder_missing_returns_none(conn):
    assert SettingsRepo(conn).get_folder(PHOTOS_ROOT) is None
