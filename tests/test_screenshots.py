"""tools/screenshots.py runs in the Pages build; this test notices when a UI change breaks it."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parent.parent


def test_screenshots_are_written(tmp_path: Path) -> None:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": "1"}
    subprocess.run(
        [sys.executable, ROOT / "tools" / "screenshots.py", "--out", tmp_path], check=True, env=env, timeout=120
    )
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [f"{n}-{m}.png" for n in ("connect", "main") for m in ("dark", "light")]
    assert (tmp_path / "main-light.png").read_bytes() != (tmp_path / "main-dark.png").read_bytes()
