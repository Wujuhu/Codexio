"""Set the one source of truth used by the app and Windows EXE metadata."""
from pathlib import Path
import re
import sys


def set_version(value: str, source: Path) -> None:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value) or any(int(n) > 65535 for n in value.split(".")):
        raise ValueError("Use a stable version such as 0.1.2; each number must be at most 65535.")
    text = source.read_text(encoding="utf-8")
    updated, count = re.subn(r'^__version__ = "[^"]+"$', '__version__ = "' + value + '"', text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("Could not find the app version.")
    source.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    set_version(sys.argv[1], Path(__file__).resolve().parents[1] / "src" / "codexio" / "__init__.py")
    print("Version: " + sys.argv[1])
