"""Entry point: `uv run uvicorn photolib.main:app --reload`."""

from photolib.api.app import create_app

app = create_app()
