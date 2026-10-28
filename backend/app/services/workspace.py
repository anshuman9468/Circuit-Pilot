from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from ..errors import ApiError


ALLOWED_EXTENSIONS = {".v", ".sv", ".vh", ".svh", ".md", ".txt", ".json"}


class WorkspaceService:
    def __init__(self, root: Path, max_file_size: int):
        self.root = root.resolve()
        self.max_file_size = max_file_size
        self.root.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        if not project_id or "/" in project_id or "\\" in project_id or Path(project_id).name != project_id:
            raise ApiError(400, "invalid_project_id", "The project workspace identifier is invalid.")
        project_dir = (self.root / project_id).resolve()
        self._assert_inside(project_dir)
        return project_dir

    def ensure_project(self, project_id: str) -> Path:
        project_dir = self.project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir

    def safe_path(self, project_id: str, relative_path: str) -> Path:
        if not relative_path or "\\" in relative_path:
            raise ApiError(400, "invalid_file_path", "File paths must be relative POSIX paths.")
        path = PurePosixPath(relative_path)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ApiError(400, "invalid_file_path", "File paths must stay inside the project workspace.")
        if "/".join(path.parts) != relative_path:
            raise ApiError(400, "invalid_file_path", "File paths must use normalized POSIX separators.")
        if len(path.parts) > 12:
            raise ApiError(400, "invalid_file_path", "File paths may not contain more than 12 segments.")
        suffix = Path(path.name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
            raise ApiError(400, "unsupported_file_type", f"Only these text file types are supported: {allowed}.")
        project_dir = self.project_dir(project_id)
        candidate = (project_dir / Path(*path.parts)).resolve()
        try:
            candidate.relative_to(project_dir)
        except ValueError as exc:
            raise ApiError(400, "invalid_file_path", "The path escapes the project workspace.") from exc
        return candidate

    def read(self, project_id: str, relative_path: str) -> str:
        path = self.safe_path(project_id, relative_path)
        if not path.is_file():
            raise ApiError(404, "file_not_found", "The requested file does not exist in this project.")
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ApiError(400, "not_text_file", "Project files must contain UTF-8 text.") from exc

    def write(self, project_id: str, relative_path: str, content: str) -> int:
        path = self.safe_path(project_id, relative_path)
        content_bytes = content.encode("utf-8")
        if len(content_bytes) > self.max_file_size:
            raise ApiError(413, "file_too_large", f"Files must be smaller than {self.max_file_size} bytes.")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".circuitpilot-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise
        return len(content_bytes)

    def delete(self, project_id: str, relative_path: str) -> None:
        path = self.safe_path(project_id, relative_path)
        if not path.is_file():
            raise ApiError(404, "file_not_found", "The requested file does not exist in this project.")
        path.unlink()
        self._remove_empty_parents(path.parent, self.project_dir(project_id))

    def rename(self, project_id: str, old_path: str, new_path: str) -> None:
        source = self.safe_path(project_id, old_path)
        destination = self.safe_path(project_id, new_path)
        if not source.is_file():
            raise ApiError(404, "file_not_found", "The requested file does not exist in this project.")
        if destination.exists():
            raise ApiError(409, "duplicate_file", "A file with that path already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        self._remove_empty_parents(source.parent, self.project_dir(project_id))

    def remove_project(self, project_id: str) -> Path | None:
        project_dir = self.project_dir(project_id)
        if not project_dir.exists():
            return None
        tombstone = project_dir.with_name(f".{project_id}.deleting")
        if tombstone.exists():
            shutil.rmtree(tombstone)
        os.replace(project_dir, tombstone)
        return tombstone

    def restore_project(self, project_id: str, tombstone: Path) -> None:
        project_dir = self.project_dir(project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir)
        os.replace(tombstone, project_dir)

    @staticmethod
    def _remove_empty_parents(path: Path, project_dir: Path) -> None:
        while path != project_dir and path != path.parent:
            try:
                path.rmdir()
            except OSError:
                break
            path = path.parent

    def _assert_inside(self, candidate: Path) -> None:
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ApiError(400, "invalid_file_path", "The path escapes the project workspace.") from exc
