"""System UI language and shared Chinese/English interface strings."""
from __future__ import annotations

import json
import locale
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def language():
    try:
        from PySide6.QtCore import QLocale
        preferred = QLocale.system().uiLanguages()
    except ImportError:
        preferred = [locale.getlocale()[0] or "en"]
    for value in preferred:
        if value.lower().startswith("zh"):
            return "zh"
        if value.lower().startswith("en"):
            return "en"
    return "en"


@lru_cache(maxsize=1)
def _catalog():
    try:
        return json.loads(Path(__file__).with_name("translations.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _plural(text):
    if language() != "en":
        return text
    return re.sub(r"\b1 (days|hours|minutes|seconds|requests|calls|resets|records|files|chats)\b",
                  lambda match: "1 " + match.group(1)[:-1], text)


class _Text(str):
    def __mod__(self, value):
        return _plural(super().__mod__(value))

    def format(self, *args, **kwargs):
        return _plural(super().format(*args, **kwargs))


def tr(text):
    value = str(text)
    return _Text(_catalog().get(value, value) if language() == "en" else value)
