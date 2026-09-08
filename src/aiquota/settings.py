from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

# Keep the existing data directory so renaming the app preserves settings and history.
APP_DIR_NAME = "AIQuotaWidget"
SETTINGS_FILENAME = "settings.json"
CACHE_FILENAME = "quota_cache.json"

REFRESH_INTERVALS = (30, 60, 300)
DEFAULT_REFRESH_INTERVAL = 60
DISPLAY_MODES = ("bottom", "top", "tray")
DEFAULT_DISPLAY_MODE = "top"
VISUAL_STYLES = ("classic", "rings", "tiles", "compact", "minimal", "orb")
DEFAULT_VISUAL_STYLE = "classic"
DEFAULT_WINDOW_WIDTH = 320
DEFAULT_WINDOW_HEIGHT = 200
MIN_WINDOW_WIDTH = 200
MIN_WINDOW_HEIGHT = 96
MAX_WINDOW_WIDTH = 1200
MAX_WINDOW_HEIGHT = 800
DEFAULT_BACKGROUND_TRANSPARENT = True
DEFAULT_BACKGROUND_OPACITY = 70
MIN_BACKGROUND_OPACITY = 0
MAX_BACKGROUND_OPACITY = 100
DEFAULT_SHOW_BORDER = True
DEFAULT_BORDER_COLOR = "#8AB4F8"
DOCK_EDGES = ("none", "top", "bottom", "left", "right")
DEFAULT_DOCK_EDGE = "none"
QUOTA_SCOPES = ("auto", "both", "week")
DEFAULT_QUOTA_SCOPE = "auto"
_HEX_COLOR = re.compile(r"^#([0-9A-Fa-f]{6})$")


def data_dir() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        root = Path(local_appdata)
    else:
        root = Path.home() / "AppData" / "Local"
    path = root / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return data_dir() / SETTINGS_FILENAME


def cache_path() -> Path:
    return data_dir() / CACHE_FILENAME


@dataclass
class AppSettings:
    refresh_interval_seconds: int = DEFAULT_REFRESH_INTERVAL
    display_mode: str = DEFAULT_DISPLAY_MODE
    window_x: Optional[int] = None
    window_y: Optional[int] = None
    window_width: Optional[int] = None
    window_height: Optional[int] = None
    visual_style: str = DEFAULT_VISUAL_STYLE
    background_transparent: bool = DEFAULT_BACKGROUND_TRANSPARENT
    background_opacity: int = DEFAULT_BACKGROUND_OPACITY
    show_border: bool = DEFAULT_SHOW_BORDER
    border_color: str = DEFAULT_BORDER_COLOR
    dock_edge: str = DEFAULT_DOCK_EDGE
    dock_top_width: Optional[int] = None
    dock_top_height: Optional[int] = None
    dock_side_width: Optional[int] = None
    dock_side_height: Optional[int] = None
    quota_scope: str = DEFAULT_QUOTA_SCOPE
    codex_path: Optional[str] = None

    def normalized(self) -> "AppSettings":
        interval = self.refresh_interval_seconds
        if interval not in REFRESH_INTERVALS:
            interval = DEFAULT_REFRESH_INTERVAL
        mode = self.display_mode if self.display_mode in DISPLAY_MODES else DEFAULT_DISPLAY_MODE
        style = self.visual_style if self.visual_style in VISUAL_STYLES else DEFAULT_VISUAL_STYLE
        edge = self.dock_edge if self.dock_edge in DOCK_EDGES else DEFAULT_DOCK_EDGE
        scope = self.quota_scope if self.quota_scope in QUOTA_SCOPES else DEFAULT_QUOTA_SCOPE
        path = self.codex_path.strip() if isinstance(self.codex_path, str) and self.codex_path.strip() else None
        opacity = _clamp_int(self.background_opacity, MIN_BACKGROUND_OPACITY, MAX_BACKGROUND_OPACITY)
        if opacity is None:
            opacity = DEFAULT_BACKGROUND_OPACITY
        return AppSettings(
            refresh_interval_seconds=interval,
            display_mode=mode,
            window_x=self.window_x,
            window_y=self.window_y,
            window_width=_clamp_int(self.window_width, MIN_WINDOW_WIDTH, MAX_WINDOW_WIDTH),
            window_height=_clamp_int(self.window_height, MIN_WINDOW_HEIGHT, MAX_WINDOW_HEIGHT),
            visual_style=style,
            background_transparent=bool(self.background_transparent),
            background_opacity=opacity,
            show_border=bool(self.show_border),
            border_color=_normalize_hex_color(self.border_color),
            dock_edge=edge,
            dock_top_width=_clamp_int(self.dock_top_width, 40, MAX_WINDOW_WIDTH),
            dock_top_height=_clamp_int(self.dock_top_height, 32, MAX_WINDOW_HEIGHT),
            dock_side_width=_clamp_int(self.dock_side_width, 40, MAX_WINDOW_WIDTH),
            dock_side_height=_clamp_int(self.dock_side_height, 32, MAX_WINDOW_HEIGHT),
            quota_scope=scope,
            codex_path=path,
        )

    def background_alpha(self) -> int:
        if not self.background_transparent:
            return 255
        return int(round(255 * (100 - self.background_opacity) / 100.0))


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_settings(path: Optional[Path] = None) -> AppSettings:
    raw = _read_json(path or settings_path())
    settings = AppSettings(
        refresh_interval_seconds=_optional_int(raw.get("refresh_interval_seconds")) or DEFAULT_REFRESH_INTERVAL,
        display_mode=str(raw.get("display_mode", DEFAULT_DISPLAY_MODE)),
        window_x=_optional_int(raw.get("window_x")),
        window_y=_optional_int(raw.get("window_y")),
        window_width=_optional_int(raw.get("window_width")),
        window_height=_optional_int(raw.get("window_height")),
        visual_style=str(raw.get("visual_style", DEFAULT_VISUAL_STYLE)),
        background_transparent=_optional_bool(raw.get("background_transparent"), DEFAULT_BACKGROUND_TRANSPARENT),
        background_opacity=_optional_int(raw.get("background_opacity"))
        if "background_opacity" in raw
        else DEFAULT_BACKGROUND_OPACITY,
        show_border=_optional_bool(raw.get("show_border"), DEFAULT_SHOW_BORDER),
        border_color=raw.get("border_color") if isinstance(raw.get("border_color"), str) else DEFAULT_BORDER_COLOR,
        dock_edge=str(raw.get("dock_edge", DEFAULT_DOCK_EDGE)),
        dock_top_width=_optional_int(raw.get("dock_top_width")),
        dock_top_height=_optional_int(raw.get("dock_top_height")),
        dock_side_width=_optional_int(raw.get("dock_side_width")),
        dock_side_height=_optional_int(raw.get("dock_side_height")),
        quota_scope=str(raw.get("quota_scope", DEFAULT_QUOTA_SCOPE)),
        codex_path=raw.get("codex_path") if isinstance(raw.get("codex_path"), str) else None,
    )
    return settings.normalized()


def save_settings(settings: AppSettings, path: Optional[Path] = None) -> None:
    payload = asdict(settings.normalized())
    payload["version"] = 1
    _write_json(path or settings_path(), payload)


def load_quota_cache(path: Optional[Path] = None) -> dict:
    return _read_json(path or cache_path())


def save_quota_cache(payload: dict, path: Optional[Path] = None) -> None:
    _write_json(path or cache_path(), payload)


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return default


def _clamp_int(value: Optional[int], low: int, high: int) -> Optional[int]:
    number = _optional_int(value)
    if number is None:
        return None
    return max(low, min(high, number))


def _normalize_hex_color(value: Any, default: str = DEFAULT_BORDER_COLOR) -> str:
    if isinstance(value, str) and _HEX_COLOR.match(value.strip()):
        return "#" + value.strip()[1:].upper()
    return default
