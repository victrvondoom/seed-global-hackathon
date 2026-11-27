from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm.exc import StaleDataError

from app.api.routes import router
from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.services.runtime import Runtime

logging.basicConfig(level=logging.INFO)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    rt = Runtime(settings)
    rt.db.create_all()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt.bus.bind_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(title="Waypoint API", version="0.1.0", lifespan=lifespan,
                  description="Travel copilot: the model proposes, the backend controls execution.")
    app.state.rt = rt
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, e: DomainError) -> JSONResponse:
        return JSONResponse({"error": {"code": e.code, "message": e.message, "details": e.details}}, status_code=e.status_code)

    @app.exception_handler(StaleDataError)
    async def stale(_: Request, e: StaleDataError) -> JSONResponse:
        return JSONResponse({"error": {"code": "conflict", "message": "The trip changed meanwhile; refresh and retry",
                                       "details": {}}}, status_code=409)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    app.include_router(router)

    static = Path(settings.static_dir) if settings.static_dir else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if static.is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            f = (static / path).resolve()
            if path and f.is_file() and static.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(static / "index.html")

    return app


app = create_app()
