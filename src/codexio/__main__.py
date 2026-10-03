"""Development handoff to the native app; the Python desktop UI is retired."""
from __future__ import annotations

import sys


def main() -> int:
    arguments = sys.argv[1:]
    if {"--apply-update", "--apply-mac-update", "--upstream-proxy"}.intersection(arguments):
        print("旧 Python 后台模式已退役，请使用当前原生应用。", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("旧 Python 桌面版已退役。Windows 请使用 run.ps1 或原生 Codexio.exe。", file=sys.stderr)
        return 2
    from codexio.native_launcher import launch
    try:
        launch(arguments)
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
