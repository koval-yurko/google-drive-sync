"""Auto-discovery of action modules.

Any module in this package declaring ID, TITLE, DESCRIPTION, ORDER, Params and
run() becomes an action, and therefore a page in the UI.
"""

from __future__ import annotations

import importlib
import pkgutil

import photolib.actions
from photolib.actions.base import ActionSpec

_REQUIRED = ("ID", "TITLE", "DESCRIPTION", "ORDER", "Params", "run")


class UnknownActionError(KeyError):
    """Raised when an action id does not exist."""


def _discover() -> dict[str, ActionSpec]:
    specs: dict[str, ActionSpec] = {}
    for info in pkgutil.iter_modules(photolib.actions.__path__):
        if info.name in {"base", "registry"}:
            continue
        module = importlib.import_module(f"photolib.actions.{info.name}")
        if not all(hasattr(module, attr) for attr in _REQUIRED):
            continue
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
