from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data"


def _absolute_path(value: str | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


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

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _absolute_path(os.getenv("DATA_DIR"), DEFAULT_DATA_DIR)
        database_path = _absolute_path(os.getenv("DATABASE_PATH"), data_dir / "circuitpilot.db")
        workspace_root = _absolute_path(os.getenv("WORKSPACE_ROOT"), data_dir / "workspaces")
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
            ).split(",")
            if origin.strip()
        )
        return cls(
            backend_host=os.getenv("BACKEND_HOST", "127.0.0.1"),
            backend_port=int(os.getenv("BACKEND_PORT", "8000")),
            database_path=database_path,
            workspace_root=workspace_root,
            allowed_origins=origins,
        )


settings = Settings.from_env()

