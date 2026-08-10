"""FastAPI application factory and wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from photolib.actions.base import ActionContext
from photolib.config import Config
from photolib.db import catalog
from photolib.db.jobs_repo import JobsRepo
from photolib.db.settings_repo import SettingsRepo
from photolib.drive.auth import TokenProvider
from photolib.drive.client import DriveClient
from photolib.drive.writer import DriveWriter
from photolib.jobs.broker import EventBroker
from photolib.jobs.runner import JobRunner


def create_app(config: Config | None = None, drive=None) -> FastAPI:
    cfg = config or Config.load()
    conn = catalog.connect(cfg.db_path)
    tokens = TokenProvider(cfg.credentials_path, cfg.token_path)
    drive_client = drive if drive is not None else DriveClient(tokens)

    settings = SettingsRepo(conn)
    jobs = JobsRepo(conn)
    broker = EventBroker()

    # A fake injected by tests already speaks the writer protocol; a real
    # client needs wrapping. DriveWriter resolves the client lazily, so this
    # is safe even when nothing ever uploads.
    drive_writer = (
        drive_client
        if hasattr(drive_client, "start_session")
        else DriveWriter(drive_client)
    )

    def context_factory() -> ActionContext:
        return ActionContext(
            conn=conn, drive=drive_client, settings=settings, config=cfg,
            writer=drive_writer,
        )

    runner = JobRunner(context_factory=context_factory, repo=jobs, broker=broker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runner.start()
        yield
        runner.stop()
        conn.close()

    app = FastAPI(title="Photo Library Organizer", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.config = cfg
    app.state.conn = conn
    app.state.tokens = tokens
    app.state.drive = drive_client
    app.state.settings = settings
    app.state.jobs = jobs
    app.state.broker = broker
    app.state.runner = runner

    from photolib.api import (
        routes_actions,
        routes_drive,
        routes_jobs,
        routes_review,
        routes_settings,
    )

    app.include_router(routes_settings.router, prefix="/api")
    app.include_router(routes_drive.router, prefix="/api")
    app.include_router(routes_actions.router, prefix="/api")
    app.include_router(routes_jobs.router, prefix="/api")
    app.include_router(routes_review.router, prefix="/api")
    return app
