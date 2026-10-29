from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def _absolute_path(value: str | None, default: Path) -> Path:
    # Older copies of .env.example used documentation-only placeholder paths.
    # Treat those as unset so a copied configuration remains runnable.
    if value and value.startswith("/absolute/path/to/"):
        value = None
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def _read_local_env() -> dict[str, str]:
    """Read simple local env files without overriding the process environment.

    This keeps the MVP dependency-light while making the documented `.env` workflow
    useful. Values are never logged or returned by the API.
    """
    values: dict[str, str] = {}
    for path in (REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                values[key] = value
    return values


def _env(name: str, default: str | None = None, local: dict[str, str] | None = None) -> str | None:
    return os.getenv(name, (local or {}).get(name, default))


@dataclass(frozen=True)
class Settings:
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    database_path: Path = field(default_factory=lambda: DEFAULT_DATA_DIR / "circuitpilot.db")
    workspace_root: Path = field(default_factory=lambda: DEFAULT_DATA_DIR / "workspaces")
    allowed_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    max_file_size: int = 256 * 1024
    max_request_size: int = 512 * 1024
    max_requirements_size: int = 128 * 1024
    model_api_key: str | None = None
    meta_base_url: str = "https://api.meta.ai/v1"
    meta_model: str = "muse-spark-1.3-contributor"
    model_timeout_seconds: float = 45.0
    max_model_rounds: int = 6
    max_tool_calls: int = 12
    max_completion_tokens: int = 2048
    max_context_chars: int = 96_000
    max_tool_result_chars: int = 32_000
    max_active_runs: int = 1
    max_user_message_chars: int = 20_000
    generation_enabled: bool = False
    max_generation_output_bytes: int = 512 * 1024
    max_generation_corrections: int = 1

    def __post_init__(self) -> None:
        if self.model_timeout_seconds <= 0:
            raise ValueError("model_timeout_seconds must be positive")
        for name in (
            "max_model_rounds",
            "max_tool_calls",
            "max_completion_tokens",
            "max_context_chars",
            "max_tool_result_chars",
            "max_active_runs",
            "max_user_message_chars",
            "max_generation_output_bytes",
            "max_generation_corrections",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not self.meta_base_url.startswith(("http://", "https://")):
            raise ValueError("meta_base_url must be an HTTP(S) URL")

    @classmethod
    def from_env(cls) -> "Settings":
        local = _read_local_env()
        data_dir = _absolute_path(_env("DATA_DIR", local=local), DEFAULT_DATA_DIR)
        database_path = _absolute_path(_env("DATABASE_PATH", local=local), data_dir / "circuitpilot.db")
        workspace_root = _absolute_path(_env("WORKSPACE_ROOT", local=local), data_dir / "workspaces")
        origins = tuple(
            origin.strip()
            for origin in (_env("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000", local) or "").split(",")
            if origin.strip()
        )
        return cls(
            backend_host=_env("BACKEND_HOST", "127.0.0.1", local) or "127.0.0.1",
            backend_port=int(_env("BACKEND_PORT", "8000", local) or "8000"),
            database_path=database_path,
            workspace_root=workspace_root,
            allowed_origins=origins,
            model_api_key=_env("MODEL_API_KEY", local=local) or None,
            meta_base_url=_env("META_BASE_URL", "https://api.meta.ai/v1", local) or "https://api.meta.ai/v1",
            meta_model=_env("META_MODEL", "muse-spark-1.3-contributor", local) or "muse-spark-1.3-contributor",
            model_timeout_seconds=float(_env("MODEL_TIMEOUT_SECONDS", "45", local) or "45"),
            max_model_rounds=int(_env("MAX_MODEL_ROUNDS", "6", local) or "6"),
            max_tool_calls=int(_env("MAX_TOOL_CALLS", "12", local) or "12"),
            max_completion_tokens=int(_env("MAX_COMPLETION_TOKENS", "2048", local) or "2048"),
            max_context_chars=int(_env("MAX_CONTEXT_CHARS", "96000", local) or "96000"),
            max_tool_result_chars=int(_env("MAX_TOOL_RESULT_CHARS", "32000", local) or "32000"),
            max_active_runs=int(_env("MAX_ACTIVE_RUNS", "1", local) or "1"),
            max_user_message_chars=int(_env("MAX_USER_MESSAGE_CHARS", "20000", local) or "20000"),
            generation_enabled=(_env("GENERATION_ENABLED", "false", local) or "false").lower() in {"1", "true", "yes", "on"},
            max_generation_output_bytes=int(_env("MAX_GENERATION_OUTPUT_BYTES", "524288", local) or "524288"),
            max_generation_corrections=int(_env("MAX_GENERATION_CORRECTIONS", "1", local) or "1"),
        )


settings = Settings.from_env()
