"""Set the Mac version by default; Windows requires an explicit platform choice."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--platform", choices=("macos", "windows"), default="macos")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.platform == "windows":
        set_version(args.version, root / "src/codexio/__init__.py")
    else:
        if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", args.version):
            parser.error("Use a stable version such as 0.3.2")
        (root / "macos/VERSION").write_text(args.version + "\n", encoding="utf-8")
    print(args.platform + " version: " + args.version)
