"""Auto-discovery of action modules.

Any module in this package declaring ID, TITLE, DESCRIPTION, ORDER, Params and
run() becomes an action, and therefore a page in the UI.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

import photolib.actions
from photolib.actions.base import ActionSpec

_REQUIRED = ("ID", "TITLE", "DESCRIPTION", "ORDER", "Params", "run")
_logger = logging.getLogger(__name__)

# Module-level storage for discovery errors
_discovery_errors: dict[str, str] = {}


class UnknownActionError(KeyError):
    """Raised when an action id does not exist."""


def discovery_errors() -> dict[str, str]:
    """Return a dict mapping module names to error strings for modules that failed discovery."""
    _discover()  # Ensure discovery has run
    return _discovery_errors.copy()


def _discover() -> dict[str, ActionSpec]:
    global _discovery_errors
    _discovery_errors = {}
    specs: dict[str, ActionSpec] = {}
    for info in pkgutil.iter_modules(photolib.actions.__path__):
        if info.name in {"base", "registry"}:
            continue
        try:
            module = importlib.import_module(f"photolib.actions.{info.name}")
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            _discovery_errors[info.name] = error_msg
            _logger.exception(f"Failed to import photolib.actions.{info.name}")
            continue

        if not all(hasattr(module, attr) for attr in _REQUIRED):
            continue

        # Validate that run is a generator function
        if not inspect.isgeneratorfunction(module.run):
            error_msg = "run must be a generator function"
            _discovery_errors[info.name] = error_msg
            _logger.warning(
                f"Skipping photolib.actions.{info.name}: {error_msg}"
            )
            continue

        # Detect duplicate IDs
        if module.ID in specs:
            error_msg = (
                f"Duplicate ID '{module.ID}': already defined in a previous module"
            )
            raise ValueError(error_msg)

        specs[module.ID] = ActionSpec(
            id=module.ID,
            title=module.TITLE,
            description=module.DESCRIPTION,
            order=module.ORDER,
            params_model=module.Params,
            run=module.run,
        )
    return specs


def all_actions() -> list[ActionSpec]:
    return sorted(_discover().values(), key=lambda s: (s.order, s.id))


def get_action(action_id: str) -> ActionSpec:
    specs = _discover()
    if action_id not in specs:
        raise UnknownActionError(action_id)
    return specs[action_id]
