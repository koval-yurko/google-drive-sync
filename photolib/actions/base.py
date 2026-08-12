"""The contract every action implements."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from typing import Callable, Iterator

from pydantic import BaseModel, ConfigDict

from photolib.config import Config
from photolib.db.settings_repo import SettingsRepo


class ActionParams(BaseModel):
    """Base for action parameters; unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class ProgressEvent:
    """One unit of feedback from a running action."""

    message: str
    progress: float | None = None
    level: str = "info"
    phase: str | None = None
    """Display name of the phase, e.g. 'Upload (5/5)'. None outside a flow."""
    done: int | None = None
    total: int | None = None
    """Items finished and items declared, for the phase in progress."""


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
    run_id: str | None = None
    """Identity of this flow run; the key `job_items` are stored under."""
    cancelled: threading.Event | None = None
    """Set when the operator cancels. None outside a job."""


@dataclass
class ActionSpec:
    id: str
    title: str
    description: str
    order: int
    params_model: type[ActionParams]
    run: Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]
    group: str = "advanced"

    def json_schema(self) -> dict:
        return self.params_model.model_json_schema()
