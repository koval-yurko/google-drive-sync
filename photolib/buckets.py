"""Greedy month-packing: the folder layout of the Global Photos folder.

Folders hold whole months only, packed chronologically to roughly
TARGET_SIZE files. Whole months keep names meaningful (`2025-01 - 2025-03`);
the cap keeps folders browsable. Plan Organization and Reorganize both ask
this module, so a planned upload and an existing file can never disagree
about where a month belongs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

TARGET_SIZE = 100
MAX_BUCKET = 130
UNKNOWN_FOLDER = "unknown-date"


def month_of(capture: int | None) -> str | None:
    if capture is None:
        return None
    return datetime.fromtimestamp(capture, tz=timezone.utc).strftime("%Y-%m")


@dataclass(frozen=True)
class Bucket:
    months: tuple[str, ...]
    count: int

    @property
    def name(self) -> str:
        if self.months[0] == self.months[-1]:
            return self.months[0]
        return f"{self.months[0]} - {self.months[-1]}"


def pack(counts: dict[str, int]) -> list[Bucket]:
    """Chronological greedy packing. A lone month may exceed MAX_BUCKET;
    a bucket never grows past it."""
    buckets: list[Bucket] = []
    months: list[str] = []
    total = 0
    for month in sorted(counts):
        count = counts[month]
        if months and total + count > MAX_BUCKET:
            buckets.append(Bucket(tuple(months), total))
            months, total = [], 0
        months.append(month)
        total += count
    if months:
        buckets.append(Bucket(tuple(months), total))
    return buckets


def folder_map(counts: dict[str, int]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for bucket in pack(counts):
        for month in bucket.months:
            mapping[month] = bucket.name
    return mapping
