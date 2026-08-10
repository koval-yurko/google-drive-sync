"""The contract every action implements."""

from __future__ import annotations

import sqlite3
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


@dataclass
class ActionContext:
    """Everything an action is allowed to reach."""

    conn: sqlite3.Connection
    drive: object
    settings: SettingsRepo
    config: Config
    writer: object | None = None
    """Whatever may mutate Drive. None in a read-only context."""


@dataclass
class ActionSpec:
    id: str
    title: str
    description: str
    order: int
    params_model: type[ActionParams]
    run: Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]

    def json_schema(self) -> dict:
        return self.params_model.model_json_schema()
