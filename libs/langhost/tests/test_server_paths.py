from __future__ import annotations

from pathlib import Path

import pytest


def test_server_rejects_custom_app_symlink_escape(tmp_path: Path) -> None:
    external = tmp_path.parent / "escaped_app.py"
    external.write_text(
        "def app():\n    return 'ok'\n",
        encoding="utf-8",
    )
    package = tmp_path / "runtime_service"
    package.mkdir()
    (package / "app.py").symlink_to(external)
    config = {"http": {"app": "./app.py:app"}}

    from langhost.server import _load_symbol

    with pytest.raises(ValueError, match="escapes base directory"):
        _load_symbol(config["http"]["app"], package)
