"""One file, start to finish, with no database and no threads in sight."""

import hashlib
import zlib

import pytest

from photolib import transfer
from photolib.drive.errors import TransientError
from photolib.ziparchive.reader import ZipEntry, read_central_directory
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip, reader_for

PAYLOAD = b"a photograph, if you squint" * 400


def archive_and_entry(name: str = "IMG_1.HEIC", content: bytes = PAYLOAD):
    data = build_zip({f"Takeout/Google Photos/Photos from 2023/{name}": content})
    read_range = reader_for(data)
    entry = read_central_directory(read_range, len(data))[0]
    return read_range, entry


def test_spool_writes_the_inflated_bytes(tmp_path):
    read_range, entry = archive_and_entry()
    dest = tmp_path / "out.bin"
    transfer.spool_entry(read_range, entry, dest)
    assert dest.read_bytes() == PAYLOAD


def test_spool_rejects_a_bad_crc(tmp_path):
    read_range, entry = archive_and_entry()
    liar = ZipEntry(
        path=entry.path, name=entry.name, crc32=entry.crc32 ^ 0xFFFF,
        size=entry.size, compressed_size=entry.compressed_size,
        method=entry.method, local_header_offset=entry.local_header_offset,
    )
    with pytest.raises(transfer.TransferError) as exc:
        transfer.spool_entry(read_range, liar, tmp_path / "out.bin")
    assert exc.value.stage == "crc"


def test_spool_never_holds_the_whole_file(tmp_path):
    """Read in slices — the reason this project can move a 2 GB video."""
    read_range, entry = archive_and_entry()
    sizes = []

    def counting(start: int, end: int) -> bytes:
        sizes.append(end - start + 1)
        return read_range(start, end)

    transfer.spool_entry(counting, entry, tmp_path / "out.bin", chunk=1024)
    assert max(sizes) <= 1024


def transfer_one(tmp_path, fake, **overrides):
    read_range, entry = overrides.pop("pair", archive_and_entry())
    kwargs = dict(
        read_range=read_range, entry=entry, writer=fake, parent_id="p",
        name="IMG_1.HEIC", properties={"source_crc": str(entry.crc32)},
        spool_dir=tmp_path,
    )
    kwargs.update(overrides)
    return transfer.transfer_entry(**kwargs)


def test_a_clean_transfer_returns_the_drive_id_and_md5(tmp_path):
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    result = transfer_one(tmp_path, fake)
    assert result.md5 == hashlib.md5(PAYLOAD).hexdigest()
    assert fake.get_file(result.drive_file_id).name == "IMG_1.HEIC"
    assert result.size == len(PAYLOAD)


def test_properties_ride_along_with_the_upload(tmp_path):
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    result = transfer_one(
        tmp_path, fake, properties={"place": "Warsaw", "source_archive": "a.zip"}
    )
    assert fake.properties_of(result.drive_file_id)["place"] == "Warsaw"


def test_the_spool_file_is_always_cleaned_up(tmp_path):
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    transfer_one(tmp_path, fake)
    assert list(tmp_path.iterdir()) == []


def test_a_bad_crc_never_reaches_drive(tmp_path):
    read_range, entry = archive_and_entry()
    liar = ZipEntry(
        path=entry.path, name=entry.name, crc32=0, size=entry.size,
        compressed_size=entry.compressed_size, method=entry.method,
        local_header_offset=entry.local_header_offset,
    )
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    with pytest.raises(transfer.TransferError) as exc:
        transfer_one(tmp_path, fake, pair=(read_range, liar))
    assert exc.value.stage == "crc"
    assert fake.list_children("p") == []
    assert list(tmp_path.iterdir()) == []


def test_an_md5_mismatch_trashes_the_bad_upload(tmp_path):
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    fake.corrupt_next_upload = True
    with pytest.raises(transfer.TransferError) as exc:
        transfer_one(tmp_path, fake)
    assert exc.value.stage == "verify"
    assert len(fake.trashed) == 1
    assert fake.list_children("p") == []      # nothing corrupt left behind


def test_the_session_is_reported_as_soon_as_it_exists(tmp_path):
    """So a crash mid-upload leaves something to resume from."""
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    seen = []
    transfer_one(tmp_path, fake, on_session=seen.append)
    assert len(seen) == 1 and seen[0].startswith("https://upload.fake/")


def test_a_transient_failure_is_retried_from_drives_offset(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "BASE_DELAY", 0)
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    fake.fail_chunks = 2
    result = transfer_one(tmp_path, fake)
    assert fake.get_file(result.drive_file_id).md5 == hashlib.md5(PAYLOAD).hexdigest()


def test_an_expired_session_is_replaced_rather_than_resumed(tmp_path):
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    dead = fake.start_session("p", "IMG_1.HEIC", len(PAYLOAD), "image/heic", {})
    fake.expire_session(dead)
    result = transfer_one(tmp_path, fake, session_uri=dead)
    assert result.md5 == hashlib.md5(PAYLOAD).hexdigest()


def test_resuming_asks_drive_where_the_session_actually_is(tmp_path):
    """The persisted offset is a hint. Drive is the authority."""
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    live = fake.start_session("p", "IMG_1.HEIC", len(PAYLOAD), "image/heic", {})
    fake.send_chunk(live, PAYLOAD[:1000], 0, len(PAYLOAD))
    result = transfer_one(tmp_path, fake, session_uri=live)
    assert fake.get_file(result.drive_file_id).md5 == hashlib.md5(PAYLOAD).hexdigest()


def test_giving_up_after_too_many_transient_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "BASE_DELAY", 0)      # keep the test quick
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    fake.fail_chunks = transfer.MAX_CHUNK_ATTEMPTS + 1
    with pytest.raises(transfer.TransferError) as exc:
        transfer_one(tmp_path, fake)
    assert exc.value.stage == "upload"


def test_mime_type_is_guessed_from_the_name():
    assert transfer.mime_for("IMG_1.HEIC") == "image/heic"
    assert transfer.mime_for("IMG_2.MOV") == "video/quicktime"
    assert transfer.mime_for("odd.xyzzy") == "application/octet-stream"


def test_stored_entries_are_handled_too(tmp_path):
    """Takeout deflates everything, but a stored entry must not silently break."""
    data = build_zip({"Takeout/x/IMG_3.PNG": PAYLOAD}, compress=False)
    read_range = reader_for(data)
    entry = read_central_directory(read_range, len(data))[0]
    dest = tmp_path / "out.bin"
    transfer.spool_entry(read_range, entry, dest)
    assert dest.read_bytes() == PAYLOAD
    assert zlib.crc32(dest.read_bytes()) == entry.crc32


def test_a_claimed_part_file_is_named_after_the_photo(tmp_path):
    path = transfer.claim_part_path(tmp_path, "IMG_1.HEIC")
    assert path.name == "IMG_1.HEIC.part"
    assert path.exists()


def test_a_second_claim_of_the_same_name_gets_a_number(tmp_path):
    first = transfer.claim_part_path(tmp_path, "IMG_1.HEIC")
    second = transfer.claim_part_path(tmp_path, "IMG_1.HEIC")
    assert first.name == "IMG_1.HEIC.part"
    assert second.name == "IMG_1.HEIC.2.part"


def test_a_separator_in_the_name_cannot_escape_the_folder(tmp_path):
    path = transfer.claim_part_path(tmp_path, "2023/IMG_1.HEIC")
    assert path.parent == tmp_path
    assert path.name == "2023_IMG_1.HEIC.part"


def test_claiming_gives_up_rather_than_spinning(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_NAME_ATTEMPTS", 3)
    for _ in range(3):
        transfer.claim_part_path(tmp_path, "IMG_1.HEIC")
    with pytest.raises(transfer.TransferError) as exc:
        transfer.claim_part_path(tmp_path, "IMG_1.HEIC")
    assert exc.value.stage == "read"


def test_the_spooled_file_is_visible_under_its_real_name(tmp_path):
    """on_session fires after spooling, so the folder is at its fullest."""
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    seen: list[str] = []
    transfer_one(
        tmp_path, fake,
        on_session=lambda uri: seen.extend(p.name for p in tmp_path.iterdir()),
    )
    assert seen == ["IMG_1.HEIC.part"]


def test_the_spool_file_is_reported_as_it_is_claimed(tmp_path):
    fake = FakeDrive()
    fake.add_folder("p", "2023-11")
    claimed: list = []
    transfer_one(tmp_path, fake, on_spool=claimed.append)
    assert [p.name for p in claimed] == ["IMG_1.HEIC.part"]
