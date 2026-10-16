"""Set the Mac version by default; Windows requires an explicit platform choice."""
import argparse
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codexio.versions import AppVersion


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--platform", choices=("macos", "windows"), default="macos")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    value = str(AppVersion.parse(args.version))
    (root / args.platform / "VERSION").write_text(value + "\n", encoding="utf-8")
    print(args.platform + " version: " + args.version)
