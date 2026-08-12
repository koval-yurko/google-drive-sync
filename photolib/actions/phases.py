"""Compose actions into flows without either side knowing about the other.

A flow drives an existing action's `run()` generator through `run_phase`,
which rescales that action's 0..1 progress into its slice of the flow and
stamps the phase name onto every event. The sub-action stays unaware it is
being composed, so its Advanced page keeps working on the same code.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Iterator

from photolib.actions.base import ActionContext, ActionParams, ProgressEvent

PhaseRunner = Callable[[ActionContext, ActionParams], Iterator[ProgressEvent]]


def phase_label(name: str, index: int, total: int) -> str:
    return f"{name} ({index}/{total})"


def run_phase(
    name: str,
    span: tuple[float, float],
    runner: PhaseRunner,
    ctx: ActionContext,
    params: ActionParams,
    *,
    index: int,
    total: int,
) -> Iterator[ProgressEvent]:
    """Re-yield `runner`'s events with progress mapped into `span`."""
    low, high = span
    label = phase_label(name, index, total)
    for event in runner(ctx, params):
        progress = event.progress
        if progress is not None:
            progress = low + (high - low) * min(max(progress, 0.0), 1.0)
        yield replace(event, progress=progress, phase=label)
