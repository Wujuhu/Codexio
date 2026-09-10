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

from codexio.analytics_config import utc_now
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

    def __init__(self, config: dict, mock: bool = False, directory: Path | None = None) -> None:
        super().__init__()
        self._config = copy.deepcopy(config)
        self._mock = mock
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

    def set_price_override(self, model: str, rates: dict | None) -> None:
        self._commands.put(("override", (model, rates)))
        self._wake.set()

    def rescan(self) -> None:
        self._commands.put(("rescan", None))
        self._wake.set()

    def add_snapshot(self, snapshot, source_id: str = "local-quota") -> None:
        if self._mock:
            return
        now = utc_now()
        observations = []
        for key, bucket in (snapshot.by_limit or {snapshot.limit_id or "codex": snapshot}).items():
            for window in (bucket.primary, bucket.secondary):
                if window is None or window.used_percent is None:
                    continue
                observations.append({
                    "id": "live:" + hashlib.sha256((now + source_id + key + str(window.window_duration_mins)).encode()).hexdigest(),
                    "timestamp": now, "used_percent": window.used_percent,
                    "window_minutes": window.window_duration_mins, "resets_at": window.resets_at,
                    "plan_type": bucket.plan_type or snapshot.plan_type, "limit_id": key,
                    "source_id": source_id, "account_key": "current",
                    "observation_source": "app-server",
                })
        self._commands.put(("observations", (source_id, observations)))
        self._wake.set()

    def stop(self, timeout_ms: int = 45000) -> bool:
        self._stop_event.set()
        self._wake.set()
        if self.isRunning():
            return self.wait(timeout_ms)
        return True

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
            self._directory.mkdir(parents=True, exist_ok=True)
            self._store = UsageStore(self._directory / ("usage_mock.sqlite" if self._mock else "usage.sqlite"))
            self._catalog = PricingCatalog(self._directory / ("mock_prices" if self._mock else "prices"))
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
        if self._mock:
            self._report_initial_loading("正在准备模拟用量数据")
            self._seed_mock()
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
                    if command == "config":
                        if self._mock:
                            # UI preferences carry the real enrollment timestamp;
                            # changing theme must not unassign the synthetic history.
                            value["account_since"] = self._config["account_since"]
                        self._config = value
                        self._config_changed.clear()
                        next_remote = next_sync = 0
                    elif command == "observations":
                        self._store.upsert_observations(value[1], value[0])
                    elif command == "rescan":
                        self._store.clear_index()
                        if self._mock:
                            self._seed_mock()
                    elif command == "override":
                        self._catalog.set_override(*value)
                    elif command == "sync":
                        force_sync = True
                    elif command == "refresh":
                        next_remote = 0
                    dirty = True
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
                    except Exception:
                        logger.exception("本地计量扫描失败")
                        self._store.set_source_status(source_id, status="error", name="本机", error="日志扫描失败", last_scan_at=utc_now())
                        self._scan_status = "部分来源读取失败"
                        dirty = True
                if time.monotonic() >= next_remote:
                    if any(s.get("enabled", True) for kind in ("ssh",)
                           for s in self._config.get(kind + "_sources", [])):
                        self._report_initial_loading("正在同步远程用量")
                    self._collect_remote()
                    next_remote = time.monotonic() + 60
                    dirty = True
            if self._stop_event.is_set():
                break
            if dirty or time.monotonic() >= next_publish:
                self._publish()
                dirty = False
                next_publish = time.monotonic() + 60
            if not self._mock and not self._cancel_requested() and (force_sync or (self._config.get("auto_sync_prices") and time.monotonic() >= next_sync)):
                self.progress_changed.emit("正在同步模型价格")
                try:
                    self._catalog.sync(force=force_sync)
                except Exception:
                    logger.exception("模型价格同步失败，沿用已缓存价格")
                next_sync = time.monotonic() + 3600
                dirty = True
            self._wake.wait(0.1 if dirty else 5.0)

    def _collect_remote(self) -> None:
        from codexio.remote_collector import collect_ssh
        for source in self._config.get("ssh_sources", []):
            if not source.get("enabled", True) or self._stop_event.is_set():
                continue
            if self._config_changed.is_set():
                return
            source_id = _remote_source_id("ssh", source)
            try:
                cursors = self._store.get_meta("cursor:" + source_id) or {}
                result = collect_ssh(dict(source, id=source_id, account_since=self._config.get("account_since")), cursors, timeout=30)
                if self._cancel_requested():
                    raise InterruptedError("collection cancelled")
                self._store.import_frames(result, source_id, meta_key="cursor:" + source_id)
                self._store.update_session_titles(result.get("titles") or {})
                diagnostics = result.get("diagnostics") or {}
                if (result.get("errors") or result.get("partial_files") or result.get("deferred_files")
                        or any(diagnostics.get(key) for key in ("ambiguous_cumulative", "missing_baseline", "malformed_lines"))):
                    raise RuntimeError("remote history incomplete")
                self._store.set_source_status(source_id, name=source.get("name") or source.get("host"),
                                              status="ok", last_scan_at=utc_now(), error=None)
            except InterruptedError:
                self._store.set_source_status(source_id, status="cancelled", error="采集已暂停，等待后续同步", last_scan_at=utc_now())
                return
            except Exception as exc:
                logger.warning("SSH 用量同步失败: %s", type(exc).__name__)
                self._store.set_source_status(source_id, name=source.get("name") or source.get("host"),
                                              status="error", error="连接或采集失败，请检查主机、认证和 Python 配置", last_scan_at=utc_now())

    def _publish(self) -> None:
        self._report_initial_loading("正在汇总用量数据")
        from codexio.available_models import load_available_models
        from codexio.usage_queries import UsageQueries
        sources = [source for source in self._store.sources() if not str(source.get("id", source.get("source_id", ""))).startswith("lan:")]
        queries = UsageQueries(self._store.path)
        generation = queries.rebuild(self._catalog, sources)
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
            self._summary_key = summary_key
            self._summary_at = now
            self._next_summary_at = parse_time(queries.next_record_at(now))
        self._estimates = queries.weekly_estimates(active, self._config["account_since"], sources_complete=complete,
                                                   assignments=self._config.get("history_assignments", []), now=now)
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
            "summaries": self._summaries, "latest_request": queries.latest_request(), "filters": queries.filters(),
            "prices": self._catalog.rows(),
            "standard_prices": self._catalog.standard_rows(),
            "pricing_status": copy.deepcopy(self._catalog.status), "sources": sources, "weekly_estimates": self._estimates,
            "calibration_sources": sorted(active), "sources_complete": complete,
            "available_models": model_catalog.get("models", []), "model_catalog_status": model_catalog,
            "updated_at": utc_now(), "scan_status": self._scan_status,
        }
        self.data_changed.emit(data)
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
        reset = int((now + timedelta(days=3)).timestamp())
        self._store.upsert_observations([
            {"id": "mock-q-%d" % i, "timestamp": (now - timedelta(hours=48 - i * 6)).isoformat(),
             "used_percent": 10 + i * 4, "window_minutes": 10080, "resets_at": reset,
             "plan_type": "pro", "limit_id": "codex", "account_key": "current", "source_id": "local"}
            for i in range(8)
        ], "local")
        self._scan_status = "模拟预览 · 未连接真实 Codex"
