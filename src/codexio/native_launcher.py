"""Development CLI handoff to the Swift app; no Qt macOS runtime."""
from __future__ import annotations

import os
from pathlib import Path


def launch(arguments):
    root = Path(__file__).resolve().parents[2]
    app = root / "build/dev/macos/Codexio.app/Contents/MacOS/Codexio"
    if not app.is_file():
        raise RuntimeError("Build the native Mac app first with ./build_macos.sh")
    os.execv(str(app), [str(app), *arguments])
