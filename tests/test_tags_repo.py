import pytest

from photolib.db.tags_repo import DuplicateTagError, TagsRepo, slugify


def _drive_file(conn, drive_id: str, name: str = "IMG.HEIC", parent: str = "2025-05"):
    conn.execute(
        "INSERT INTO drive_files (drive_id, name, parent_path) VALUES (?, ?, ?)",
        (drive_id, name, parent),
    )
    conn.commit()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Family", "family"),
        ("Greece 2025", "greece-2025"),
        ("  Print These!  ", "print-these"),
        ("Lake  Como", "lake-como"),
        ("Ünïcodé", "unicode"),
        ("a" * 80, "a" * 60),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_slug_must_survive_being_a_drive_property_key():
    """t_<slug> shares a 124-byte budget with its value; 60 chars leaves room."""
    assert len(slugify("x" * 200)) == 60


def test_create_returns_the_row(conn):
    tag = TagsRepo(conn).create("Family")
    assert tag["name"] == "Family"
    assert tag["slug"] == "family"
    assert tag["color"] == "#6b7280"


def test_creating_the_same_slug_twice_is_an_error(conn):
    repo = TagsRepo(conn)
    repo.create("Family")
    with pytest.raises(DuplicateTagError):
        repo.create("  family ")


def test_counts_only_files_that_are_still_in_drive(conn):
    """A tag on a file that vanished from Drive must not inflate its count."""
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "here")
    repo.add_files(tag["id"], ["here", "gone"])

    counts = {row["slug"]: row["file_count"] for row in repo.list_with_counts()}
    assert counts["family"] == 1


def test_trashed_files_do_not_count(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")
    conn.execute("UPDATE drive_files SET trashed_at = 'now' WHERE drive_id = 'd1'")
    conn.commit()
    repo.add_files(tag["id"], ["d1"])

    assert repo.list_with_counts()[0]["file_count"] == 0


def test_add_files_is_idempotent(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")

    assert repo.add_files(tag["id"], ["d1", "d1"]) == 1
    assert repo.add_files(tag["id"], ["d1"]) == 0
    assert repo.list_with_counts()[0]["file_count"] == 1


def test_remove_files(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")
    _drive_file(conn, "d2")
    repo.add_files(tag["id"], ["d1", "d2"])

    assert repo.remove_files(tag["id"], ["d1"]) == 1
    assert repo.list_with_counts()[0]["file_count"] == 1


def test_rename_updates_the_slug(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Familly")
    renamed = repo.rename(tag["id"], "Family")
    assert (renamed["name"], renamed["slug"]) == ("Family", "family")


def test_renaming_onto_an_existing_slug_is_an_error(conn):
    repo = TagsRepo(conn)
    repo.create("Family")
    other = repo.create("Friends")
    with pytest.raises(DuplicateTagError):
        repo.rename(other["id"], "Family")


def test_recolor(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    assert repo.recolor(tag["id"], "#ff0000")["color"] == "#ff0000"


def test_delete_takes_its_assignments_with_it(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    _drive_file(conn, "d1")
    repo.add_files(tag["id"], ["d1"])

    repo.delete(tag["id"])
    assert repo.list_with_counts() == []
    assert conn.execute("SELECT COUNT(*) FROM file_tags").fetchone()[0] == 0


def test_merge_moves_files_and_drops_the_source(conn):
    repo = TagsRepo(conn)
    source = repo.create("Familly")
    target = repo.create("Family")
    for drive_id in ("d1", "d2"):
        _drive_file(conn, drive_id)
    repo.add_files(source["id"], ["d1", "d2"])
    repo.add_files(target["id"], ["d2"])

    moved = repo.merge(source["id"], target["id"])

    assert moved == 1                      # d2 was already there
    assert [r["slug"] for r in repo.list_with_counts()] == ["family"]
    assert repo.list_with_counts()[0]["file_count"] == 2


def test_merging_a_tag_into_itself_is_refused(conn):
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    with pytest.raises(ValueError):
        repo.merge(tag["id"], tag["id"])


def test_tags_for_groups_by_file(conn):
    repo = TagsRepo(conn)
    family = repo.create("Family")
    print_these = repo.create("Print These")
    for drive_id in ("d1", "d2"):
        _drive_file(conn, drive_id)
    repo.add_files(family["id"], ["d1", "d2"])
    repo.add_files(print_these["id"], ["d1"])

    grouped = repo.tags_for(["d1", "d2", "d3"])

    assert [t["slug"] for t in grouped["d1"]] == ["family", "print-these"]
    assert [t["slug"] for t in grouped["d2"]] == ["family"]
    assert "d3" not in grouped


def test_tags_for_handles_an_empty_request(conn):
    assert TagsRepo(conn).tags_for([]) == {}


def test_tags_for_survives_more_files_than_sqlite_takes_variables(conn):
    """1,284 files in one page must not trip SQLite's variable limit."""
    repo = TagsRepo(conn)
    tag = repo.create("Family")
    ids = [f"d{n}" for n in range(1500)]
    for drive_id in ids:
        _drive_file(conn, drive_id)
    repo.add_files(tag["id"], ids)

    assert len(repo.tags_for(ids)) == 1500


def test_slugs_by_file(conn):
    repo = TagsRepo(conn)
    family = repo.create("Family")
    _drive_file(conn, "d1")
    repo.add_files(family["id"], ["d1"])

    assert repo.slugs_by_file() == {"d1": {"family"}}
