from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import getproxies

from codexio.logging_setup import get_logger, redact_text
from codexio import __version__
from codexio.codex_discovery import find_codex

CLIENT_NAME = "codexio"
CLIENT_TITLE = "Codexio"
CLIENT_VERSION = __version__
DEFAULT_REQUEST_TIMEOUT = 20.0
INITIALIZE_TIMEOUT = 15.0
RATE_LIMITS_METHOD = "account/rateLimits/read"
RATE_LIMITS_UPDATED = "account/rateLimits/updated"

logger = get_logger("app_server")


class CodexNotFoundError(RuntimeError):
    pass


class AppServerError(RuntimeError):
    def __init__(self, message: str, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MessageKind(str, Enum):
    RESPONSE = "response"
    ERROR = "error"
    NOTIFICATION = "notification"
    REQUEST = "request"


@dataclass(frozen=True)
class JsonRpcMessage:
    kind: MessageKind
    raw: dict
    id: Any = None
    method: Optional[str] = None
    params: Any = None
    result: Any = None
    error: Optional[dict] = None


def parse_jsonrpc_line(line: str) -> JsonRpcMessage:
    text = line.strip()
    if not text:
        raise ValueError("empty JSON-RPC line")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON: %s" % exc) from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON-RPC message must be an object")
    method = payload.get("method")
    has_id = "id" in payload
    if method and has_id:
        return JsonRpcMessage(
            kind=MessageKind.REQUEST,
            raw=payload,
            id=payload.get("id"),
            method=str(method),
            params=payload.get("params"),
        )
    if method and not has_id:
        return JsonRpcMessage(
            kind=MessageKind.NOTIFICATION,
            raw=payload,
            method=str(method),
            params=payload.get("params"),
        )
    if has_id and "error" in payload:
        error = payload.get("error")
        return JsonRpcMessage(
            kind=MessageKind.ERROR,
            raw=payload,
            id=payload.get("id"),
            error=error if isinstance(error, dict) else {"message": str(error)},
        )
    if has_id:
        return JsonRpcMessage(
            kind=MessageKind.RESPONSE,
            raw=payload,
            id=payload.get("id"),
            result=payload.get("result"),
        )
    raise ValueError("unrecognized JSON-RPC message")


def discover_codex_executable(explicit_path: Optional[str] = None, excluded=()) -> Path:
    resolved = find_codex(explicit_path, excluded)
    if resolved is not None:
        return resolved
    raise CodexNotFoundError("未找到可用的 Codex 组件，请安装或更新 Codex App 并登录")


class _WindowsJob:
    def __init__(self) -> None:
        self._handle = None
        if sys.platform != "win32":
            return
        try:
            self._handle = _create_kill_on_close_job()
        except OSError as exc:
            logger.warning("无法创建 Job Object，将回退到进程树结束: %s", exc)

    def assign(self, pid: int) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        try:
            _assign_pid_to_job(self._handle, pid)
        except OSError as exc:
            logger.warning("无法将 Codex 进程加入 Job Object: %s", exc)

    def close(self) -> None:
        if self._handle is None or sys.platform != "win32":
            return
        handle = self._handle
        self._handle = None
        import ctypes

        ctypes.windll.kernel32.CloseHandle(handle)


class AppServerClient:
    def __init__(
        self,
        executable: Optional[Path] = None,
        on_notification: Optional[Callable[[JsonRpcMessage], None]] = None,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self._executable = executable
        self._on_notification = on_notification
        self._request_timeout = request_timeout
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._job = _WindowsJob()
        self._reader_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending: Dict[int, Queue] = {}
        self._next_id = 1
        self._closed = False
        self._initialized = False

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def start(self, executable: Optional[Path] = None) -> None:
        with self._lock:
            if self.running:
                return
            self._closed = False
            self._initialized = False
            exe = executable or self._executable or discover_codex_executable()
            self._executable = exe
            env = _child_env()
            env.setdefault("RUST_LOG", "error")
            startupinfo = None
            creationflags = 0
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            logger.info("启动 Codex app-server: %s", exe)
            self._proc = subprocess.Popen(
                [str(exe), "app-server", "--listen", "stdio://"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path.home()),
                env=env,
                bufsize=0,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
            self._job.assign(self._proc.pid)
            self._reader_thread = threading.Thread(target=self._read_stdout, name="app-server-stdout", daemon=True)
            self._stderr_thread = threading.Thread(target=self._read_stderr, name="app-server-stderr", daemon=True)
            self._reader_thread.start()
            self._stderr_thread.start()
        self.initialize()

    def initialize(self) -> dict:
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": CLIENT_NAME,
                    "title": CLIENT_TITLE,
                    "version": CLIENT_VERSION,
                },
                "capabilities": {
                    "optOutNotificationMethods": [
                        "thread/started",
                        "thread/archived",
                        "item/agentMessage/delta",
                        "item/reasoning/delta",
                        "item/commandExecution/outputDelta",
                        "turn/started",
                    ]
                },
            },
            timeout=INITIALIZE_TIMEOUT,
        )
        self.notify("initialized", {})
        self._initialized = True
        return result if isinstance(result, dict) else {}

    def read_rate_limits(self) -> dict:
        result = self.request(RATE_LIMITS_METHOD, timeout=self._request_timeout)
        if not isinstance(result, dict):
            raise AppServerError("account/rateLimits/read 返回了无效结果")
        return result

    def request(self, method: str, params: Any = None, timeout: Optional[float] = None) -> Any:
        message_id = self._next_request_id()
        queue: Queue = Queue(maxsize=1)
        with self._lock:
            if not self.running:
                raise AppServerError("Codex app-server 未运行")
            self._pending[message_id] = queue
        payload: Dict[str, Any] = {"method": method, "id": message_id}
        if params is not None:
            payload["params"] = params
        self._write(payload)
        try:
            message = queue.get(timeout=timeout or self._request_timeout)
        except Empty:
            with self._lock:
                self._pending.pop(message_id, None)
            raise AppServerError("请求超时: %s" % method)
        if message.kind == MessageKind.ERROR:
            error = message.error or {}
            raise AppServerError(
                str(error.get("message") or "JSON-RPC error"),
                code=_safe_int(error.get("code")),
                data=error.get("data"),
            )
        return message.result

    def notify(self, method: str, params: Any = None) -> None:
        payload: Dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            proc = self._proc
            self._proc = None
            pending = list(self._pending.values())
            self._pending.clear()
        for queue in pending:
            queue.put(
                JsonRpcMessage(
                    kind=MessageKind.ERROR,
                    raw={},
                    error={"message": "app-server closed"},
                )
            )
        if proc is not None:
            _terminate_process(proc)
        self._job.close()
        self._initialized = False

    def _next_request_id(self) -> int:
        with self._lock:
            message_id = self._next_id
            self._next_id += 1
            return message_id

    def _write(self, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise AppServerError("Codex app-server 标准输入不可用")
        data = line.encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except OSError as exc:
                raise AppServerError("写入 app-server 失败: %s" % exc) from exc

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw in iter(proc.stdout.readline, b""):
                if self._closed:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    message = parse_jsonrpc_line(line)
                except ValueError:
                    logger.warning("忽略无效 JSON-RPC 行")
                    continue
                self._dispatch(message)
        finally:
            self._fail_pending("Codex app-server 已退出")

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, b""):
            if self._closed:
                break
            text = redact_text(raw.decode("utf-8", errors="replace").rstrip())
            if text:
                logger.info("app-server: %s", text)

    def _dispatch(self, message: JsonRpcMessage) -> None:
        if message.kind in (MessageKind.RESPONSE, MessageKind.ERROR):
            with self._lock:
                queue = self._pending.pop(message.id, None)
            if queue is not None:
                queue.put(message)
            return
        if message.kind == MessageKind.REQUEST:
            self._reject_server_request(message)
            return
        callback = self._on_notification
        if callback is not None:
            try:
                callback(message)
            except Exception:
                logger.exception("处理 app-server 通知失败")

    def _reject_server_request(self, message: JsonRpcMessage) -> None:
        logger.info("忽略服务端请求: %s", message.method)
        try:
            self._write(
                {
                    "id": message.id,
                    "error": {"code": -32601, "message": "Method not supported"},
                }
            )
        except AppServerError:
            logger.warning("无法回复服务端请求 %s", message.method)

    def _fail_pending(self, reason: str) -> None:
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        error = JsonRpcMessage(kind=MessageKind.ERROR, raw={}, error={"message": reason})
        for queue in pending:
            queue.put(error)


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
        return
    except Exception:
        pass
    if sys.platform == "win32" and proc.pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=3)
            return
        except Exception:
            pass
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        logger.warning("结束 Codex 进程失败 pid=%s", proc.pid)


def _child_env() -> dict:
    env = os.environ.copy()
    if not _local_proxy_unreachable():
        return env
    logger.warning("本机代理不可用，已改为直连启动 Codex")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return env


def _local_proxy_unreachable() -> bool:
    proxies = getproxies()
    raw = proxies.get("https") or proxies.get("http")
    if not raw:
        return False
    parsed = urlparse(raw)
    host = parsed.hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = socket.socket()
    sock.settimeout(0.4)
    try:
        sock.connect((host, port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _create_kill_on_close_job():
    import ctypes
    from ctypes import wintypes

    job_object_extended_limit_information = 9
    job_object_limit_kill_on_close = 0x2000

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError("CreateJobObjectW failed")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = job_object_limit_kill_on_close
    if not kernel32.SetInformationJobObject(
        handle,
        job_object_extended_limit_information,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(handle)
        raise OSError("SetInformationJobObject failed")
    return handle


def _assign_pid_to_job(job_handle, pid: int) -> None:
    import ctypes

    process_set_quota = 0x0100
    process_terminate = 0x0001
    kernel32 = ctypes.windll.kernel32
    process = kernel32.OpenProcess(process_set_quota | process_terminate, False, pid)
    if not process:
        raise OSError("OpenProcess failed")
    try:
        if not kernel32.AssignProcessToJobObject(job_handle, process):
            raise OSError("AssignProcessToJobObject failed")
    finally:
        kernel32.CloseHandle(process)
