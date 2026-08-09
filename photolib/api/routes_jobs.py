"""Job history, detail, and the SSE event stream.

Stub: the routes land in Task 12. The router exists now so `app.py` can wire
its final set of includes and stay importable at every commit.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["jobs"])
