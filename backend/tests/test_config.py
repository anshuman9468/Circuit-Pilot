from pathlib import Path

from app.config import REPO_ROOT, _absolute_path


def test_old_documentation_placeholder_paths_use_safe_repository_defaults(tmp_path):
    fallback = tmp_path / "data" / "circuitpilot.db"
    assert _absolute_path("/absolute/path/to/Circuit_Pilot/data/circuitpilot.db", fallback) == fallback
    assert _absolute_path("data/circuitpilot.db", Path("/repo") / "circuitpilot.db") == REPO_ROOT / "data" / "circuitpilot.db"
