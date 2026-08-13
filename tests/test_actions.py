import importlib
import sys
from pathlib import Path

import pytest

from photolib.actions import check_connection
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent
from photolib.actions.registry import UnknownActionError, all_actions, discovery_errors, get_action
from photolib.config import Config
from photolib.db.settings_repo import PHOTOS_ROOT, ZIP_SOURCE, FolderRef, SettingsRepo
from tests.fakes.fake_drive import FakeDrive
from tests.fixtures.zipbuilder import build_zip


def make_ctx(conn, drive) -> ActionContext:
    return ActionContext(
        conn=conn, drive=drive, settings=SettingsRepo(conn), config=Config.load()
    )


def test_registry_discovers_check_connection():
    ids = [spec.id for spec in all_actions()]
    assert "check_connection" in ids


def test_registry_specs_are_complete():
    spec = get_action("check_connection")
    assert spec.title
    assert spec.description
    assert spec.json_schema()["type"] == "object"


def test_registry_rejects_unknown_action():
    with pytest.raises(UnknownActionError):
        get_action("no_such_action")


def test_check_connection_reports_missing_settings(conn):
    drive = FakeDrive()
    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    text = " ".join(e.message for e in events)
    assert "not configured" in text.lower()
    assert any(e.level == "warn" for e in events)


def test_check_connection_reports_configured_folders(conn):
    drive = FakeDrive()
    drive.add_folder("photos", "Global Photos")
    drive.add_folder("zips", "zip-3-22-26")
    drive.add_file("z1", "takeout-001.zip", build_zip({"a.txt": b"x"}), parent="zips")
    drive.add_file("z2", "takeout-002.zip", build_zip({"b.txt": b"y"}), parent="zips")

    settings = SettingsRepo(conn)
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="Global Photos"))
    settings.set_folder(ZIP_SOURCE, FolderRef(id="zips", name="zip-3-22-26"))

    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    text = " ".join(e.message for e in events)
    assert "Global Photos" in text
    assert "2 archive" in text
    assert all(e.level != "error" for e in events)


def test_check_connection_reports_unreachable_folder(conn):
    drive = FakeDrive()
    settings = SettingsRepo(conn)
    # Configure only PHOTOS_ROOT to unreachable, leave ZIP_SOURCE unconfigured
    settings.set_folder(PHOTOS_ROOT, FolderRef(id="ghost", name="Gone"))

    events = list(check_connection.run(make_ctx(conn, drive), check_connection.Params()))
    # Should have exactly 2 events: error for unreachable PHOTOS_ROOT, warn for unconfigured ZIP_SOURCE
    assert len(events) == 2
    assert events[0].level == "error"
    assert "unreachable" in events[0].message.lower()
    assert events[1].level == "warn"
    assert "not configured" in events[1].message.lower()


def test_progress_is_monotonic_and_bounded(conn):
    drive = FakeDrive()
    drive.add_folder("photos", "P")
    drive.add_folder("zips", "Z")
    SettingsRepo(conn).set_folder(PHOTOS_ROOT, FolderRef(id="photos", name="P"))
    SettingsRepo(conn).set_folder(ZIP_SOURCE, FolderRef(id="zips", name="Z"))

    values = [
        e.progress
        for e in check_connection.run(make_ctx(conn, drive), check_connection.Params())
        if e.progress is not None
    ]
    assert values == sorted(values)
    assert all(0.0 <= v <= 1.0 for v in values)


# Discovery tests: dynamic module creation and cleanup

def _write_temp_action(name: str, code: str) -> Path:
    """Write a temporary action module and return its path."""
    actions_dir = Path(__file__).parent.parent / "photolib" / "actions"
    module_path = actions_dir / f"{name}.py"
    module_path.write_text(code)
    return module_path


def _cleanup_temp_module(name: str):
    """Remove a temporary module from disk and sys.modules."""
    actions_dir = Path(__file__).parent.parent / "photolib" / "actions"
    module_path = actions_dir / f"{name}.py"
    if module_path.exists():
        module_path.unlink()
    # Clear from sys.modules so re-import gets fresh code
    full_name = f"photolib.actions.{name}"
    if full_name in sys.modules:
        del sys.modules[full_name]


@pytest.fixture
def cleanup_temp_modules():
    """Fixture to clean up temporary modules after test."""
    yield
    # Cleanup common temp module names used in tests
    for name in [
        "test_well_formed_action",
        "test_missing_attr_action",
        "test_import_error_action",
        "test_non_generator_action",
        "test_duplicate_id_action_1",
        "test_duplicate_id_action_2",
    ]:
        _cleanup_temp_module(name)


def test_well_formed_second_module_is_discovered(cleanup_temp_modules):
    """A well-formed second module with unique ID is discovered automatically."""
    code = '''"""Test action module."""
from typing import Iterator
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent

ID = "test_well_formed_action"
TITLE = "Test Well Formed"
DESCRIPTION = "A test action"
ORDER = 100

class Params(ActionParams):
    pass

def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    yield ProgressEvent("test")
'''
    _write_temp_action("test_well_formed_action", code)

    actions = all_actions()
    ids = [spec.id for spec in actions]
    assert "test_well_formed_action" in ids

    # Verify it's in the right sorted position (ORDER=100 puts it after check_connection ORDER=0)
    spec = get_action("test_well_formed_action")
    assert spec.title == "Test Well Formed"
    assert spec.description == "A test action"

    # Verify sorting by (order, id)
    check_conn_spec = get_action("check_connection")
    assert (check_conn_spec.order, check_conn_spec.id) < (spec.order, spec.id)


def test_module_missing_required_attribute_is_skipped(cleanup_temp_modules):
    """A module missing a required attribute is skipped, not discovered."""
    # Missing TITLE
    code = '''"""Test action missing title."""
from typing import Iterator
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent

ID = "test_missing_attr_action"
DESCRIPTION = "Missing title"
ORDER = 50

class Params(ActionParams):
    pass

def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    yield ProgressEvent("test")
'''
    _write_temp_action("test_missing_attr_action", code)

    actions = all_actions()
    ids = [spec.id for spec in actions]
    assert "test_missing_attr_action" not in ids

    # check_connection should still be discovered
    assert "check_connection" in ids


def test_module_that_raises_on_import_is_skipped(cleanup_temp_modules):
    """A module that raises an exception on import is skipped, not discovered."""
    code = '''"""Test action that fails to import."""
raise RuntimeError("Intentional import error for testing")
'''
    _write_temp_action("test_import_error_action", code)

    # Should not raise, just skip the broken module
    actions = all_actions()
    ids = [spec.id for spec in actions]
    assert "test_import_error_action" not in ids

    # check_connection should still be discovered
    assert "check_connection" in ids

    # Error should be recorded
    errors = discovery_errors()
    assert "test_import_error_action" in errors
    assert "RuntimeError" in errors["test_import_error_action"]


def test_module_with_non_generator_run_is_skipped(cleanup_temp_modules):
    """A module where run is not a generator function is skipped."""
    code = '''"""Test action with non-generator run."""
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent

ID = "test_non_generator_action"
TITLE = "Test Non-Generator"
DESCRIPTION = "Non-generator run"
ORDER = 50

class Params(ActionParams):
    pass

def run(ctx: ActionContext, params: Params):
    """Not a generator - just returns a list."""
    return [ProgressEvent("test")]
'''
    _write_temp_action("test_non_generator_action", code)

    actions = all_actions()
    ids = [spec.id for spec in actions]
    assert "test_non_generator_action" not in ids

    # check_connection should still be discovered
    assert "check_connection" in ids

    # Error should be recorded
    errors = discovery_errors()
    assert "test_non_generator_action" in errors
    assert "generator" in errors["test_non_generator_action"]


def test_duplicate_id_raises_error(cleanup_temp_modules):
    """Two modules with the same ID raise an error naming both modules."""
    # Create a module with the same ID as check_connection
    code = '''"""Test action with duplicate ID."""
from typing import Iterator
from photolib.actions.base import ActionContext, ActionParams, ProgressEvent

ID = "check_connection"
TITLE = "Duplicate"
DESCRIPTION = "Duplicate ID"
ORDER = 200

class Params(ActionParams):
    pass

def run(ctx: ActionContext, params: Params) -> Iterator[ProgressEvent]:
    yield ProgressEvent("test")
'''
    _write_temp_action("test_duplicate_id_action_1", code)

    # Discovery should raise ValueError due to duplicate ID
    # Error message must name both the original and colliding module names
    with pytest.raises(ValueError) as exc_info:
        all_actions()

    error_message = str(exc_info.value)
    assert "Duplicate" in error_message
    assert "check_connection" in error_message
    assert "test_duplicate_id_action_1" in error_message


def test_actions_default_to_the_advanced_group():
    from photolib.actions.registry import get_action

    assert get_action("scan_archives").group == "advanced"


def test_flows_sort_ahead_of_advanced_actions():
    from photolib.actions.registry import all_actions

    groups = [spec.group for spec in all_actions()]
    assert groups == sorted(groups, key=lambda g: g != "flow")
