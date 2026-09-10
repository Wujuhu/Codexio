from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import math
from typing import Any, Iterable, Optional, Tuple

FIVE_HOUR_MINUTES = 300
WEEK_MINUTES = 10080
FIVE_HOUR_TOLERANCE = 2
WEEK_TOLERANCE = 10


class QuotaStatus(str, Enum):
    READING = "reading"
    OK = "ok"
    ERROR = "error"
    STALE = "stale"


@dataclass(frozen=True)
class RateLimitWindow:
    used_percent: Optional[float]
    window_duration_mins: Optional[int]
    resets_at: Optional[int]

    @classmethod
    def from_payload(cls, payload: Any) -> Optional["RateLimitWindow"]:
        if not isinstance(payload, dict):
            return None
        used = _coerce_percent(first_value(payload, "usedPercent", "used_percent"))
        duration = _coerce_int(
            first_value(payload, "windowDurationMins", "window_duration_mins", "window_minutes")
        )
        resets = _coerce_int(first_value(payload, "resetsAt", "resets_at"))
        if used is None and duration is None and resets is None:
            return None
        return cls(used_percent=used, window_duration_mins=duration, resets_at=resets)


@dataclass(frozen=True)
class ResetCredit:
    """The per-credit fields published by the local app-server protocol."""

    id: str
    expires_at: Optional[datetime] = None
    expiry_known: bool = False
    granted_at: Optional[datetime] = None
    status: str = "unknown"
    reset_type: str = "unknown"
    title: Optional[str] = None
    description: Optional[str] = None


@dataclass(frozen=True)
class RateLimitSnapshot:
    primary: Optional[RateLimitWindow] = None
    secondary: Optional[RateLimitWindow] = None
    plan_type: Optional[str] = None
    limit_id: Optional[str] = None
    raw: Optional[dict] = None
    by_limit: Optional[dict] = None
    reset_credits: Optional[int] = None
    reset_credit_details: Optional[Tuple[ResetCredit, ...]] = None
    # Notifications may omit this account-level field; explicit null means unknown.
    _reset_credits_present: bool = field(default=False, repr=False)

    @classmethod
    def from_payload(cls, payload: Any) -> "RateLimitSnapshot":
        data = payload if isinstance(payload, dict) else {}
        return cls(
            primary=RateLimitWindow.from_payload(first_value(data, "primary")),
            secondary=RateLimitWindow.from_payload(first_value(data, "secondary")),
            plan_type=_coerce_str(first_value(data, "planType", "plan_type")),
            limit_id=_coerce_str(first_value(data, "limitId", "limit_id")),
            raw=data or None,
            reset_credits=_reset_credit_count(data),
            reset_credit_details=_reset_credit_details(data),
            _reset_credits_present=_has_reset_credits(data),
        )


@dataclass(frozen=True)
class WindowView:
    remaining_percent: Optional[int]
    used_percent: Optional[int]
    window_duration_mins: Optional[int]
    resets_at: Optional[datetime]

    @classmethod
    def unavailable(cls) -> "WindowView":
        return cls(None, None, None, None)

    @classmethod
    def from_window(cls, window: Optional[RateLimitWindow]) -> "WindowView":
        if window is None or window.used_percent is None:
            return cls.unavailable()
        return cls(
            remaining_percent=remaining_percent(int(round(window.used_percent))),
            used_percent=int(round(window.used_percent)),
            window_duration_mins=window.window_duration_mins,
            resets_at=_from_unix(window.resets_at),
        )


@dataclass(frozen=True)
class QuotaState:
    five_hour: WindowView
    week: WindowView
    status: QuotaStatus
    message: str
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
    plan_type: Optional[str] = None
    reset_credits: Optional[int] = None
    reset_credit_details: Optional[Tuple[ResetCredit, ...]] = None

    @classmethod
    def empty(cls, status: QuotaStatus = QuotaStatus.READING, message: str = "正在读取") -> "QuotaState":
        return cls(
            five_hour=WindowView.unavailable(),
            week=WindowView.unavailable(),
            status=status,
            message=message,
        )


def first_value(payload: dict, *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def remaining_percent(used_percent: Optional[int]) -> Optional[int]:
    clamped = _clamp_percent(used_percent)
    if clamped is None:
        return None
    return 100 - clamped


def parse_rate_limits_result(result: Any) -> RateLimitSnapshot:
    if not isinstance(result, dict):
        raise ValueError("rate limits result must be an object")
    snapshot_payload = first_value(result, "rateLimits", "rate_limits")
    buckets_raw = first_value(result, "rateLimitsByLimitId", "rate_limits_by_limit_id")
    buckets = {}
    if isinstance(buckets_raw, dict):
        for key, value in buckets_raw.items():
            if isinstance(value, dict):
                bucket = RateLimitSnapshot.from_payload(value)
                buckets[str(key)] = replace(bucket, limit_id=bucket.limit_id or str(key))
    if buckets:
        preferred = buckets.get("codex")
        if preferred is None:
            old = RateLimitSnapshot.from_payload(snapshot_payload)
            preferred = buckets.get(old.limit_id) if old.limit_id else None
        if preferred is None:
            preferred = next(iter(buckets.values()))
        snapshot = replace(preferred, by_limit=buckets)
    else:
        if snapshot_payload is None:
            raise ValueError("rate limits result is missing rateLimits")
        snapshot = RateLimitSnapshot.from_payload(snapshot_payload)
    # Reset credits belong to the account, outside both quota bucket views.
    if _has_reset_credits(result):
        snapshot = replace(
            snapshot,
            reset_credits=_reset_credit_count(result),
            reset_credit_details=_reset_credit_details(result),
            _reset_credits_present=True,
        )
    return snapshot


def merge_rate_limit_snapshots(
    base: Optional[RateLimitSnapshot],
    update: RateLimitSnapshot,
) -> RateLimitSnapshot:
    def merge_one(old, new):
        if old is None or (old.limit_id and new.limit_id and old.limit_id != new.limit_id):
            return replace(new, by_limit=None)
        return RateLimitSnapshot(
            primary=new.primary if new.primary is not None else old.primary,
            secondary=new.secondary if new.secondary is not None else old.secondary,
            plan_type=new.plan_type if new.plan_type is not None else old.plan_type,
            limit_id=new.limit_id if new.limit_id is not None else old.limit_id,
            raw=new.raw if new.raw is not None else old.raw,
        )

    if base is None:
        return update
    credit_source = update if update._reset_credits_present or update.reset_credits is not None else base

    def with_account_credits(snapshot):
        return replace(
            snapshot,
            reset_credits=credit_source.reset_credits,
            reset_credit_details=credit_source.reset_credit_details,
            _reset_credits_present=credit_source._reset_credits_present,
        )

    different_bucket = bool(base.limit_id and update.limit_id and base.limit_id != update.limit_id)
    if not base.by_limit and not update.by_limit and not different_bucket:
        return with_account_credits(merge_one(base, update))
    buckets = {key: replace(value, by_limit=None) for key, value in (base.by_limit or {}).items()}
    if not buckets:
        buckets[base.limit_id or "codex"] = replace(base, by_limit=None)
    incoming = update.by_limit or {update.limit_id or base.limit_id or "codex": update}
    for key, value in incoming.items():
        buckets[key] = merge_one(buckets.get(key), value)
    chosen = buckets.get("codex") or buckets.get(base.limit_id) or next(iter(buckets.values()))
    return with_account_credits(replace(chosen, by_limit=buckets))


def classify_windows(
    snapshot: RateLimitSnapshot,
) -> Tuple[Optional[RateLimitWindow], Optional[RateLimitWindow]]:
    five_hour = None
    week = None
    unmatched: list[RateLimitWindow] = []
    for window in _iter_windows(snapshot):
        if window is None:
            continue
        if five_hour is None and _matches_duration(window.window_duration_mins, FIVE_HOUR_MINUTES, FIVE_HOUR_TOLERANCE):
            five_hour = window
            continue
        if week is None and _matches_duration(window.window_duration_mins, WEEK_MINUTES, WEEK_TOLERANCE):
            week = window
            continue
        unmatched.append(window)
    if week is None:
        week = next((item for item in unmatched if _looks_like_week(item)), None)
    return five_hour, week


def _looks_like_week(window: RateLimitWindow) -> bool:
    duration = window.window_duration_mins
    if duration is None:
        return True
    if _matches_duration(duration, WEEK_MINUTES, WEEK_TOLERANCE):
        return True
    return duration >= 1440


def should_show_five_hour(scope: str, state: QuotaState) -> bool:
    if scope == "week":
        return False
    if scope == "both":
        return True
    if state.five_hour.remaining_percent is not None:
        return True
    if state.week.remaining_percent is not None:
        return False
    return True


def quota_state_from_snapshot(
    snapshot: RateLimitSnapshot,
    *,
    status: QuotaStatus,
    message: str,
    last_success_at: Optional[datetime] = None,
    last_error: Optional[str] = None,
) -> QuotaState:
    five_hour, week = classify_windows(snapshot)
    return QuotaState(
        five_hour=WindowView.from_window(five_hour),
        week=WindowView.from_window(week),
        status=status,
        message=message,
        last_success_at=last_success_at,
        last_error=last_error,
        plan_type=snapshot.plan_type,
        reset_credits=snapshot.reset_credits,
        reset_credit_details=snapshot.reset_credit_details,
    )


def snapshot_to_cache(snapshot: RateLimitSnapshot, fetched_at: datetime) -> dict:
    payload = {
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat(),
        "rateLimits": {
            "primary": _window_to_dict(snapshot.primary),
            "secondary": _window_to_dict(snapshot.secondary),
            "planType": snapshot.plan_type,
            "limitId": snapshot.limit_id,
        },
    }
    if snapshot.by_limit:
        payload["rateLimitsByLimitId"] = {
            key: snapshot_to_cache(replace(value, by_limit=None), fetched_at)["rateLimits"]
            for key, value in snapshot.by_limit.items()
        }
    if snapshot._reset_credits_present or snapshot.reset_credits is not None:
        payload["rateLimitResetCredits"] = (
            {"availableCount": snapshot.reset_credits} if snapshot.reset_credits is not None else None
        )
        if snapshot.reset_credit_details is not None:
            summary = {"availableCount": snapshot.reset_credits, "credits": []}
            for credit in snapshot.reset_credit_details:
                item = dict(id=credit.id, status=credit.status, resetType=credit.reset_type,
                            title=credit.title, description=credit.description,
                            grantedAt=int(credit.granted_at.timestamp()) if credit.granted_at else None)
                if credit.expiry_known:
                    item["expiresAt"] = int(credit.expires_at.timestamp()) if credit.expires_at else None
                summary["credits"].append(item)
            payload["rateLimitResetCredits"] = summary
    return payload


def snapshot_from_cache(payload: dict) -> Tuple[Optional[RateLimitSnapshot], Optional[datetime]]:
    if not isinstance(payload, dict):
        return None, None
    fetched_at = _parse_iso(payload.get("fetched_at"))
    rate_limits = first_value(payload, "rateLimits", "rate_limits")
    if not isinstance(rate_limits, dict):
        return None, fetched_at
    return parse_rate_limits_result(payload), fetched_at


WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def format_reset_date(value: datetime, *, split_time: bool = False) -> str:
    local = value.astimezone()
    separator = "\n" if split_time else " "
    return local.strftime("%Y/%m/%d") + " " + WEEKDAY_NAMES[local.weekday()] + separator + local.strftime("%H:%M")


def format_reset_time(value: Optional[datetime], now: Optional[datetime] = None) -> str:
    if value is None:
        return "N/A"
    current = now.astimezone() if now is not None else datetime.now().astimezone()
    local = value.astimezone()
    today = current.date()
    if local.date() == today:
        date = "今天"
    elif local.date() == today + timedelta(days=1):
        date = "明天"
    else:
        date = "%d月%d日" % (local.month, local.day)
    return "%s %s %s" % (date, WEEKDAY_NAMES[local.weekday()], local.strftime("%H:%M"))


def format_updated_time(value: Optional[datetime], now: Optional[datetime] = None) -> str:
    if value is None:
        return "尚未更新"
    current = now or datetime.now().astimezone()
    local = value.astimezone()
    delta = (current - local).total_seconds()
    if delta < 45:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % max(1, int(delta // 60))
    if local.date() == current.date():
        return "今天 %s" % local.strftime("%H:%M")
    return local.strftime("%m-%d %H:%M")


def stale_after_seconds(refresh_interval_seconds: int) -> int:
    return max(120, refresh_interval_seconds * 2)


def next_backoff_seconds(failures: int, cap: int = 300) -> int:
    if failures <= 0:
        return 1
    return min(cap, 2 ** min(failures, 16))


def _iter_windows(snapshot: RateLimitSnapshot) -> Iterable[Optional[RateLimitWindow]]:
    yield snapshot.primary
    yield snapshot.secondary


def _matches_duration(actual: Optional[int], expected: int, tolerance: int) -> bool:
    if actual is None:
        return False
    return abs(int(actual) - expected) <= tolerance


def _window_to_dict(window: Optional[RateLimitWindow]) -> Optional[dict]:
    if window is None:
        return None
    return {
        "usedPercent": window.used_percent,
        "windowDurationMins": window.window_duration_mins,
        "resetsAt": window.resets_at,
    }


def _from_unix(value: Optional[int]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).astimezone()
    except (OSError, OverflowError, ValueError):
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return None


def _has_reset_credits(payload: dict) -> bool:
    return "rateLimitResetCredits" in payload or "rate_limit_reset_credits" in payload


def _reset_credit_count(payload: dict) -> Optional[int]:
    credits = first_value(payload, "rateLimitResetCredits", "rate_limit_reset_credits")
    if not isinstance(credits, dict):
        return None
    value = first_value(credits, "availableCount", "available_count")
    # availableCount is authoritative: details can be missing or truncated.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        count = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(count) or count < 0 or not count.is_integer():
        return None
    return int(count)


def _reset_credit_details(payload: dict) -> Optional[Tuple[ResetCredit, ...]]:
    summary = first_value(payload, "rateLimitResetCredits", "rate_limit_reset_credits")
    if not isinstance(summary, dict) or not isinstance(summary.get("credits"), list):
        return None
    result, seen = [], set()
    for item in summary["credits"][:1000]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen:
            continue
        seen.add(item["id"])
        present = "expiresAt" in item or "expires_at" in item
        expires = first_value(item, "expiresAt", "expires_at")
        valid = present and (expires is None or not isinstance(expires, bool) and isinstance(expires, (int, float)) and math.isfinite(expires))
        deadline = _from_unix(_coerce_int(expires)) if expires is not None and valid else None
        if expires is not None and deadline is None:
            valid = False
        status = str(item.get("status") or "unknown")
        result.append(ResetCredit(id=item["id"], expires_at=deadline, expiry_known=bool(valid),
                                  granted_at=_from_unix(_coerce_int(first_value(item, "grantedAt", "granted_at"))),
                                  status=status if status in ("available", "redeeming", "redeemed") else "unknown",
                                  reset_type=str(first_value(item, "resetType", "reset_type") or "unknown"),
                                  title=_coerce_str(item.get("title")), description=_coerce_str(item.get("description"))))
    return tuple(result)


def _coerce_percent(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return max(0.0, min(100.0, number)) if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _coerce_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clamp_percent(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return max(0, min(100, int(value)))
