from photolib.planning.buckets import UNKNOWN_FOLDER, folder_map, month_of, pack


def test_month_of_formats_utc_and_tolerates_none():
    assert month_of(1700000000) == "2023-11"
    assert month_of(None) is None


def test_small_months_pack_into_one_named_range():
    buckets = pack({"2022-01": 5, "2022-03": 4, "2023-02": 7})
    assert len(buckets) == 1
    assert buckets[0].name == "2022-01 - 2023-02"
    assert buckets[0].count == 16


def test_a_single_month_bucket_is_named_by_that_month():
    assert pack({"2026-05": 90})[0].name == "2026-05"


def test_a_bucket_closes_rather_than_growing_past_the_cap():
    buckets = pack({"2025-01": 90, "2025-02": 50})
    assert [b.name for b in buckets] == ["2025-01", "2025-02"]


def test_an_oversized_month_stands_alone():
    buckets = pack({"2026-04": 50, "2026-05": 182, "2026-06": 60})
    assert [b.name for b in buckets] == ["2026-04", "2026-05", "2026-06"]
    assert buckets[1].count == 182


def test_packing_ignores_insertion_order():
    counts = {"2024-02": 10, "2024-01": 10}
    assert pack(counts) == pack(dict(reversed(list(counts.items()))))
    assert pack(counts)[0].months == ("2024-01", "2024-02")


def test_folder_map_covers_every_month():
    mapping = folder_map({"2022-01": 5, "2022-02": 4})
    assert mapping == {
        "2022-01": "2022-01 - 2022-02",
        "2022-02": "2022-01 - 2022-02",
    }


def test_unknown_folder_name():
    assert UNKNOWN_FOLDER == "unknown-date"
