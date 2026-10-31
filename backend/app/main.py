from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .api import router, validation_error_response
from .conversation_api import router as conversation_router
from .generation_api import router as generation_router
from .execution_api import router as execution_router
from .specification_api import router as specification_router
from .config import Settings, settings
from .db import create_database, initialize_database
from .errors import ApiError
from .services.agent_runs import RunManager, recover_interrupted_runs
from .services.generation_service import GenerationManager, MetaGenerationProvider, recover_interrupted_generations
from .services.model_provider import FallbackModelProvider, MetaModelProvider, OpenRouterModelProvider
from .services.planner_service import MetaRequirementsPlanner, SpecificationPlannerManager, recover_interrupted_planner_runs
from .services.execution_service import ExecutionManager, recover_interrupted_execution_runs
from .services.toolchain import ContainerRuntime
from .services.tool_registry import ReadOnlyToolRegistry
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


def create_app(app_settings: Settings | None = None, generation_provider=None, planner_provider=None, execution_runtime=None) -> FastAPI:
    active_settings = app_settings or settings
    engine, session_factory = create_database(active_settings.database_path)
    initialize_database(engine)
    active_settings.workspace_root.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="CircuitPilot API", version="0.1.0")
    app.state.settings = active_settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.workspace = WorkspaceService(active_settings.workspace_root, active_settings.max_file_size)
    app.state.model_provider = FallbackModelProvider(
        MetaModelProvider(active_settings, enabled=active_settings.model_primary_enabled),
        OpenRouterModelProvider(active_settings),
        enabled=active_settings.model_fallback_enabled,
        allow_transient_fallback=active_settings.model_fallback_on_transient_errors,
    )
    app.state.generation_provider = generation_provider or MetaGenerationProvider(app.state.model_provider, active_settings)
    app.state.planner_provider = planner_provider or MetaRequirementsPlanner(app.state.model_provider, active_settings)
    app.state.model_connection_status = "configured_not_tested" if app.state.model_provider.configured else "not_configured"
    app.state.model_last_error = None
    app.state.tool_registry = ReadOnlyToolRegistry(active_settings.max_tool_result_chars)
    app.state.run_manager = RunManager(app)
    app.state.generation_manager = GenerationManager(app)
    app.state.planner_manager = SpecificationPlannerManager(app)
    app.state.toolchain_runtime = execution_runtime or ContainerRuntime(active_settings)
    app.state.execution_manager = ExecutionManager(app)
    recover_interrupted_runs(session_factory)
    recover_interrupted_generations(session_factory)
    recover_interrupted_planner_runs(session_factory)
    recover_interrupted_execution_runs(session_factory, app.state.toolchain_runtime)
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=active_settings.max_request_size)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(active_settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(conversation_router)
    app.include_router(specification_router)
    app.include_router(generation_router)
    app.include_router(execution_router)

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
