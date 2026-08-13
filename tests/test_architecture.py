"""The layering contract, run as a test so `pytest` alone catches drift."""

import subprocess


def test_import_contracts_hold():
    # The console script, not `python -m importlinter`: import-linter's entry
    # point is a click command with no `__main__`, and `uv run pytest` puts
    # the venv's bin directory on PATH.
    result = subprocess.run(["lint-imports"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
