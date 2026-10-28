from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .api import router, validation_error_response
from .config import Settings, settings
from .db import create_database, initialize_database
from .errors import ApiError
from .services.workspace import WorkspaceService


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        try:
            request_size = int(content_length) if content_length else None
        except ValueError:
            request_size = None
        if request_size is not None and request_size > self.max_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": f"Request bodies must be smaller than {self.max_bytes} bytes.",
                        "details": None,
                    }
                },
            )
        return await call_next(request)


def create_app(app_settings: Settings | None = None) -> FastAPI:
    active_settings = app_settings or settings
    engine, session_factory = create_database(active_settings.database_path)
    initialize_database(engine)
    active_settings.workspace_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="CircuitPilot API", version="0.1.0")
    app.state.settings = active_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.workspace = WorkspaceService(active_settings.workspace_root, active_settings.max_file_size)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=active_settings.max_request_size)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return validation_error_response(request, exc)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        # Avoid returning internal paths or tracebacks to the browser.
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "The server could not complete the request.", "details": None}},
        )

    return app


app = create_app()
