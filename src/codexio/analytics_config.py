"""Versioned preferences for the usage dashboard (no authentication credentials)."""
from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from codexio.settings import data_dir

NAVIGATION_PAGES = ("overview", "subscription", "trends", "logs", "pricing", "settings")
DEFAULT_NAVIGATION_ORDER = ("overview", "logs", "trends", "subscription", "pricing", "settings")
SIDEBAR_MAX_WIDTH = 180
SIDEBAR_MIN_WIDTH = 120
SIDEBAR_COLLAPSED_WIDTH = 60
PREVIEW_DEFAULT_WIDTH = 240
PREVIEW_MIN_WIDTH = 220
PREVIEW_MAX_WIDTH = 480
USAGE_REFRESH_INTERVALS = (5, 10, 30, 60)
DEFAULT_USAGE_REFRESH_INTERVAL = 10
WEEK_ESTIMATE_INTERVALS = (10, 30, 60)
DEFAULT_WEEK_ESTIMATE_INTERVAL = 10


def normalize_panel_layout(config):
    result = {"sidebar_collapsed": config.get("sidebar_collapsed") is True}
    for key, default, low, high in (
        ("sidebar_width", SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH),
        ("log_preview_width", PREVIEW_DEFAULT_WIDTH, PREVIEW_MIN_WIDTH, PREVIEW_MAX_WIDTH),
    ):
        value = config.get(key, default)
        result[key] = max(low, min(high, value)) if isinstance(value, int) and not isinstance(value, bool) else default
    return result


def normalize_navigation_order(value) -> list[str]:
    """Keep stable page identities, with overview permanently pinned first."""
    ordered = ["overview"]
    for name in value if isinstance(value, (list, tuple)) else ():
        if name in NAVIGATION_PAGES and name not in ordered:
            ordered.append(name)
    ordered.extend(name for name in DEFAULT_NAVIGATION_ORDER if name not in ordered)
    return ordered


def normalize_subscription_profile(value) -> dict:
    import math
    from datetime import date
    raw = value if isinstance(value, dict) else {}
    plan = str(raw.get("plan") or "").strip()[:64]
    price = raw.get("price_usd")
    try:
        price = float(price) if price is not None and not isinstance(price, bool) else None
        if price is not None and (not math.isfinite(price) or price < 0 or price > 1000000):
            price = None
    except (ValueError, TypeError, OverflowError):
        price = None
    renewal = str(raw.get("renewal_date") or "").strip()
    try:
        renewal = date.fromisoformat(renewal).isoformat() if renewal else ""
    except ValueError:
        renewal = ""
    return dict(plan=plan, price_usd=price, renewal_date=renewal)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_config() -> dict:
    return {
        "version": 1,
        "theme": "system",
        "widget_visible": True,
        "auto_sync_prices": True,
        "auto_update": True,
        "macos_auto_update": True,
        "upstream_detection_enabled": False,
        "show_log_source": False,
        "usage_refresh_interval_seconds": DEFAULT_USAGE_REFRESH_INTERVAL,
        "usage_refresh_interval_user_set": False,
        "week_estimate_interval_minutes": DEFAULT_WEEK_ESTIMATE_INTERVAL,
        "codex_roots": [os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")],
        "ssh_sources": [],
        "account_since": utc_now(),
        "history_assignments": [],
        "main_geometry": None,
        "navigation_order": list(DEFAULT_NAVIGATION_ORDER),
        "navigation_order_version": 1,
        **normalize_panel_layout({}),
        "subscription_profile": normalize_subscription_profile(None),
    }


def load_analytics_config(path: Path | None = None) -> dict:
    target = path or data_dir() / "analytics_settings.json"
    config = default_config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            config.update({k: v for k, v in raw.items() if k in config})
            # Adopt the new starting order once; subsequent drag-and-drop
            # preferences are saved with this marker and stay untouched.
            if raw.get("navigation_order_version") != 1:
                config["navigation_order"] = list(DEFAULT_NAVIGATION_ORDER)
                config["navigation_order_version"] = 1
    except (OSError, ValueError):
        pass
    if config["theme"] not in ("system", "dark", "light"):
        config["theme"] = "system"
    config["navigation_order"] = normalize_navigation_order(config.get("navigation_order"))
    config.update(normalize_panel_layout(config))
    config["subscription_profile"] = normalize_subscription_profile(config.get("subscription_profile"))
    for key in ("ssh_sources", "history_assignments"):
        if not isinstance(config[key], list):
            config[key] = []
        config[key] = [v for v in config[key] if isinstance(v, dict)]
    if not isinstance(config["codex_roots"], list):
        config["codex_roots"] = default_config()["codex_roots"]
    config["codex_roots"] = list(dict.fromkeys(str(v) for v in config["codex_roots"] if str(v).strip()))
    for key in ("widget_visible", "auto_sync_prices", "auto_update", "macos_auto_update", "show_log_source", "upstream_detection_enabled"):
        config[key] = config[key] if isinstance(config[key], bool) else False
    config["auto_sync_prices"] = True
    config["usage_refresh_interval_user_set"] = config.get("usage_refresh_interval_user_set") is True
    if not config["usage_refresh_interval_user_set"] and config.get("usage_refresh_interval_seconds") == 5:
        # The previous build saved its 5-second default without a choice marker.
        config["usage_refresh_interval_seconds"] = DEFAULT_USAGE_REFRESH_INTERVAL
    if config.get("usage_refresh_interval_seconds") not in USAGE_REFRESH_INTERVALS:
        config["usage_refresh_interval_seconds"] = DEFAULT_USAGE_REFRESH_INTERVAL
    if config.get("week_estimate_interval_minutes") not in WEEK_ESTIMATE_INTERVALS:
        config["week_estimate_interval_minutes"] = DEFAULT_WEEK_ESTIMATE_INTERVAL
    try:
        datetime.fromisoformat(str(config["account_since"]).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        config["account_since"] = utc_now()
    return copy.deepcopy(config)


def save_analytics_config(config: dict, path: Path | None = None) -> None:
    config = {key: value for key, value in config.items() if key not in ("lan_sources", "share_tokens", "usd_per_credit", "upstream_exit_prompt", "server_estimates_enabled")}
    config["auto_sync_prices"] = True
    config["navigation_order"] = normalize_navigation_order(config.get("navigation_order"))
    config.update(normalize_panel_layout(config))
    config["subscription_profile"] = normalize_subscription_profile(config.get("subscription_profile"))
    config["show_log_source"] = config.get("show_log_source") is True
    config["usage_refresh_interval_user_set"] = config.get("usage_refresh_interval_user_set") is True
    if config.get("usage_refresh_interval_seconds") not in USAGE_REFRESH_INTERVALS:
        config["usage_refresh_interval_seconds"] = DEFAULT_USAGE_REFRESH_INTERVAL
    if config.get("week_estimate_interval_minutes") not in WEEK_ESTIMATE_INTERVALS:
        config["week_estimate_interval_minutes"] = DEFAULT_WEEK_ESTIMATE_INTERVAL
    config["navigation_order_version"] = 1
    target = path or data_dir() / "analytics_settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
