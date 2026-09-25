from __future__ import annotations

import threading
import base64
import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from codexio.app_server import (
    ACCOUNT_UPDATED,
    RATE_LIMITS_UPDATED,
    AppServerClient,
    AppServerError,
    CodexNotFoundError,
    JsonRpcMessage,
    discover_codex_executable,
)
from codexio.logging_setup import get_logger
from codexio.rate_limits import (
    QuotaState,
    QuotaStatus,
    RateLimitSnapshot,
    merge_rate_limit_snapshots,
    next_backoff_seconds,
    parse_rate_limits_result,
    quota_state_from_snapshot,
    snapshot_from_cache,
    snapshot_to_cache,
    stale_after_seconds,
)
from codexio.settings import AppSettings, load_quota_cache, save_quota_cache, save_settings
from codexio.provider_config import ProviderConfigError, resolve_provider_config

logger = get_logger("worker")


def _auth_identity():
    """Read identity only; never retain or log the local OAuth token."""
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
    try:
        tokens = json.loads(path.read_text(encoding="utf-8")).get("tokens") or {}
        account = tokens.get("account_id")
        token = tokens.get("access_token")
        if not isinstance(account, str) or not account or not isinstance(token, str):
            return None
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        subject = claims.get("sub")
        return (account, subject) if isinstance(subject, str) and subject else None
    except (OSError, ValueError, TypeError, IndexError, AttributeError, UnicodeError):
        return None


def _account_key(payload, before, after):
    if before != after:
        return None
    remote = payload.get("accountId", payload.get("account_id")) if isinstance(payload, dict) else None
    remote = remote if isinstance(remote, str) and remote else None
    if remote and after and remote != after[0]:
        return None
    account = remote or (after[0] if after else None)
    if not account:
        return None
    subject = after[1] if after else ""
    return hashlib.sha256(json.dumps(["codexio-week-v1", account, subject], separators=(",", ":")).encode()).hexdigest()


class QuotaWorker(QThread):
    state_changed = Signal(object)
    snapshot_changed = Signal(object, str)
    identity_changed = Signal()
    applicability_changed = Signal(bool, str)

    def __init__(self, settings: AppSettings, mock: bool = False) -> None:
        super().__init__()
        self._settings = settings
        self._mock = mock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._refresh_requested = threading.Event()
        self._reconnect_requested = threading.Event()
        self._notification: Optional[JsonRpcMessage] = None
        self._account_change_pending = False
        self._notification_lock = threading.Lock()
        self._client: Optional[AppServerClient] = None
        self._snapshot: Optional[RateLimitSnapshot] = None
        self._local_snapshot: Optional[RateLimitSnapshot] = None
        self._last_success_at: Optional[datetime] = None
        self._local_success_at: Optional[datetime] = None
        self._failures = 0
        self._status = QuotaStatus.READING
        self._message = "正在读取"
        self._applicable: Optional[bool] = True if mock else None
        self._auth_mode: Optional[str] = "mock" if mock else None
        self._cache_loaded = False

    def run(self) -> None:
        self._emit_state()
        while not self._stop.is_set():
            self._wake.clear()
            try:
                if self._mock:
                    self._run_mock_cycle()
                else:
                    self._run_live_cycle()
            except Exception as exc:
                logger.exception("额度读取循环异常")
                self._on_failure("读取失败: %s" % _user_error(exc))
            self._wait_for_next()

    def stop(self, timeout_ms: int = 8000) -> None:
        self.request_stop()
        self.wait(timeout_ms)

    def request_stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._close_client()

    def request_refresh(self) -> None:
        self._refresh_requested.set()
        self._wake.set()

    def set_interval(self, seconds: int) -> None:
        self._settings.refresh_interval_seconds = seconds
        save_settings(self._settings)
        self._wake.set()

    def update_settings(self, settings: AppSettings) -> None:
        if self._settings.codex_path != settings.codex_path:
            self._reconnect_requested.set()
        self._settings = settings.normalized()
        self.request_refresh()

    def _run_live_cycle(self) -> None:
        if self._reconnect_requested.is_set():
            self._reconnect_requested.clear()
            self._close_client()
            self._applicable = None
            self._auth_mode = None
            self._cache_loaded = False
        self._ensure_client()
        with self._notification_lock:
            changed_account = self._account_change_pending
            self._account_change_pending = False
            if changed_account:
                self._notification = None
        if self._applicable is None or changed_account or self._refresh_requested.is_set():
            self._refresh_account_mode(force_identity=changed_account)
        if not self._applicable:
            if self._status != QuotaStatus.NOT_APPLICABLE:
                self._status = QuotaStatus.NOT_APPLICABLE
                self._message = ""
                self._emit_state()
            return
        if not self._cache_loaded:
            self._load_cache()
            self._cache_loaded = True
            if self._snapshot is not None:
                self._emit_state()
        self._fetch_local()

    def _fetch_local(self) -> None:
        notification = self._take_notification()
        if notification is not None:
            if self._apply_notification(notification):
                return
        if self._snapshot is None:
            self._set_status(QuotaStatus.READING, "正在读取")
        before = _auth_identity()
        result = self._require_client().read_rate_limits()
        after = _auth_identity()
        snapshot = replace(parse_rate_limits_result(result), account_key=_account_key(result, before, after))
        self._store_local(snapshot)
        self._on_success(snapshot, "已更新", persist=False)

    @staticmethod
    def _account_mode(result: dict, provider) -> tuple[bool, str]:
        account = result.get("account") if isinstance(result, dict) else None
        account_type = str(account.get("type") or "").strip() if isinstance(account, dict) else ""
        normalized = account_type.replace("_", "").replace("-", "").casefold()
        requires = result.get("requiresOpenaiAuth", result.get("requires_openai_auth"))
        if not isinstance(requires, bool):
            requires = provider.requires_openai_auth
        api_types = {"apikey", "amazonbedrock", "bedrockapikey"}
        chatgpt_types = {"chatgpt", "chatgptauthtokens", "agentidentity", "personalaccesstoken"}
        custom = provider.provider_id != "openai"
        forced_api = provider.forced_login_method == "api"
        applicable = (not custom and not forced_api and requires
                      and (not normalized or normalized in chatgpt_types) and normalized not in api_types)
        if custom:
            mode = "provider:" + provider.provider_id
        elif forced_api or not requires:
            mode = "api"
        elif normalized:
            mode = normalized
        else:
            mode = "unknown"
        return bool(applicable), mode

    def _refresh_account_mode(self, *, force_identity=False) -> None:
        result = self._require_client().read_account(False)
        try:
            provider = resolve_provider_config()
        except ProviderConfigError as exc:
            raise AppServerError(str(exc)) from exc
        applicable, mode = self._account_mode(result, provider)
        changed = applicable != self._applicable or mode != self._auth_mode
        if not changed and not force_identity:
            return
        self._applicable, self._auth_mode = applicable, mode
        self._snapshot = self._local_snapshot = None
        self._last_success_at = self._local_success_at = None
        self._cache_loaded = bool(force_identity)
        self.identity_changed.emit()
        self.applicability_changed.emit(applicable, mode)
        if applicable:
            self._status, self._message = QuotaStatus.READING, "正在读取"
        else:
            self._status, self._message = QuotaStatus.NOT_APPLICABLE, ""
        self._emit_state()

    def _run_mock_cycle(self) -> None:
        now = int(datetime.now().timestamp())
        snapshot = RateLimitSnapshot.from_payload(
            {
                "primary": {
                    "usedPercent": 28,
                    "windowDurationMins": 300,
                    "resetsAt": now + 3600,
                },
                "secondary": {
                    "usedPercent": 38,
                    "windowDurationMins": 10080,
                    "resetsAt": now + 86400 * 3,
                },
                "planType": "mock",
                "rateLimitResetCredits": {"availableCount": 2},
            }
        )
        self._store_local(snapshot)
        self._on_success(snapshot, "模拟数据", persist=False)

    def _store_local(self, snapshot: RateLimitSnapshot) -> None:
        if self._applicable is False:
            return
        self._local_snapshot = snapshot
        self._local_success_at = datetime.now().astimezone()
        self.snapshot_changed.emit(snapshot, "local-quota")
        try:
            if not self._mock:
                save_quota_cache(snapshot_to_cache(snapshot, self._local_success_at))
        except OSError:
            logger.warning("写入额度缓存失败")

    def _ensure_client(self) -> None:
        client = self._client
        if client is not None and client.running:
            return
        self._close_client()
        self._set_status(QuotaStatus.READING, "正在连接 Codex")
        excluded = set()
        last_error = None
        for _attempt in range(4):
            try:
                executable = discover_codex_executable(self._settings.codex_path, excluded=excluded)
            except CodexNotFoundError:
                if last_error is not None:
                    raise AppServerError("Codex 组件无法启动：%s" % last_error) from last_error
                raise
            client = AppServerClient(executable=executable, on_notification=self._on_notification)
            try:
                client.start()
            except (OSError, AppServerError) as exc:
                last_error = exc
                excluded.add(executable)
                client.close()
                logger.warning("Codex 组件启动失败，将尝试其他安装位置：%s (%s)", executable, exc)
                continue
            self._client = client
            return
        raise AppServerError("Codex 组件无法启动：%s" % last_error)

    def _require_client(self) -> AppServerClient:
        client = self._client
        if client is None:
            raise AppServerError("Codex app-server 未启动")
        return client

    def _on_notification(self, message: JsonRpcMessage) -> None:
        if message.method == ACCOUNT_UPDATED:
            with self._notification_lock:
                self._account_change_pending = True
            self._refresh_requested.set()
            self._wake.set()
            return
        if message.method != RATE_LIMITS_UPDATED:
            return
        if self._applicable is False:
            return
        with self._notification_lock:
            self._notification = message
        self._wake.set()

    def _take_notification(self) -> Optional[JsonRpcMessage]:
        with self._notification_lock:
            message = self._notification
            self._notification = None
            return message

    def _apply_notification(self, message: JsonRpcMessage) -> bool:
        if self._applicable is False:
            return False
        params = message.params if isinstance(message.params, dict) else {}
        try:
            incoming = parse_rate_limits_result(params) if any(key in params for key in ("rateLimits", "rate_limits", "rateLimitsByLimitId", "rate_limits_by_limit_id")) else (
                RateLimitSnapshot.from_payload(params.get("rateLimits") or params.get("rate_limits") or params)
            )
        except ValueError:
            logger.warning("忽略无法解析的额度通知")
            return False
        before = after = _auth_identity()
        incoming = replace(incoming, account_key=_account_key(params, before, after))
        base = self._snapshot
        merged = merge_rate_limit_snapshots(base, incoming)
        self._store_local(merged)
        self._on_success(merged, "已收到额度更新", persist=False)
        return base is not None

    def _on_success(self, snapshot: RateLimitSnapshot, message: str, persist: bool = True) -> None:
        self._snapshot = snapshot
        self._last_success_at = datetime.now().astimezone()
        self._failures = 0
        self._status = QuotaStatus.OK
        self._message = message
        if persist:
            try:
                save_quota_cache(snapshot_to_cache(snapshot, self._last_success_at))
            except OSError:
                logger.warning("写入额度缓存失败")
        self._emit_state()

    def _on_failure(self, message: str) -> None:
        self._failures += 1
        self._status = QuotaStatus.ERROR
        self._message = message
        self._close_client()
        self._emit_state()

    def _load_cache(self) -> None:
        snapshot, fetched_at = snapshot_from_cache(load_quota_cache())
        if snapshot is None:
            return
        self._local_snapshot = snapshot
        self._local_success_at = fetched_at
        self._snapshot = snapshot
        self._last_success_at = fetched_at
        self._status = QuotaStatus.STALE
        self._message = "已加载上次缓存"

    def _wait_for_next(self) -> None:
        if self._stop.is_set():
            return
        if self._refresh_requested.is_set() or self._notification is not None:
            self._refresh_requested.clear()
            return
        if self._status == QuotaStatus.ERROR:
            delay = next_backoff_seconds(self._failures)
        else:
            delay = self._settings.refresh_interval_seconds
        self._wake.wait(delay)
        self._refresh_requested.clear()

    def _emit_state(self) -> None:
        now = datetime.now().astimezone()
        status = self._status
        message = self._message
        if (
            self._snapshot is not None
            and self._last_success_at is not None
            and status == QuotaStatus.OK
            and (now - self._last_success_at).total_seconds()
            > stale_after_seconds(self._settings.refresh_interval_seconds)
        ):
            status = QuotaStatus.STALE
            message = "数据过期"
        if self._applicable is None:
            state = QuotaState.empty(status=status, message=message,
                                     applicable=False, auth_mode=self._auth_mode)
        elif self._applicable is False:
            state = QuotaState.empty(status=QuotaStatus.NOT_APPLICABLE, message="",
                                     applicable=False, auth_mode=self._auth_mode)
        elif self._snapshot is None:
            state = QuotaState.empty(status=status, message=message, auth_mode=self._auth_mode)
            state = QuotaState(
                five_hour=state.five_hour,
                week=state.week,
                status=status,
                message=message,
                last_success_at=self._last_success_at,
                last_error=message if status == QuotaStatus.ERROR else None,
                applicable=True,
                auth_mode=self._auth_mode,
            )
        else:
            state = quota_state_from_snapshot(
                self._snapshot,
                status=status,
                message=message,
                last_success_at=self._last_success_at,
                last_error=message if status == QuotaStatus.ERROR else None,
                applicable=True,
                auth_mode=self._auth_mode,
            )
        self.state_changed.emit(state)

    def _set_status(self, status: QuotaStatus, message: str) -> None:
        self._status = status
        self._message = message
        self._emit_state()

    def _close_client(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                logger.warning("关闭 app-server 失败")


def _user_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if isinstance(exc, CodexNotFoundError):
        return "未找到 Codex，请确认已安装并登录"
    if "authentication" in lowered or "login" in lowered or "unauthorized" in lowered:
        return "未登录 ChatGPT / Codex"
    if "error sending request" in lowered or "wham/usage" in lowered or "connection" in lowered:
        return "无法连接 ChatGPT（网络或代理异常）"
    if "timeout" in lowered or "超时" in text:
        return "读取超时"
    if (
        "error sending request" in lowered
        or "failed to fetch" in lowered
        or "network" in lowered
        or "connection" in lowered
    ):
        return "网络异常，无法读取额度"
    if isinstance(exc, AppServerError):
        return text
    return exc.__class__.__name__
