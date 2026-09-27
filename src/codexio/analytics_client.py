"""Account-bound, read-only allowance reports from the official Codex contracts."""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from codexio.i18n import tr


def _keyring(name):
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", "Codex Auth", "-a", name, "-w"],
                                capture_output=True, timeout=5, check=False)
        return result.stdout if result.returncode == 0 else b""
    if sys.platform == "win32":
        from ctypes import wintypes
        class Credential(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
        pointer = ctypes.POINTER(Credential)()
        advapi = ctypes.windll.advapi32
        advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        advapi.CredFree.restype = None
        for target in ("Codex Auth:" + name, name + ":Codex Auth", "Codex Auth/" + name):
            if advapi.CredReadW(target, 1, 0, ctypes.byref(pointer)):
                try:
                    return ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize)
                finally:
                    advapi.CredFree(pointer)
    return b""


def credentials(root=None):
    root = Path(root or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    try:
        value = json.loads((root / "auth.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        secret = _keyring("cli|" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16])
        value = {}
        for encoding in ("utf-8", "utf-16-le"):
            try:
                value = json.loads(secret.decode(encoding)); break
            except (UnicodeError, ValueError):
                pass
    tokens = value.get("tokens") or {}
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(tr("需要本机 Codex 的 ChatGPT 登录状态"))
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, IndexError, UnicodeError):
        raise RuntimeError(tr("无法确认当前账户")) from None
    fields = claims.get("https://api.openai.com/auth") or {}
    account = tokens.get("account_id") or fields.get("chatgpt_account_id")
    user = claims.get("sub") or fields.get("chatgpt_user_id")
    if not isinstance(account, str) or not account or not isinstance(user, str):
        raise RuntimeError(tr("无法确认当前账户"))
    return token, (account, user)


class _SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before, after = urlsplit(req.full_url), urlsplit(newurl)
        if (before.scheme, before.netloc) != (after.scheme, after.netloc):
            raise RuntimeError(tr("服务重定向被拒绝"))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class AccountAnalyticsClient:
    def __init__(self, root=None):
        self.root = root
        self.identity = None
        self.retry_at = 0.0

    def _request(self, route, payload=None):
        if route not in ("usage/plan_limit_history?days=7", "usage/thread_usage/query_v2"):
            raise ValueError("Unsupported analytics route")
        if time.monotonic() < self.retry_at:
            raise RuntimeError(tr("请求频繁，请稍后刷新"))
        token, identity = credentials(self.root)
        if self.identity is not None and self.identity != identity:
            raise RuntimeError(tr("账户已变更，请重新刷新"))
        self.identity = identity
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
        request = Request("https://chatgpt.com/backend-api/wham/" + route, data=body,
                          headers={"Authorization": "Bearer " + token, "ChatGPT-Account-Id": identity[0],
                                   "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Codexio/0.3.1"})
        try:
            with build_opener(_SameOriginRedirect()).open(request, timeout=15) as response:
                data = response.read(16_000_001)
        except HTTPError as error:
            messages = {401: tr("请重新登录 Codex"), 403: tr("此账户没有这项数据的访问权限"), 404: tr("当前账户尚未提供这项明细"), 429: tr("请求频繁，请稍后刷新")}
            if error.code == 429:
                try:
                    delay = min(3600, max(60, float(error.headers.get("Retry-After", "60"))))
                except ValueError:
                    delay = 60
                self.retry_at = time.monotonic() + delay
            raise RuntimeError(tr(messages.get(error.code, "服务暂不可用"))) from None
        if len(data) > 16_000_000 or credentials(self.root)[1] != identity:
            raise RuntimeError(tr("账户或统计数据已变化，请重新刷新"))
        result = json.loads(data.decode("utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError(tr("服务返回了无效统计数据"))
        return result

    def plan_history(self):
        return self._request("usage/plan_limit_history?days=7")

    def thread_usage(self, threads, stopped=lambda: False):
        result, batch, ids, all_ids, as_of = [], [], set(), set(), None
        def flush():
            nonlocal batch, ids, as_of
            if stopped():
                raise RuntimeError(tr("已取消读取"))
            response = self._request("usage/thread_usage/query_v2", {"threads": batch})
            expected, received = {row["thread_id"] for row in batch}, set()
            for row in response.get("threads", []):
                if row.get("thread_id") not in expected or row["thread_id"] in received:
                    raise RuntimeError(tr("服务返回了无效统计数据"))
                received.add(row["thread_id"])
                for part in [row, *row.get("groups", [])]:
                    for key in ("weekly_limit_percent", "five_hour_limit_percent"):
                        value = part.get(key)
                        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                            raise RuntimeError(tr("服务返回了无效统计数据"))
                result.append(row)
            for missing in expected - received:
                result.append(dict(thread_id=missing, data_status="unavailable", groups=[]))
            as_of = response.get("data_as_of")
            batch, ids = [], set()
        for raw in threads:
            row = {key: raw.get(key) for key in ("thread_id", "created_at", "descendant_thread_ids")}
            values = [row["thread_id"], *(row["descendant_thread_ids"] or [])]
            if any(not isinstance(value, str) or not value.strip() or len(value) > 512 for value in values) or len(set(values)) != len(values) or set(values) & all_ids or len(values) > 1000:
                result.append(dict(thread_id=row["thread_id"], data_status="unavailable", groups=[]))
                continue
            if batch and (len(batch) >= 100 or len(ids) + len(values) > 1000):
                flush()
            batch.append(row); ids.update(values); all_ids.update(values)
        if batch:
            flush()
        return dict(threads=result, data_as_of=as_of)


from PySide6.QtCore import QThread, Signal


class AccountReportsWorker(QThread):
    reports_changed = Signal(object)

    def __init__(self, mock=False):
        super().__init__()
        self.mock = mock
        self.commands = queue.Queue()
        self.stopped = threading.Event()
        self.generation = 0
        self.last_read = 0.0
        self.retry_at = 0.0
        self.cached_plan, self.cached_chats = {}, {}

    def request_reports(self, threads, force=False):
        if self.mock:
            self.reports_changed.emit(dict(plan={}, chats={}, error=tr("模拟模式不读取账户统计")))
            return
        if time.monotonic() < self.retry_at or (not force and time.monotonic() - self.last_read < 60):
            return
        self.last_read = time.monotonic()
        self.commands.put((self.generation, list(threads)))

    def invalidate(self):
        self.generation += 1
        self.cached_plan, self.cached_chats = {}, {}
        self.last_read = 0
        self.reports_changed.emit(dict(plan={}, chats={}, error=""))

    def stop(self):
        self.stopped.set(); self.commands.put(None)
        self.wait(35000)

    def run(self):
        while not self.stopped.is_set():
            command = self.commands.get()
            if command is None:
                return
            generation, threads = command
            if generation != self.generation:
                continue
            client, plan, chats, errors = AccountAnalyticsClient(), {}, {}, []
            client.retry_at = self.retry_at
            try:
                plan = client.plan_history()
            except Exception as error:
                errors.append(str(error))
            try:
                if not self.stopped.is_set():
                    chats = client.thread_usage(threads, self.stopped.is_set)
            except Exception as error:
                errors.append(str(error))
            self.retry_at = client.retry_at
            if generation == self.generation and not self.stopped.is_set():
                if plan:
                    self.cached_plan = plan
                if chats:
                    self.cached_chats = chats
                account_key = hashlib.sha256(json.dumps(["codexio-week-v1", *client.identity], ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest() if client.identity else ""
                self.reports_changed.emit(dict(plan=self.cached_plan, chats=self.cached_chats, account_key=account_key, error=" · ".join(dict.fromkeys(errors))))
