"""Background metering coordinator; the GUI never scans conversation files."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from codexio.analytics_config import DEFAULT_USAGE_REFRESH_INTERVAL, USAGE_REFRESH_INTERVALS, utc_now
from codexio.logging_setup import get_logger
from codexio.settings import data_dir

logger = get_logger("usage")


def parse_time(value) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _count(value) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    try:
        number = float(value)
        return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _cost(record):
    value = record.get("cost_usd")
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _remote_source_id(kind, source):
    value = str(source.get("id") or source.get("host"))
    return value if value.startswith(kind + ":") else kind + ":" + value


def summarize(records: list[dict], now: datetime | None = None) -> dict:
    local = (now or datetime.now().astimezone()).astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = {"today": midnight, "week": midnight - timedelta(days=6), "month": midnight - timedelta(days=29), "all": None}
    output = {}
    dated = [(r, parse_time(r.get("timestamp"))) for r in records]
    for key, start in starts.items():
        rows = [r for r, stamp in dated if stamp is not None and (start is None or stamp >= start) and stamp <= local]
        input_total = sum(_count(r.get("input_tokens", 0)) for r in rows)
        costs = [_cost(r) for r in rows]
        known_costs = [value for value in costs if value is not None]
        unpriced = sum(value is None for value in costs)
        output[key] = {
            "tokens": sum(_count(r.get("total_tokens", 0)) for r in rows),
            "usd": math.fsum(known_costs) if known_costs or not rows else None,
            "cost_status": "unpriced" if rows and not known_costs else ("partial" if unpriced else "priced"),
            "requests": sum(1 for r in rows if str(r.get("quality", "")).split(":", 1)[0]
                            not in ("aggregate", "cumulative", "cumulative_observation", "unresolved")),
            "unpriced_requests": unpriced,
            "unpriced_tokens": sum(_count(r.get("total_tokens", 0)) for r in rows if _cost(r) is None),
            "input_tokens": input_total,
            "cached_input_tokens": sum(_count(r.get("cached_input_tokens", 0)) for r in rows),
            "cache_hit_rate": sum(_count(r.get("cached_input_tokens", 0)) for r in rows) / input_total if input_total else 0,
        }
    return output


class UsageWorker(QThread):
    data_changed = Signal(object)
    progress_changed = Signal(str)
    loading_changed = Signal(object)

    def __init__(self, config: dict, mock: bool = False, directory: Path | None = None,
                 widget_only: bool = False) -> None:
        super().__init__()
        self._config = copy.deepcopy(config)
        self._mock = mock
        self._widget_only = widget_only
        self._directory = directory or data_dir()
        self._commands = queue.Queue()
        self._stop_event = threading.Event()
        self._wake = threading.Event()
        self._config_changed = threading.Event()
        self._store = None
        self._catalog = None
        self._scan_status = "正在建立用量索引"
        self._initial_loading = True
        self._loading_stage = None
        self._estimates_visible = False
        self._estimates = []
        self._quota_applicable = bool(mock)
        self._rolling = None
        self._last_payload_signature = None
        self._last_widget_signature = None
        self._last_progress = None

    def update_config(self, config: dict) -> None:
        self._commands.put(("config", copy.deepcopy(config)))
        self._config_changed.set()
        self._wake.set()

    def request_refresh(self) -> None:
        self._commands.put(("refresh", None))
        self._wake.set()

    def request_sync(self) -> None:
        self._commands.put(("sync", None))
        self._wake.set()

    def set_estimates_visible(self, visible: bool) -> None:
        self._commands.put(("estimates_visible", bool(visible)))
        self._wake.set()

    def set_price_override(self, model: str, rates: dict | None) -> None:
        self._commands.put(("override", (model, rates)))
        self._wake.set()

    def rescan(self) -> None:
        self._commands.put(("rescan", None))
        self._wake.set()

    def add_snapshot(self, snapshot, source_id: str = "local-quota") -> None:
        if self._mock or not self._quota_applicable:
            return
        bucket = (snapshot.by_limit or {}).get("codex") or snapshot
        window = next((value for value in (bucket.primary, bucket.secondary)
                       if value is not None and value.window_duration_mins is not None
                       and abs(value.window_duration_mins - 10080) <= 10), None)
        self._commands.put(("weekly_sample", dict(
            timestamp=time.time(), used_percent=window.used_percent if window else None,
            reset_at=window.resets_at if window else None,
            plan_type=bucket.plan_type or snapshot.plan_type,
            account_key=snapshot.account_key, limit_id=bucket.limit_id or "codex",
            sole_codex_pool=set((snapshot.by_limit or {}).keys()) == {"codex"})))
        self._wake.set()

    def set_quota_applicable(self, applicable: bool, _mode: str = "") -> None:
        self._commands.put(("quota_applicable", bool(applicable)))
        self._wake.set()

    def invalidate_estimate_window(self) -> None:
        self._commands.put(("estimate_invalidate", None))
        self._wake.set()

    def stop(self, timeout_ms: int = 45000) -> bool:
        self.request_stop()
        if self.isRunning():
            return self.wait(timeout_ms)
        return True

    def request_stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def _cancel_requested(self):
        return self._stop_event.is_set() or self._config_changed.is_set()

    def _local_sources(self):
        for index, raw in enumerate(self._config.get("codex_roots", [])):
            root = Path(raw).expanduser().resolve()
            source_id = "local" if index == 0 else "local:" + hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16]
            yield source_id, root

    def _active_sources(self):
        if self._mock:
            return {"local"}
        active = {source_id for source_id, _root in self._local_sources()}
        for kind in ("ssh",):
            active.update(_remote_source_id(kind, s) for s in self._config.get(kind + "_sources", []) if s.get("enabled", True))
        return active

    def _report_initial_loading(self, stage: str) -> None:
        if self._initial_loading and stage != self._loading_stage:
            self._loading_stage = stage
            self.loading_changed.emit({"loading": True, "stage": stage, "error": None})

    def _finish_initial_loading(self, stage: str, error: str | None = None) -> None:
        if self._initial_loading:
            self._initial_loading = False
            self._loading_stage = stage
            self.loading_changed.emit({"loading": False, "stage": stage, "error": error})

    def run(self) -> None:
        # Emit from the running thread, before reading SQLite or price files, so
        # the GUI can show an indeterminate progress indicator immediately.
        self._report_initial_loading("正在准备本地用量数据")
        try:
            from codexio.usage_store import UsageStore
            from codexio.usage_collector import Collector
            from codexio.pricing import PricingCatalog
            from codexio.rolling_estimation import RollingWeeklyEstimator
            self._directory.mkdir(parents=True, exist_ok=True)
            self._store = UsageStore(self._directory / ("usage_mock.sqlite" if self._mock else "usage.sqlite"))
            self._catalog = PricingCatalog(self._directory / ("mock_prices" if self._mock else "prices"))
            if self._mock and not self._widget_only:
                self._rolling = RollingWeeklyEstimator(self._store.path,
                    self._config.get("week_estimate_interval_minutes", 10))
            collector = Collector(self._store)
            self._run_loop(collector)
        except Exception as exc:
            logger.exception("用量统计服务异常")
            self.progress_changed.emit("用量服务启动失败，请检查运行日志")
            self._finish_initial_loading("用量数据加载失败", "用量服务启动失败（%s），请检查运行日志" % type(exc).__name__)
        finally:
            # Stopping before the first publication cancels loading; it must
            # not leave a busy indicator running or claim data was loaded.
            self._finish_initial_loading("已停止加载")

    def _run_loop(self, collector) -> None:
        dirty, next_remote, next_publish, next_sync = True, 0.0, 0.0, 0.0
        startup_price_sync = True
        startup_sync_inflight = False
        if self._mock:
            self._report_initial_loading("正在准备模拟用量数据")
            self._seed_mock()
        elif self._config.get("auto_sync_prices"):
            # Start the network check at process startup while the normal scan
            # continues. The catalog serializes its own cache updates.
            startup_price_sync = False
            startup_sync_inflight = True
            next_sync = time.monotonic() + 86400
            self.progress_changed.emit("正在同步模型价格")

            def sync_on_launch():
                try:
                    result = self._catalog.sync(force=False)
                except Exception:
                    logger.exception("启动时模型价格同步失败，沿用已缓存价格")
                    result = {"status": "offline"}
                self._commands.put(("startup_price_sync_done", result))
                self._wake.set()

            threading.Thread(target=sync_on_launch, name="Codexio-price-startup", daemon=True).start()
        while not self._stop_event.is_set():
            self._wake.clear()
            force_sync = False
            for _ in range(128):
                if self._stop_event.is_set():
                    break
                try:
                    command, value = self._commands.get_nowait()
                except queue.Empty:
                    break
                try:
                    publish_for_command = True
                    if command == "config":
                        if self._mock:
                            # UI preferences carry the real enrollment timestamp;
                            # changing theme must not unassign the synthetic history.
                            value["account_since"] = self._config["account_since"]
                        was_auto_sync = bool(self._config.get("auto_sync_prices"))
                        self._config = value
                        if self._rolling is not None:
                            self._rolling.configure(value.get("week_estimate_interval_minutes", 10))
                        self._config_changed.clear()
                        next_remote = 0
                        if self._config.get("auto_sync_prices") and not was_auto_sync:
                            next_sync = 0
                    elif command == "weekly_sample":
                        if self._rolling is not None and self._quota_applicable:
                            previous = self._rolling.latest
                            self._rolling.add_sample(value)
                            current = self._rolling.latest
                            publish_for_command = self._estimates_visible and (
                                previous is None or current is None or
                                self._rolling._identity(previous) != self._rolling._identity(current) or
                                abs(previous["reset_at"] - current["reset_at"]) > 60)
                        else:
                            publish_for_command = False
                    elif command == "estimate_invalidate":
                        if self._rolling is not None:
                            self._rolling.invalidate()
                        publish_for_command = self._estimates_visible
                    elif command == "quota_applicable":
                        self._quota_applicable = bool(value)
                        self._estimates = []
                        if self._quota_applicable and self._rolling is None and not self._widget_only:
                            from codexio.rolling_estimation import RollingWeeklyEstimator
                            self._rolling = RollingWeeklyEstimator(
                                self._store.path, self._config.get("week_estimate_interval_minutes", 10))
                        if self._rolling is not None:
                            self._rolling.invalidate()
                    elif command == "rescan":
                        self._store.clear_index()
                        collector.invalidate()
                        if self._mock:
                            self._seed_mock()
                    elif command == "override":
                        self._catalog.set_override(*value)
                    elif command == "sync":
                        force_sync = not startup_sync_inflight
                    elif command == "startup_price_sync_done":
                        startup_sync_inflight = False
                        next_sync = time.monotonic() + value.get("next_check_seconds",
                            3600 if value.get("status") == "offline" else 86400)
                    elif command == "refresh":
                        next_remote = 0
                    elif command == "estimates_visible":
                        self._estimates_visible = value
                    dirty = publish_for_command or dirty
                except Exception:
                    logger.exception("用量操作失败: %s", command)
                    self.progress_changed.emit("操作失败，已保留现有数据")
            if not self._mock:
                self._report_initial_loading("正在扫描本机日志")
                for index, (source_id, root) in enumerate(self._local_sources()):
                    if self._cancel_requested():
                        break
                    try:
                        result = collector.scan(root, source_id=source_id, source_name="本机" if index == 0 else root.name,
                                                account_since=self._config.get("account_since"), stop=self._cancel_requested)
                        dirty = bool(result.get("changed")) or dirty
                        self._scan_status = result.get("error") or "已索引 %s 个日志文件" % result.get("indexed_files", result.get("files", 0))
                        if self._scan_status != self._last_progress:
                            self._last_progress = self._scan_status
                            self.progress_changed.emit(self._scan_status)
                    except Exception:
                        logger.exception("本地计量扫描失败")
                        self._store.set_source_status(source_id, status="error", name="本机", error="日志扫描失败", last_scan_at=utc_now())
                        self._scan_status = "部分来源读取失败"
                        dirty = True
                if time.monotonic() >= next_remote:
                    if any(s.get("enabled", True) for kind in ("ssh",)
                           for s in self._config.get(kind + "_sources", [])):
                        self._report_initial_loading("正在同步远程用量")
                    if self._config.get("ssh_sources"):
                        dirty = self._collect_remote() or dirty
                    next_remote = time.monotonic() + 60
            if self._stop_event.is_set():
                break
            if self._quota_applicable and self._rolling is not None and self._rolling.ready(time.time()):
                dirty = True
            if dirty or time.monotonic() >= next_publish:
                self._publish()
                dirty = False
                next_publish = time.monotonic() + self._next_publish_delay()
            if not self._mock and not startup_sync_inflight and not self._cancel_requested() and (force_sync or (
                    self._config.get("auto_sync_prices") and (startup_price_sync or time.monotonic() >= next_sync))):
                self.progress_changed.emit("正在同步模型价格")
                try:
                    result = self._catalog.sync(force=force_sync or startup_price_sync)
                except Exception:
                    logger.exception("模型价格同步失败，沿用已缓存价格")
                    result = {"status": "offline"}
                # Each launch checks online once; while running, successful checks
                # recur after 24 hours and failed checks retry after one hour.
                startup_price_sync = False
                next_sync = time.monotonic() + (3600 if result.get("status") == "offline" else 86400)
                dirty = True
            interval = self._config.get("usage_refresh_interval_seconds")
            if interval not in USAGE_REFRESH_INTERVALS:
                interval = DEFAULT_USAGE_REFRESH_INTERVAL
            self._wake.wait(0.1 if dirty else interval)

    def _next_publish_delay(self) -> float:
        now = datetime.now().astimezone()
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).astimezone()
        deadlines = [midnight, getattr(self, "_next_summary_at", None)]
        seconds = [(deadline - now).total_seconds() for deadline in deadlines if deadline is not None and deadline > now]
        return max(1.0, min([3600.0, *seconds]))

    def _collect_remote(self) -> bool:
        from codexio.remote_collector import collect_ssh
        changed = False
        for source in self._config.get("ssh_sources", []):
            if not source.get("enabled", True) or self._stop_event.is_set():
                continue
            if self._config_changed.is_set():
                return changed
            source_id = _remote_source_id("ssh", source)
            try:
                cursors = self._store.get_meta("cursor:" + source_id) or {}
                result = collect_ssh(dict(source, id=source_id, account_since=self._config.get("account_since")), cursors, timeout=30)
                if self._cancel_requested():
                    raise InterruptedError("collection cancelled")
                changed = bool(self._store.import_frames(result, source_id, meta_key="cursor:" + source_id)) or changed
                changed = bool(self._store.update_session_titles(result.get("titles") or {})) or changed
                diagnostics = result.get("diagnostics") or {}
                if (result.get("errors") or result.get("partial_files") or result.get("deferred_files")
                        or any(diagnostics.get(key) for key in ("ambiguous_cumulative", "missing_baseline", "malformed_lines"))):
                    raise RuntimeError("remote history incomplete")
                changed = self._store.set_source_status(source_id, name=source.get("name") or source.get("host"),
                                                        status="ok", last_scan_at=utc_now(), error=None) or changed
            except InterruptedError:
                return self._store.set_source_status(source_id, status="cancelled", error="采集已暂停，等待后续同步", last_scan_at=utc_now()) or changed
            except Exception as exc:
                logger.warning("SSH 用量同步失败: %s", type(exc).__name__)
                changed = self._store.set_source_status(source_id, name=source.get("name") or source.get("host"),
                                                        status="error", error="连接或采集失败，请检查主机、认证和 Python 配置", last_scan_at=utc_now()) or changed
        return changed

    def _publish(self) -> None:
        self._report_initial_loading("正在汇总用量数据")
        from codexio.available_models import load_available_models
        from codexio.usage_queries import UsageQueries
        sources = [source for source in self._store.sources() if not str(source.get("id", source.get("source_id", ""))).startswith("lan:")]
        queries = UsageQueries(self._store.path)
        generation = queries.rebuild(self._catalog, sources)
        if self._quota_applicable and self._rolling is not None:
            self._rolling.refresh_prices(self._catalog.price_version, queries)
            self._rolling.process_due(time.time(), queries)
        if self._widget_only:
            now = datetime.now().astimezone()
            today = queries.dashboard_summary(start=now.replace(hour=0, minute=0, second=0, microsecond=0), end=now)
            widget_data = {"widget_request": queries.widget_request(), "menu_bar_today": today}
            signature = json.dumps(widget_data, sort_keys=True, ensure_ascii=False, default=str)
            if signature != self._last_widget_signature:
                self._last_widget_signature = signature
                self.data_changed.emit(widget_data)
            self._finish_initial_loading("小组件数据已加载")
            return
        active = self._active_sources()
        by_source = {s.get("id", s.get("source_id")): s for s in sources}
        complete = bool(active) and not self._cancel_requested() and all(
            by_source.get(key, {}).get("status") in ("ok", "ready", "indexed")
            and not by_source.get(key, {}).get("cancelled") for key in active)
        # Calendar/maturity boundaries still refresh without new records. Heavy
        # pricing and request aggregation only change with their durable inputs.
        now = datetime.now().astimezone()
        summary_key = generation, now.date(), now.utcoffset()
        next_summary_at = getattr(self, "_next_summary_at", None)
        if (getattr(self, "_summary_key", None) != summary_key
                or now < getattr(self, "_summary_at", now)
                or next_summary_at is not None and now >= next_summary_at):
            self._summaries = queries.summaries(now)
            self._menu_bar_today = queries.dashboard_summary(start=now.replace(hour=0, minute=0, second=0, microsecond=0), end=now)
            self._summary_key = summary_key
            self._summary_at = now
            self._next_summary_at = parse_time(queries.next_record_at(now))
        if self._quota_applicable and self._estimates_visible:
            self._estimates = self._rolling.rows()
        elif not self._quota_applicable:
            self._estimates = []
        if self._mock:
            model_catalog = {"models": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"], "status": "ok"}
        else:
            roots = self._config.get("codex_roots", [])
            model_catalog = load_available_models(roots)
            key = "available_models:" + hashlib.sha256(json.dumps(roots, sort_keys=True).encode()).hexdigest()
            if model_catalog.get("status") == "unavailable":
                previous = self._store.get_meta(key)
                if isinstance(previous, dict) and previous.get("models"):
                    model_catalog = dict(previous, status="stale")
            elif model_catalog.get("status") == "ok" or model_catalog.get("models"):
                self._store.set_meta(key, model_catalog)
        data = {
            "query_path": str(self._store.path), "query_generation": generation,
            "summaries": self._summaries, "latest_request": queries.latest_request(),
            "widget_request": queries.widget_request(), "filters": queries.filters(),
            "menu_bar_today": self._menu_bar_today, "today_date": now.date().isoformat(),
            "prices": self._catalog.rows(),
            "standard_prices": self._catalog.standard_rows(),
            "pricing_status": copy.deepcopy(self._catalog.status), "sources": sources, "weekly_estimates": self._estimates,
            "estimate_identity": ({key: self._rolling.latest[key] for key in
                                   ("account_key", "plan_type", "reset_at", "sole_codex_pool")}
                                  if self._quota_applicable and self._rolling is not None
                                  and self._rolling.latest is not None else None),
            "calibration_sources": sorted(active), "sources_complete": complete,
            "available_models": model_catalog.get("models", []), "model_catalog_status": model_catalog,
            "updated_at": utc_now(), "scan_status": self._scan_status,
        }
        comparable = dict(data, updated_at=None)
        source_fields = ("id", "source_id", "name", "status", "message", "error", "cancelled")
        comparable["sources"] = [{key: source.get(key) for key in source_fields} for source in sources]
        signature = hashlib.sha256(json.dumps(comparable, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
        if signature != self._last_payload_signature:
            data["content_changed"] = True
            self._last_payload_signature = signature
            self.data_changed.emit(data)
        if self._scan_status != self._last_progress:
            self._last_progress = self._scan_status
            self.progress_changed.emit(self._scan_status)
        if not self._cancel_requested():
            # An empty history is also a completed first load. Config changes
            # interrupt scans, so wait for the new configuration's first pass.
            self._finish_initial_loading("用量数据已加载")

    def _seed_mock(self) -> None:
        import random
        rng = random.Random(72)
        now = datetime.now(timezone.utc)
        self._config["account_since"] = (now - timedelta(days=35)).isoformat()
        self._store.clear_index()
        records = []
        for i in range(420):
            date = now - timedelta(minutes=i * 93 + 2)
            inp = rng.randrange(12000, 340000)
            cached = int(inp * 0.82)
            written = int(inp * 0.1)
            out = rng.randrange(140, 6000)
            records.append({
                "id": "mock:%s" % i, "response_id": "resp_preview_%04d" % i,
                "session_id": "019demo0-728a-7000-a103-%012d" % (i // 4), "turn_id": "turn-%d" % (i // 2),
                "timestamp": date.isoformat(), "duration_ms": 1200 + (i % 17) * 3100,
                "model": ("gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")[i % 4],
                "service_tier": "default" if i % 3 else "priority", "provider": "openai", "limit_id": "codex",
                "input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": written,
                "output_tokens": out, "reasoning_output_tokens": out // 2, "total_tokens": inp + out,
                "quality": "response", "source_id": "local", "source_name": "本机（模拟）",
                "session_title": "Codex 用量统计与桌面交互优化",
                "prompt_preview": "请增加每次请求的 Token 和等价美元详情，在 Session ID 旁边加上感叹号。鼠标悬停时显示会话标题和用户输入的两行预览，长内容在第二行末尾省略。",
                "output_preview": "已更新请求日志，加入每次请求的 Token、价格与 Fast 状态。会话预览分别显示用户消息和模型输出，保留必要上下文，长文本在第三行末尾省略。",
            })
        self._store.upsert_records(records, "local")
        turns = {}
        for record in records:
            key = (record["session_id"], record["turn_id"])
            group = turns.setdefault(key, {"id": "turn:{}:{}".format(*key), "session_id": key[0], "turn_id": key[1],
                "verified": True, "has_usage": True, "started_inferred": False,
                "started_at": record["timestamp"], "ended_at": record["timestamp"], "status": "completed",
                "prompt_preview": record["prompt_preview"], "output_preview": record["output_preview"]})
            group["started_at"] = min(group["started_at"], record["timestamp"])
            group["ended_at"] = max(group["ended_at"], record["timestamp"])
        if turns:
            latest = max(turns.values(), key=lambda row: row["started_at"])
            latest.update(status="running", ended_at="", latest_output_preview=latest["output_preview"], output_preview="")
        self._store.upsert_turns(turns.values(), "local")
        self._store.set_source_status("local", name="本机（模拟）", status="ok", last_scan_at=utc_now())
        self._scan_status = "模拟预览 · 未连接真实 Codex"
