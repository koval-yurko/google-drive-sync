# Visible Download Folder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make in-progress downloads visible — a per-run `downloads/<timestamp>/` folder holding readably-named `.part` files, plus a live table on the Organize page showing what is downloading and what is uploading.

**Architecture:** Organize already spools each file to disk before uploading it. Three changes: the spool folder moves from the hidden, shared, auto-wiped `.cache/spool` to a per-run `downloads/2026-08-10_14-32-05/`; `.part` files take the photo's own name, claimed atomically so concurrent workers cannot collide; and a small in-process registry records each live transfer so `GET /api/downloads` can merge it with a `stat()` of the folder and hand the UI both phases.

**Tech Stack:** Python 3.12 (uv, FastAPI, pytest), React 19 + TypeScript (Vite, vitest, @testing-library/react).

## Global Constraints

- Python 3.12, run everything through `uv run`. Backend tests must stay offline — no network, ever. Drive is faked by `tests/fakes/fake_drive.py`.
- Backend tests: `uv run pytest`. Frontend tests: `cd web && npm test`. Frontend lint: `cd web && npm run lint`.
- Spooled files are still deleted the moment their upload verifies. This feature shows work in progress; it is not an archive.
- No configurable download path — no setting, no environment variable. The folder is `downloads/` under `Config.repo_root`.
- A run never deletes another run's non-empty folder.
- `ProgressEvent.level` is only ever `"info"` or `"error"`. Do not invent new levels — the frontend styles only `.error`.
- Every task ends with tests passing and a commit.

**Two deliberate deviations from the spec:**

1. The spec named the new module `photolib/jobs/inflight.py`. This plan puts it at `photolib/downloads.py`, because it owns the folder-scanning helpers as well as the registry, and those are not job-runner concerns.
2. `transfer_entry` gains one optional keyword, `on_spool`. The registry needs the path of the `.part` file, and `transfer_entry` is what chooses it. The alternative — moving the claim out to the caller — would have changed the contract far more, so the additive hook wins. `spool_dir` keeps its meaning and position, so every existing call site and test is unaffected.

---

### Task 1: `downloads_dir` on Config

`Config` is a frozen dataclass with no defaults, constructed directly by four test fixtures. Adding a field means updating all four — that is the whole task.

**Files:**
- Modify: `photolib/config.py:10-28`
- Modify: `tests/test_config.py:7-15`
- Modify: `tests/test_api_thumbs.py:21-27`, `tests/test_api_library.py:11`, `tests/test_api_tags.py:11`, `tests/test_action_sync_tags.py:28`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.downloads_dir: Path`, equal to `repo_root / "downloads"`.

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`, add one assertion to `test_load_defaults_to_repo_root`:

```python
    assert cfg.downloads_dir == tmp_path / "downloads"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'downloads_dir'`

- [ ] **Step 3: Add the field**

In `photolib/config.py`, add the field to the dataclass and set it in `load`:

```python
@dataclass(frozen=True)
class Config:
    repo_root: Path
    db_path: Path
    credentials_path: Path
    token_path: Path
    thumbnail_cache_dir: Path
    downloads_dir: Path

    @classmethod
    def load(cls) -> "Config":
        env_home = os.environ.get("PHOTOLIB_HOME")
        root = Path(env_home) if env_home else Path(__file__).resolve().parent.parent
        return cls(
            repo_root=root,
            db_path=root / "photolib.db",
            credentials_path=root / "credentials.json",
            token_path=root / "token.json",
            thumbnail_cache_dir=root / ".cache" / "thumbnails",
            downloads_dir=root / "downloads",
        )
```

- [ ] **Step 4: Update the four fixtures that build a Config by hand**

Each of these already passes five keyword arguments. Add a sixth to every one of them:

```python
        downloads_dir=tmp_path / "downloads",
```

The four call sites are `tests/test_api_thumbs.py:21`, `tests/test_api_library.py:11`, `tests/test_api_tags.py:11`, and `tests/test_action_sync_tags.py:28`. Put the new line directly after the `thumbnail_cache_dir=` line in each.

- [ ] **Step 5: Ignore the folder**

Add to `.gitignore`, directly under the `.cache/` line:

```
downloads/
```

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS. A `TypeError: Config.__init__() missing 1 required positional argument` means a fixture was missed in Step 4.

- [ ] **Step 7: Commit**

```bash
git add photolib/config.py tests/test_config.py tests/test_api_thumbs.py \
        tests/test_api_library.py tests/test_api_tags.py \
        tests/test_action_sync_tags.py .gitignore
git commit -m "feat: add downloads_dir to Config"
```

---

### Task 2: `.part` files named after the photo

`transfer_entry` currently spools to `tempfile.mkstemp(...)`, producing `tmpXXXXXX.part`. Replace that with a name taken from the file being moved. The folder is flat and four workers run at once, so two entries sharing a `target_name` in different months can race for the same path — the claim must be atomic, not a check-then-create.

**Files:**
- Modify: `photolib/transfer.py:16-31` (imports, constants), `:170-190` (`transfer_entry`)
- Test: `tests/test_transfer.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `transfer.claim_part_path(spool_dir: Path, name: str) -> Path` — creates an empty file and returns its path.
  - `transfer_entry(..., on_spool: Callable[[Path], None] | None = None)` — new optional keyword, called with the claimed path right after it is claimed. `spool_dir` keeps its existing meaning and position.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_transfer.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_transfer.py -v`
Expected: FAIL with `AttributeError: module 'photolib.transfer' has no attribute 'claim_part_path'`, and `TypeError: transfer_entry() got an unexpected keyword argument 'on_spool'`.

- [ ] **Step 3: Implement the claim**

In `photolib/transfer.py`, add the constant next to the other module constants (near line 31):

```python
MAX_NAME_ATTEMPTS = 1000
```

Add the function above `transfer_entry`:

```python
def claim_part_path(spool_dir: Path, name: str) -> Path:
    """Create an empty `.part` file named for `name`, and return its path.

    O_EXCL makes the claim atomic. Two workers moving files that share a name
    into different months would otherwise pick the same path and corrupt each
    other's bytes; check-then-create would only narrow the window, not close it.
    """
    safe = name.replace("/", "_").replace(os.sep, "_")
    for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
        suffix = ".part" if attempt == 1 else f".{attempt}.part"
        candidate = spool_dir / f"{safe}{suffix}"
        try:
            handle = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(handle)
        return candidate
    raise TransferError(f"no free spool name for {name!r}", "read")
```

- [ ] **Step 4: Use it in `transfer_entry`**

Replace lines 185-188 of `photolib/transfer.py`:

```python
    spool_dir.mkdir(parents=True, exist_ok=True)
    handle, raw_path = tempfile.mkstemp(dir=spool_dir, suffix=".part")
    os.close(handle)
    spooled = Path(raw_path)
```

with:

```python
    spool_dir.mkdir(parents=True, exist_ok=True)
    spooled = claim_part_path(spool_dir, name)
    if on_spool:
        on_spool(spooled)
```

Add the parameter to the signature, after `on_session`:

```python
    on_spool: Callable[[Path], None] | None = None,
```

Delete the now-unused `import tempfile` at line 20.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_transfer.py -v`
Expected: PASS, all of them.

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS. `tests/test_action_organize.py` still passes — the spool folder is still `.cache/spool` at this point, only the filenames inside it changed.

- [ ] **Step 7: Commit**

```bash
git add photolib/transfer.py tests/test_transfer.py
git commit -m "feat: name spooled files after the photo being moved"
```

---

### Task 3: The downloads module — registry and folder helpers

One module owning everything about the downloads folder: what is moving through it right now, and what earlier runs left behind. No FastAPI, no SQLite, no threads started here — just a lock and some `stat()` calls, so every behaviour is a plain unit test.

**Files:**
- Create: `photolib/downloads.py`
- Test: `tests/test_downloads.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Transfer` — frozen dataclass: `key: str`, `name: str`, `destination: str`, `expected_size: int`, `path: Path`, `uploaded: int = 0`
  - `TransferView` — frozen dataclass: `name: str`, `phase: str`, `bytes: int`, `total: int`, `destination: str`
  - `InflightRegistry` — `open_run(path)`, `close_run()`, `run_dir` property, `start(key, *, name, destination, expected_size, path)`, `uploaded(key, offset)`, `finish(key)`, `snapshot() -> list[Transfer]`
  - `observe(transfers: list[Transfer]) -> list[TransferView]`
  - `run_folder_name(started: datetime) -> str`
  - `sweep_empty(root: Path, keep: Path) -> list[dict]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_downloads.py`:

```python
"""The downloads folder: what is moving, and what an earlier run left."""

import threading
from datetime import datetime

from photolib.downloads import (
    InflightRegistry,
    observe,
    run_folder_name,
    sweep_empty,
)


def part(tmp_path, name: str, size: int):
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    return path


def registered(tmp_path, *, on_disk: int, expected: int, uploaded: int = 0):
    registry = InflightRegistry()
    path = part(tmp_path, "IMG_1.HEIC.part", on_disk)
    registry.start(
        "e1", name="IMG_1.HEIC", destination="Photos/2023-11",
        expected_size=expected, path=path,
    )
    if uploaded:
        registry.uploaded("e1", uploaded)
    return registry


def test_a_started_transfer_appears_in_the_snapshot(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    [live] = registry.snapshot()
    assert live.name == "IMG_1.HEIC"
    assert live.destination == "Photos/2023-11"
    assert live.expected_size == 100
    assert live.uploaded == 0


def test_finishing_removes_the_transfer(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    registry.finish("e1")
    assert registry.snapshot() == []


def test_reporting_progress_for_an_unknown_key_is_harmless(tmp_path):
    registry = InflightRegistry()
    registry.uploaded("nobody", 500)          # must not raise
    assert registry.snapshot() == []


def test_a_partly_downloaded_file_reads_as_downloading(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    [view] = observe(registry.snapshot())
    assert view.phase == "downloading"
    assert view.bytes == 10
    assert view.total == 100


def test_a_fully_downloaded_file_reads_as_uploading(tmp_path):
    registry = registered(tmp_path, on_disk=100, expected=100, uploaded=40)
    [view] = observe(registry.snapshot())
    assert view.phase == "uploading"
    assert view.bytes == 40
    assert view.total == 100


def test_a_file_that_vanished_mid_poll_is_dropped(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=100)
    (tmp_path / "IMG_1.HEIC.part").unlink()
    assert observe(registry.snapshot()) == []


def test_concurrent_progress_reports_leave_a_consistent_snapshot(tmp_path):
    registry = registered(tmp_path, on_disk=10, expected=1000)

    def report(base: int) -> None:
        for offset in range(base, base + 100):
            registry.uploaded("e1", offset)

    threads = [threading.Thread(target=report, args=(n * 100,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    [live] = registry.snapshot()
    assert 0 <= live.uploaded < 800


def test_the_open_run_is_reported_and_then_cleared(tmp_path):
    registry = InflightRegistry()
    assert registry.run_dir is None
    registry.open_run(tmp_path / "2026-08-10_14-32-05")
    assert registry.run_dir == tmp_path / "2026-08-10_14-32-05"
    registry.close_run()
    assert registry.run_dir is None


def test_a_run_folder_is_named_for_its_start_time():
    assert run_folder_name(datetime(2026, 8, 10, 14, 32, 5)) == "2026-08-10_14-32-05"


def test_sweeping_deletes_empty_leftovers_and_keeps_full_ones(tmp_path):
    keep = tmp_path / "2026-08-10_14-32-05"
    keep.mkdir()
    (tmp_path / "2026-08-09_10-00-00").mkdir()
    full = tmp_path / "2026-08-09_22-14-01"
    full.mkdir()
    (full / "IMG_9.HEIC.part").write_bytes(b"x" * 2048)

    stale = sweep_empty(tmp_path, keep=keep)

    assert not (tmp_path / "2026-08-09_10-00-00").exists()
    assert (full / "IMG_9.HEIC.part").exists()
    assert stale == [{"dir": "2026-08-09_22-14-01", "files": 1, "bytes": 2048}]


def test_sweeping_never_touches_the_folder_it_is_told_to_keep(tmp_path):
    keep = tmp_path / "2026-08-10_14-32-05"
    keep.mkdir()
    assert sweep_empty(tmp_path, keep=keep) == []
    assert keep.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_downloads.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'photolib.downloads'`

- [ ] **Step 3: Write the module**

Create `photolib/downloads.py`:

```python
"""The downloads folder: what is moving through it, and what was left behind.

The registry is the only mutable state, and it is the only thing the worker
threads touch. Everything else is a `stat()` of a folder, so what the UI shows
is what is actually on disk.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Transfer:
    """One file being moved, as the mover sees it."""

    key: str
    name: str
    destination: str
    expected_size: int
    path: Path
    uploaded: int = 0


@dataclass(frozen=True)
class TransferView:
    """One file being moved, as a watcher sees it."""

    name: str
    phase: str          # 'downloading' | 'uploading'
    bytes: int
    total: int
    destination: str


class InflightRegistry:
    """Live transfers, keyed by entry id. Safe to call from worker threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, Transfer] = {}
        self._run_dir: Path | None = None

    def open_run(self, path: Path) -> None:
        with self._lock:
            self._run_dir = path

    def close_run(self) -> None:
        with self._lock:
            self._run_dir = None

    @property
    def run_dir(self) -> Path | None:
        with self._lock:
            return self._run_dir

    def start(
        self,
        key: str,
        *,
        name: str,
        destination: str,
        expected_size: int,
        path: Path,
    ) -> None:
        with self._lock:
            self._live[key] = Transfer(
                key=key, name=name, destination=destination,
                expected_size=expected_size, path=path,
            )

    def uploaded(self, key: str, offset: int) -> None:
        """Record upload progress. A key that has finished is not an error —
        the callback and the `finally` that clears it are on different clocks."""
        with self._lock:
            live = self._live.get(key)
            if live is not None:
                self._live[key] = replace(live, uploaded=offset)

    def finish(self, key: str) -> None:
        with self._lock:
            self._live.pop(key, None)

    def snapshot(self) -> list[Transfer]:
        with self._lock:
            return list(self._live.values())


def observe(transfers: list[Transfer]) -> list[TransferView]:
    """Merge the registry with the bytes actually on disk.

    A file whose `.part` has just been unlinked is dropped rather than raising:
    that race is the normal end of every transfer, not a fault.
    """
    views: list[TransferView] = []
    for live in transfers:
        try:
            on_disk = live.path.stat().st_size
        except OSError:
            continue
        downloading = on_disk < live.expected_size
        views.append(TransferView(
            name=live.name,
            phase="downloading" if downloading else "uploading",
            bytes=on_disk if downloading else live.uploaded,
            total=live.expected_size,
            destination=live.destination,
        ))
    return views


def run_folder_name(started: datetime) -> str:
    """Sortable, filesystem-safe, and readable at a glance in Finder."""
    return started.strftime("%Y-%m-%d_%H-%M-%S")


def sweep_empty(root: Path, keep: Path) -> list[dict]:
    """Delete leftover run folders that hold nothing; report the ones that do.

    An empty folder carries no information, so removing it costs nothing. A
    folder holding bytes is the only evidence a crashed run leaves, so it is
    reported and left for its owner to delete.
    """
    stale: list[dict] = []
    for folder in sorted(root.iterdir()):
        if folder == keep or not folder.is_dir():
            continue
        files = [f for f in folder.iterdir() if f.is_file()]
        if not files:
            folder.rmdir()
            continue
        stale.append({
            "dir": folder.name,
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
        })
    return stale
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_downloads.py -v`
Expected: PASS, all twelve.

- [ ] **Step 5: Commit**

```bash
git add photolib/downloads.py tests/test_downloads.py
git commit -m "feat: add the in-flight registry and downloads folder helpers"
```

---

### Task 4: Organize writes to a per-run folder

Replace the shared, auto-wiped `.cache/spool` with `downloads/<timestamp>/`. The point is that a crashed run stays identifiable instead of being swept by the next one.

**Files:**
- Modify: `photolib/actions/organize.py:15-29` (imports), `:131-134` (folder setup), `:210` (cleanup)
- Test: `tests/test_action_organize.py`

**Interfaces:**
- Consumes: `photolib.downloads.run_folder_name`, `photolib.downloads.sweep_empty` (Task 3); `Config.downloads_dir` (Task 1).
- Produces: nothing new that a later task calls. Task 5 edits the same region.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_action_organize.py` (add `import re` at the top):

```python
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")


def test_the_run_gets_its_own_stamped_folder(ctx):
    events = run(ctx, Params())
    next(events)                                   # the "Uploading N file(s)" event
    live = [p.name for p in ctx.config.downloads_dir.iterdir()]
    assert len(live) == 1
    assert STAMP.fullmatch(live[0])
    list(events)                                   # drain the run


def test_the_run_folder_is_removed(ctx):
    list(run(ctx, Params()))
    assert list(ctx.config.downloads_dir.iterdir()) == []


def test_an_empty_leftover_folder_is_pruned(ctx):
    orphan = ctx.config.downloads_dir / "2026-08-09_10-00-00"
    orphan.mkdir(parents=True)
    list(run(ctx, Params()))
    assert not orphan.exists()


def test_a_leftover_folder_holding_bytes_is_kept_and_reported(ctx):
    orphan = ctx.config.downloads_dir / "2026-08-09_22-14-01"
    orphan.mkdir(parents=True)
    (orphan / "IMG_9.HEIC.part").write_bytes(b"x" * 2048)

    messages = [event.message for event in run(ctx, Params())]

    assert (orphan / "IMG_9.HEIC.part").exists()
    assert any("2026-08-09_22-14-01" in m for m in messages)


def test_an_unusable_downloads_folder_stops_the_run_before_any_upload(ctx):
    ctx.config.downloads_dir.parent.mkdir(parents=True, exist_ok=True)
    ctx.config.downloads_dir.write_bytes(b"not a folder")

    events = list(run(ctx, Params()))

    assert events[-1].level == "error"
    assert uploaded_names(ctx) == {}
```

Delete the existing `test_the_spool_directory_is_left_empty` — `test_the_run_folder_is_removed` replaces it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_action_organize.py -v`
Expected: FAIL — `FileNotFoundError` on `downloads_dir.iterdir()`, because Organize still writes to `.cache/spool`.

- [ ] **Step 3: Create the run folder**

In `photolib/actions/organize.py`, replace lines 131-134:

```python
    spool_dir = Path(ctx.config.repo_root) / ".cache" / "spool"
    if spool_dir.exists():
        shutil.rmtree(spool_dir)        # sweep anything a crash orphaned
    spool_dir.mkdir(parents=True, exist_ok=True)
```

with:

```python
    downloads_root = Path(ctx.config.downloads_dir)
    run_dir = downloads_root / run_folder_name(datetime.now())
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        yield ProgressEvent(
            f"Cannot use the downloads folder {downloads_root}: {exc}",
            progress=1.0,
            level="error",
        )
        return

    # Another run's leftovers are evidence, not litter. Empty folders say
    # nothing, so they go; folders holding bytes are reported and left alone.
    for stale in sweep_empty(downloads_root, keep=run_dir):
        yield ProgressEvent(
            f"An earlier run left {stale['files']} unfinished file(s), "
            f"{stale['bytes'] / 1e9:.2f} GB, in downloads/{stale['dir']}/."
        )
```

Update the imports at the top of the file: drop `import shutil`, and add

```python
from photolib.downloads import run_folder_name, sweep_empty
```

`datetime` is already imported at line 20.

- [ ] **Step 4: Pass the run folder to the movers**

At line 166, `spool_dir=spool_dir` becomes:

```python
            spool_dir=run_dir,
```

- [ ] **Step 5: Replace the end-of-run cleanup**

Replace line 210, `shutil.rmtree(spool_dir, ignore_errors=True)`, with:

```python
    leftover = [f for f in run_dir.iterdir() if f.is_file()]
    if leftover:
        yield ProgressEvent(
            f"{len(leftover)} unfinished file(s) left in "
            f"downloads/{run_dir.name}/."
        )
    else:
        run_dir.rmdir()
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_action_organize.py -v`
Expected: PASS, including the five new tests.

- [ ] **Step 7: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add photolib/actions/organize.py tests/test_action_organize.py
git commit -m "feat: give each Organize run its own visible downloads folder"
```

---

### Task 5: Organize reports live transfers to the registry

Wire the registry through the action context and into the worker threads. `ctx.inflight` is optional, so Organize falls back to a private registry when nobody supplied one — that keeps the reporting code free of `if inflight is not None` at every call site.

**Files:**
- Modify: `photolib/actions/base.py:31-39` (`ActionContext`)
- Modify: `photolib/actions/organize.py` (`run`, `move`)
- Test: `tests/test_action_organize.py`

**Interfaces:**
- Consumes: `InflightRegistry` (Task 3); `transfer_entry(..., on_spool=...)` (Task 2).
- Produces: `ActionContext.inflight: object | None = None` — the field `create_app` fills in Task 6.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_action_organize.py` (add `from photolib.downloads import InflightRegistry, observe` at the top):

```python
def test_a_live_transfer_is_visible_while_it_moves(ctx, monkeypatch):
    """The registry is read from another thread, so read it from this one."""
    registry = InflightRegistry()
    ctx.inflight = registry
    seen: list = []

    original = organize.transfer_entry

    def spy(**kwargs):
        on_spool = kwargs.pop("on_spool")

        def watch(path):
            on_spool(path)
            seen.extend(observe(registry.snapshot()))

        return original(on_spool=watch, **kwargs)

    monkeypatch.setattr(organize, "transfer_entry", spy)
    list(run(ctx, Params(workers=1)))

    assert {view.name for view in seen} == {"IMG_1.HEIC", "IMG_2.MOV"}
    assert {view.destination for view in seen} == {"Photos/2023-11", "Photos/2019-01"}
    assert all(view.total > 0 for view in seen)


def test_the_registry_is_empty_when_the_run_ends(ctx):
    registry = InflightRegistry()
    ctx.inflight = registry
    list(run(ctx, Params()))
    assert registry.snapshot() == []
    assert registry.run_dir is None


def test_a_failed_transfer_leaves_no_ghost(ctx, monkeypatch):
    registry = InflightRegistry()
    ctx.inflight = registry

    def explode(**kwargs):
        kwargs["on_spool"](ctx.config.downloads_dir / "ghost.part")
        raise TransferError("no", "upload")

    monkeypatch.setattr(organize, "transfer_entry", explode)
    list(run(ctx, Params(workers=1)))

    assert registry.snapshot() == []
```

`organize.py` does `from photolib.transfer import transfer_entry`, so
`monkeypatch.setattr(organize, "transfer_entry", ...)` reaches the name the
action actually calls. Add these imports to the test file too:

```python
from photolib.actions import organize
from photolib.transfer import TransferError
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_action_organize.py -v`
Expected: FAIL. `ActionContext` is a plain dataclass, so `ctx.inflight = registry` is accepted silently; the failure is the assertion — `seen` stays empty and `set() != {'IMG_1.HEIC', 'IMG_2.MOV'}` — because nothing reports to the registry yet, and `explode` raises `TypeError` on the missing `on_spool` key.

- [ ] **Step 3: Add the field to ActionContext**

In `photolib/actions/base.py`, extend the dataclass. Use `object | None`, matching how `drive` and `writer` are already typed — the context deliberately does not import its collaborators:

```python
@dataclass
class ActionContext:
    """Everything an action is allowed to reach."""

    conn: sqlite3.Connection
    drive: object
    settings: SettingsRepo
    config: Config
    writer: object | None = None
    """Whatever may mutate Drive. None in a read-only context."""
    inflight: object | None = None
    """Where live transfers report themselves. None when nobody is watching."""
```

- [ ] **Step 4: Report from the worker threads**

In `photolib/actions/organize.py`, add the import:

```python
from photolib.downloads import InflightRegistry, run_folder_name, sweep_empty
```

Just after the `run_dir` is created and swept, bind the registry:

```python
    # A private registry when nobody supplied one, so the reporting below has
    # no None to step around.
    inflight = ctx.inflight or InflightRegistry()
    inflight.open_run(run_dir)
```

Replace the `move` function (lines 155-169) with:

```python
    def move(row):
        """Runs on a worker thread. No database access here."""
        entry = _entry_of(row)
        archive_id = row["archive_drive_id"]
        key = str(row["entry_id"])
        destination = f"{photos_root.name}/{row['target_folder']}"
        try:
            return transfer_entry(
                read_range=lambda s, e: ctx.drive.read_range(archive_id, s, e),
                entry=entry,
                writer=ctx.writer,
                parent_id=folders[row["target_folder"]],
                name=row["target_name"],
                properties=_properties(row),
                spool_dir=run_dir,
                session_uri=row["upload_session_uri"],
                on_session=lambda uri: sessions.put((row["entry_id"], uri)),
                on_spool=lambda path: inflight.start(
                    key,
                    name=row["target_name"],
                    destination=destination,
                    expected_size=row["size"],
                    path=path,
                ),
                on_progress=lambda offset: inflight.uploaded(key, offset),
            )
        finally:
            inflight.finish(key)
```

- [ ] **Step 5: Close the run**

In the end-of-run block from Task 4, call `inflight.close_run()` before the leftover check:

```python
    inflight.close_run()
    leftover = [f for f in run_dir.iterdir() if f.is_file()]
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_action_organize.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add photolib/actions/base.py photolib/actions/organize.py \
        tests/test_action_organize.py
git commit -m "feat: report live transfers to the in-flight registry"
```

---

### Task 6: `GET /api/downloads`

One read-only endpoint that merges the registry with the folder. It follows the shape of every other route module here: a router, a handler reading `request.app.state`, no business logic.

**Files:**
- Create: `photolib/api/routes_downloads.py`
- Modify: `photolib/api/app.py:44-46` (`context_factory`), `:60-73` (state), `:75-93` (routers)
- Test: `tests/test_api_downloads.py` (new)

**Interfaces:**
- Consumes: `InflightRegistry`, `observe`, `sweep_empty`'s sibling reporting (Task 3); `Config.downloads_dir` (Task 1).
- Produces: `GET /api/downloads` returning `{"run_dir": str | null, "files": [...], "stale_runs": [...]}`; `app.state.inflight`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_downloads.py`:

```python
import pytest
from fastapi.testclient import TestClient

from photolib.api.app import create_app
from photolib.config import Config
from tests.fakes.fake_drive import FakeDrive


@pytest.fixture
def config(tmp_path):
    return Config(
        repo_root=tmp_path,
        db_path=tmp_path / "test.db",
        credentials_path=tmp_path / "credentials.json",
        token_path=tmp_path / "token.json",
        thumbnail_cache_dir=tmp_path / "thumbs",
        downloads_dir=tmp_path / "downloads",
    )


@pytest.fixture
def client(config):
    with TestClient(create_app(config=config, drive=FakeDrive())) as test_client:
        yield test_client


def start_a_run(app, config, *, on_disk: int, expected: int):
    run_dir = config.downloads_dir / "2026-08-10_14-32-05"
    run_dir.mkdir(parents=True)
    path = run_dir / "IMG_1.HEIC.part"
    path.write_bytes(b"x" * on_disk)
    app.state.inflight.open_run(run_dir)
    app.state.inflight.start(
        "e1", name="IMG_1.HEIC", destination="Photos/2023-11",
        expected_size=expected, path=path,
    )
    return run_dir


def test_nothing_running_reports_nothing(client):
    body = client.get("/api/downloads").json()
    assert body == {"run_dir": None, "files": [], "stale_runs": []}


def test_a_downloading_file_is_reported_with_its_bytes(client, config):
    start_a_run(client.app, config, on_disk=25, expected=100)
    body = client.get("/api/downloads").json()
    assert body["run_dir"] == "downloads/2026-08-10_14-32-05"
    assert body["files"] == [{
        "name": "IMG_1.HEIC",
        "phase": "downloading",
        "bytes": 25,
        "total": 100,
        "destination": "Photos/2023-11",
    }]


def test_a_fully_spooled_file_is_reported_as_uploading(client, config):
    start_a_run(client.app, config, on_disk=100, expected=100)
    client.app.state.inflight.uploaded("e1", 60)
    [file] = client.get("/api/downloads").json()["files"]
    assert file["phase"] == "uploading"
    assert file["bytes"] == 60


def test_an_earlier_run_holding_bytes_is_reported(client, config):
    start_a_run(client.app, config, on_disk=25, expected=100)
    orphan = config.downloads_dir / "2026-08-09_22-14-01"
    orphan.mkdir(parents=True)
    (orphan / "IMG_9.HEIC.part").write_bytes(b"x" * 2048)

    body = client.get("/api/downloads").json()

    assert body["stale_runs"] == [
        {"dir": "2026-08-09_22-14-01", "files": 1, "bytes": 2048}
    ]


def test_reporting_never_deletes_anything(client, config):
    """The endpoint is a reader. Empty leftovers are the running action's job."""
    orphan = config.downloads_dir / "2026-08-09_10-00-00"
    orphan.mkdir(parents=True)
    client.get("/api/downloads")
    assert orphan.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_downloads.py -v`
Expected: FAIL with 404 on `/api/downloads`.

- [ ] **Step 3: Add a read-only stale scan**

The endpoint must not delete anything, so it needs the reporting half of `sweep_empty` without the sweeping. Add to `photolib/downloads.py`:

```python
def stale_runs(root: Path, active: Path | None) -> list[dict]:
    """Report leftover run folders holding bytes. Deletes nothing."""
    if not root.is_dir():
        return []
    found: list[dict] = []
    for folder in sorted(root.iterdir()):
        if folder == active or not folder.is_dir():
            continue
        files = [f for f in folder.iterdir() if f.is_file()]
        if not files:
            continue
        found.append({
            "dir": folder.name,
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
        })
    return found
```

Then rewrite `sweep_empty` to reuse it, so the two can never disagree about what counts as stale:

```python
def sweep_empty(root: Path, keep: Path) -> list[dict]:
    """Delete leftover run folders that hold nothing; report the ones that do.

    An empty folder carries no information, so removing it costs nothing. A
    folder holding bytes is the only evidence a crashed run leaves, so it is
    reported and left for its owner to delete.
    """
    if not root.is_dir():
        return []
    for folder in sorted(root.iterdir()):
        if folder == keep or not folder.is_dir():
            continue
        if not any(f.is_file() for f in folder.iterdir()):
            folder.rmdir()
    return stale_runs(root, active=keep)
```

- [ ] **Step 4: Write the route**

Create `photolib/api/routes_downloads.py`:

```python
"""What is moving through the downloads folder right now."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request

from photolib.downloads import observe, stale_runs

router = APIRouter(tags=["downloads"])


@router.get("/downloads")
def list_downloads(request: Request) -> dict:
    registry = request.app.state.inflight
    root = request.app.state.config.downloads_dir
    run_dir = registry.run_dir
    return {
        "run_dir": f"{root.name}/{run_dir.name}" if run_dir else None,
        "files": [asdict(view) for view in observe(registry.snapshot())],
        "stale_runs": stale_runs(root, active=run_dir),
    }
```

- [ ] **Step 5: Wire it into the app**

In `photolib/api/app.py`, add the import next to the others at the top:

```python
from photolib.downloads import InflightRegistry
```

Create the registry beside `broker` (around line 33):

```python
    inflight = InflightRegistry()
```

Pass it into the context factory:

```python
    def context_factory() -> ActionContext:
        return ActionContext(
            conn=conn, drive=drive_client, settings=settings, config=cfg,
            writer=drive_writer, inflight=inflight,
        )
```

Expose it on app state, next to `app.state.broker`:

```python
    app.state.inflight = inflight
```

Add `routes_downloads` to the deferred import list and include it:

```python
    app.include_router(routes_downloads.router, prefix="/api")
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_api_downloads.py tests/test_downloads.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole backend suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add photolib/downloads.py photolib/api/routes_downloads.py \
        photolib/api/app.py tests/test_api_downloads.py
git commit -m "feat: serve live download progress at GET /api/downloads"
```

---

### Task 7: The in-flight table component

A presentational component: it renders what it is handed and fetches nothing. Polling belongs to the page (Task 8), which keeps this testable without timers.

**Files:**
- Modify: `web/src/api/types.ts`, `web/src/api/client.ts`
- Create: `web/src/components/InflightTable.tsx`, `web/src/components/InflightTable.test.tsx`

**Interfaces:**
- Consumes: the JSON shape from Task 6.
- Produces:
  - types: `InflightFile`, `StaleRun`, `Downloads`
  - `getDownloads(): Promise<Downloads>`
  - `<InflightTable downloads={downloads} />`

- [ ] **Step 1: Write the failing test**

Create `web/src/components/InflightTable.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { InflightTable } from './InflightTable'
import type { Downloads } from '../api/types'

const EMPTY: Downloads = { run_dir: null, files: [], stale_runs: [] }

const BUSY: Downloads = {
  run_dir: 'downloads/2026-08-10_14-32-05',
  files: [
    {
      name: 'IMG_1234.HEIC',
      phase: 'downloading',
      bytes: 1_258_291,
      total: 3_565_158,
      destination: 'Photos/2025-07',
    },
    {
      name: 'IMG_1240.MOV',
      phase: 'uploading',
      bytes: 18_874_368,
      total: 44_145_213,
      destination: 'Photos/2025-08',
    },
  ],
  stale_runs: [],
}

describe('InflightTable', () => {
  it('renders nothing when no file is moving', () => {
    const { container } = render(<InflightTable downloads={EMPTY} />)
    expect(container.textContent).toBe('')
  })

  it('shows each file, its phase, and where it is going', () => {
    render(<InflightTable downloads={BUSY} />)
    expect(screen.getByText('IMG_1234.HEIC')).toBeTruthy()
    expect(screen.getByText('downloading')).toBeTruthy()
    expect(screen.getByText('IMG_1240.MOV')).toBeTruthy()
    expect(screen.getByText('uploading')).toBeTruthy()
    expect(screen.getByText('Photos/2025-08')).toBeTruthy()
  })

  it('shows how far each file has got', () => {
    render(<InflightTable downloads={BUSY} />)
    expect(screen.getByText('1.3 / 3.6 MB')).toBeTruthy()
    expect(screen.getByText('18.9 / 44.1 MB')).toBeTruthy()
  })

  it('warns about bytes an earlier run left behind', () => {
    render(
      <InflightTable
        downloads={{
          ...EMPTY,
          stale_runs: [{ dir: '2026-08-09_22-14-01', files: 3, bytes: 1_503_238_553 }],
        }}
      />,
    )
    expect(screen.getByText(/2026-08-09_22-14-01/)).toBeTruthy()
    expect(screen.getByText(/1\.50 GB/)).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/InflightTable.test.tsx`
Expected: FAIL — cannot resolve `./InflightTable`.

- [ ] **Step 3: Add the types**

Append to `web/src/api/types.ts`:

```ts
export interface InflightFile {
  name: string
  phase: 'downloading' | 'uploading'
  bytes: number
  total: number
  destination: string
}

export interface StaleRun {
  dir: string
  files: number
  bytes: number
}

export interface Downloads {
  run_dir: string | null
  files: InflightFile[]
  stale_runs: StaleRun[]
}
```

- [ ] **Step 4: Add the client call**

In `web/src/api/client.ts`, add `Downloads` to the type import block. The list is alphabetical, so it goes immediately before `DriveFolder`. Then add the call next to the other `GET` helpers:

```ts
export const getDownloads = () => request<Downloads>('/api/downloads')
```

- [ ] **Step 5: Write the component**

Create `web/src/components/InflightTable.tsx`:

```tsx
import type { Downloads } from '../api/types'

const mb = (bytes: number) => (bytes / 1e6).toFixed(1)
const gb = (bytes: number) => (bytes / 1e9).toFixed(2)

export function InflightTable({ downloads }: { downloads: Downloads }) {
  const { files, stale_runs: stale } = downloads
  if (files.length === 0 && stale.length === 0) return null

  return (
    <div className="card">
      {stale.map((run) => (
        <p key={run.dir} className="error">
          An earlier run left {run.files} unfinished file(s), {gb(run.bytes)} GB,
          in downloads/{run.dir}/.
        </p>
      ))}
      {files.map((file) => (
        <div key={file.name}>
          <strong>{file.name}</strong> <span>{file.phase}</span>{' '}
          <progress value={file.bytes} max={file.total} />{' '}
          <span>
            {mb(file.bytes)} / {mb(file.total)} MB
          </span>{' '}
          <span>{file.destination}</span>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 6: Run the tests**

Run: `cd web && npx vitest run src/components/InflightTable.test.tsx`
Expected: PASS, all four.

- [ ] **Step 7: Run lint and the full frontend suite**

Run: `cd web && npm run lint && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add web/src/api/types.ts web/src/api/client.ts \
        web/src/components/InflightTable.tsx \
        web/src/components/InflightTable.test.tsx
git commit -m "feat: add the in-flight downloads table"
```

---

### Task 8: Poll for downloads on the Organize page, and document it

The page polls once a second while an Organize job is running, and stops on the first response saying no run is open — which is exactly what the backend reports once the run closes its folder. No coupling to job status is needed.

**Files:**
- Modify: `web/src/pages/ActionPage.tsx`
- Modify: `web/src/pages/ActionPage.test.tsx`
- Modify: `README.md`

**Interfaces:**
- Consumes: `getDownloads`, `InflightTable`, `Downloads` (Task 7).
- Produces: nothing — this is the last consumer.

- [ ] **Step 1: Write the failing test**

In `web/src/pages/ActionPage.test.tsx`, add `getDownloads` to the mocked client module and add an Organize action to the fixtures:

```tsx
const getDownloads = vi.fn(async () => ({
  run_dir: 'downloads/2026-08-10_14-32-05',
  files: [
    {
      name: 'IMG_1234.HEIC',
      phase: 'downloading' as const,
      bytes: 1_258_291,
      total: 3_565_158,
      destination: 'Photos/2025-07',
    },
  ],
  stale_runs: [],
}))
```

Add to the `vi.mock('../api/client', ...)` factory object:

```tsx
  getDownloads: () => getDownloads(),
```

Add a second entry to `ACTIONS`:

```tsx
  {
    id: 'organize',
    title: 'Organize Photos',
    description: 'Upload every planned file.',
    order: 40,
    schema: { type: 'object', properties: {} },
  },
```

And add the tests:

```tsx
  it('shows in-flight files once an organize run starts', async () => {
    renderAt('/actions/organize')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(await screen.findByText('IMG_1234.HEIC')).toBeTruthy()
  })

  it('does not poll for downloads on other actions', async () => {
    renderAt('/actions/check_connection')
    await userEvent.click(screen.getByRole('button', { name: /run/i }))
    expect(getDownloads).not.toHaveBeenCalled()
  })
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd web && npx vitest run src/pages/ActionPage.test.tsx`
Expected: FAIL — `IMG_1234.HEIC` is never found, because nothing polls.

- [ ] **Step 3: Poll in ActionPage**

Rewrite `web/src/pages/ActionPage.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getDownloads, runAction } from '../api/client'
import type { ActionSpec, Downloads } from '../api/types'
import { InflightTable } from '../components/InflightTable'
import { JobProgress } from '../components/JobProgress'

const POLL_MS = 1000

export function ActionPage({ actions }: { actions: ActionSpec[] }) {
  const { actionId } = useParams()
  const [jobId, setJobId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloads, setDownloads] = useState<Downloads | null>(null)

  const watching = actionId === 'organize' && jobId !== null

  useEffect(() => {
    if (!watching) return
    let stopped = false

    const poll = async () => {
      const current = await getDownloads()
      if (stopped) return
      setDownloads(current)
      // The backend closes the run folder when the run ends, so this is the
      // run telling us it is over — no need to ask the job.
      if (current.run_dir === null) stopped = true
    }

    poll()
    const timer = setInterval(poll, POLL_MS)
    return () => {
      stopped = true
      clearInterval(timer)
    }
  }, [watching])

  const action = actions.find((a) => a.id === actionId)
  if (!action) return <p>Unknown action: {actionId}</p>

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const job = await runAction(action.id, {})
      setJobId(job.id)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const hasParams = Object.keys(action.schema.properties ?? {}).length > 0

  return (
    <>
      <h2>{action.title}</h2>
      <p>{action.description}</p>
      {hasParams && (
        <pre className="log">{JSON.stringify(action.schema, null, 2)}</pre>
      )}
      <button onClick={start} disabled={busy}>
        {busy ? 'Starting…' : 'Run'}
      </button>
      {error && <p className="error">{error}</p>}
      {jobId && <JobProgress jobId={jobId} />}
      {downloads && <InflightTable downloads={downloads} />}
    </>
  )
}
```

Note the hook moved above the `if (!action) return` early return: React requires hooks to run unconditionally on every render, and the previous version had no hooks at all.

- [ ] **Step 4: Run the tests**

Run: `cd web && npx vitest run src/pages/ActionPage.test.tsx`
Expected: PASS, all five.

- [ ] **Step 5: Run lint and the full frontend suite**

Run: `cd web && npm run lint && npm test`
Expected: PASS.

- [ ] **Step 6: Document it**

In `README.md`, add this section directly after `## Running the migration`:

```markdown
## Watching a run

Every Organize run gets its own folder under `downloads/`, named for the time
it started:

```
downloads/2026-08-10_14-32-05/IMG_1234.HEIC.part
```

A file appears there while it is being pulled out of the archive and
disappears the moment its upload is verified, so what you see is what is
genuinely in flight. `ls -lh downloads/*/` is a complete progress report. The
Organize page shows the same thing, adding the upload half: a file that has
finished downloading sits at full size on disk while its bytes go to Drive.

A run only ever touches its own folder. If a run dies, its folder stays,
named for when it started, and the next run reports it rather than deleting
it — `rm -rf downloads/<that folder>` when you have looked. Empty leftovers
are cleared automatically.

`downloads/` is gitignored, and nothing is kept there after a successful
upload.
```

Also update the `photolib/` list under `## Architecture`, adding after the
`photolib/thumbs.py` line:

```markdown
- `photolib/downloads.py` — the per-run download folder and the live transfer
  registry behind `GET /api/downloads`
```

- [ ] **Step 7: Run everything**

Run: `uv run pytest && cd web && npm run lint && npm test`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add web/src/pages/ActionPage.tsx web/src/pages/ActionPage.test.tsx README.md
git commit -m "feat: show in-flight downloads on the Organize page"
```

---

## Manual verification

After Task 8, with real credentials and the two dev processes running:

1. Start an Organize run with `limit` set to something small.
2. `watch -n1 'ls -lh downloads/*/'` in a third terminal — files should appear under their real names and grow.
3. The Organize page should list the same files, flipping from `downloading`
   to `uploading` as each one finishes spooling.
4. Kill the API process mid-run. The run folder and its `.part` files should
   survive. Start the API again and run Organize: the log should name the
   leftover folder and its size, and the folder should still be there
   afterwards.
