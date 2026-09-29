"""Windows host for the existing read-only iPhone sync protocol.

The main process owns the TLS/Bonjour listener. There is no independent
collector, app-server client, remote command endpoint or background service.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import socket
import sqlite3
import ssl
import struct
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

from PySide6.QtCore import QObject, Signal
from zeroconf import ServiceInfo, Zeroconf
from tzlocal import get_localzone_name

from codexio.settings import data_dir

ORIGIN = "https://codexio-sync.503948883.workers.dev"
SERVICE = "_codexio._tcp.local."
MAX_MESSAGE = 262_144


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(value: bytes, *, decode=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Device credentials require Windows DPAPI")
    buffer = ctypes.create_string_buffer(value)
    source = _Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt.CryptUnprotectData if decode else crypt.CryptProtectData
    operation.restype = wintypes.BOOL
    if decode:
        success = operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        success = operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not success:
        raise OSError(ctypes.get_last_error(), "Could not access protected mobile credentials")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(ctypes.cast(target.pbData, ctypes.c_void_p))


def _identity() -> dict:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Codexio Local")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
                   .not_valid_after(now+timedelta(days=3650))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .sign(key, hashes.SHA256()))
    cert = certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode("ascii")
    pin = hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
    return dict(id=str(uuid.uuid4()).upper(), writer=_secret(), cloud_enabled=False,
                certificate=cert, private_key=private, pin=pin, readers=[], revoked=[], notes={})


def _tls_context(host: dict) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    files = []
    try:
        for value in (host["certificate"], host["private_key"]):
            with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", suffix=".pem", delete=False) as output:
                output.write(value)
                files.append(Path(output.name))
        context.load_cert_chain(str(files[0]), str(files[1]))
    finally:
        for path in files:
            path.unlink(missing_ok=True)
    return context


def _read_exact(stream, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        value = stream.recv(size-len(chunks))
        if not value:
            raise EOFError()
        chunks.extend(value)
    return bytes(chunks)


def _receive(stream) -> dict:
    first, second = _read_exact(stream, 2)
    opcode = first & 0x0f
    size = second & 0x7f
    if size == 126:
        size = struct.unpack("!H", _read_exact(stream, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _read_exact(stream, 8))[0]
    if size > MAX_MESSAGE or not first & 0x80:
        raise ValueError("Invalid sync frame")
    mask = _read_exact(stream, 4) if second & 0x80 else None
    payload = _read_exact(stream, size)
    if mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if opcode == 8:
        raise EOFError()
    if opcode not in (1, 2):
        return {}
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Invalid sync message")
    return value


def _send(stream, value: dict) -> None:
    payload = _json(value)
    if len(payload) > MAX_MESSAGE:
        raise ValueError("Sync response exceeds size limit")
    head = b"\x81" + (bytes((len(payload),)) if len(payload) < 126 else
                       b"\x7e"+struct.pack("!H", len(payload)) if len(payload) < 65536 else
                       b"\x7f"+struct.pack("!Q", len(payload)))
    stream.sendall(head+payload)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _cloud(path: str, method: str, token: str, value=None) -> dict:
    body = None if value is None else _json(value)
    request = Request(ORIGIN + path, data=body, method=method,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                               "User-Agent": "Codexio/0.3.3"})
    try:
        with build_opener(HTTPSHandler(), _NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(MAX_MESSAGE+1)
            if len(raw) > MAX_MESSAGE:
                raise ValueError("Cloud response exceeds size limit")
            return json.loads(raw.decode("utf-8")) if raw else {}
    except HTTPError as error:
        raise RuntimeError("Cloud sync HTTP %d" % error.code) from error


class MobileWindowsHost(QObject):
    changed = Signal()
    error = Signal(str)

    def __init__(self, parent=None, *, config_provider=lambda: {}, mock=False):
        super().__init__(parent)
        self.mock = mock
        self.config_provider = config_provider
        self.directory = data_dir()
        self.path = self.directory / "mobile-host-win.dat"
        self.enabled = False
        self.cloud_enabled = False
        self.status = "尚未开启"
        self.name = socket.gethostname()[:40] or "Windows PC"
        self.host = None
        self.ticket = None
        self.pending = None
        self.qr = ""
        self.datasets = {}
        self._server = None
        self._bonjour = None
        self._service = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._generation = None
        self._detail_generation = None
        self._history_generation = None
        self._project_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Codexio-mobile-project")
        self._last_error = None
        self._latest_data = None
        self._latest_quota = None
        self._query_path = None
        self._request_ids = {}
        self._details = OrderedDict()
        self._detail_bytes = 0
        self._cloud_details = None
        self._next_capability = 0.0
        self._sent_details = {}
        self._clients = threading.BoundedSemaphore(8)
        self._cloud_sent = {}
        self._cloud_readers = set()
        self._sending = False
        self._upload_again = False
        self._starting = False
        self._lifecycle = 0
        self._closed = False
        if not mock:
            try:
                self.host = json.loads(_dpapi(self.path.read_bytes(), decode=True))
                self.cloud_enabled = bool(self.host.get("cloud_enabled"))
                self.name = str(self.host.get("name") or self.name)[:40]
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                self.status = str(exc)

    @property
    def readers(self) -> list[dict]:
        return list((self.host or {}).get("readers") or [])

    def _save(self):
        if self.host is None or self.mock:
            return
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(_dpapi(_json(self.host)))
        temporary.replace(self.path)

    def _announce(self, message: str):
        self.status = message
        self.changed.emit()

    def start(self):
        if self.enabled or self._starting or self.mock or self._closed:
            return
        self._starting = True
        self._lifecycle += 1
        generation = self._lifecycle
        self._stop.clear()
        threading.Thread(target=self._start_async, args=(generation,), name="Codexio-mobile-start", daemon=True).start()

    def _start_async(self, generation: int):
        server = None
        bonjour = None
        service = None
        try:
            if self.host is None:
                self.host = _identity()
                self.host["name"] = self.name
                self._save()
            context = _tls_context(self.host)
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", 0))
            server.listen(8)
            server.settimeout(1)
            ip = self._local_ip()
            if ip is None:
                server.close()
                raise OSError("No local IPv4 address is available")
            bonjour = Zeroconf()
            service = ServiceInfo(SERVICE, self.host["id"] + "." + SERVICE,
                                  addresses=[socket.inet_aton(ip)], port=server.getsockname()[1],
                                  properties={"protocol": "1"})
            bonjour.register_service(service)
            if generation != self._lifecycle or self._stop.is_set():
                bonjour.unregister_service(service)
                bonjour.close(); server.close()
                return
            self._server, self._bonjour, self._service = server, bonjour, service
            self.enabled = True
            self._announce("局域网服务已开启")
            threading.Thread(target=self._serve, args=(context, server), name="Codexio-mobile-local", daemon=True).start()
            threading.Thread(target=self._retry_loop, args=(generation,), name="Codexio-mobile-cloud-retry", daemon=True).start()
            self.update(self._latest_data, self._latest_quota)
        except Exception as exc:
            if bonjour is not None:
                if service is not None:
                    try:
                        bonjour.unregister_service(service)
                    except (OSError, ValueError):
                        pass
                bonjour.close()
            if server is not None:
                server.close()
            self._announce(str(exc))
        finally:
            self._starting = False

    @staticmethod
    def _local_ip() -> str | None:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("192.0.2.1", 80))
            value = probe.getsockname()[0]
            probe.close()
            return value if value and not value.startswith("127.") else None
        except OSError:
            return None

    def stop(self, *, disable=False):
        self._stop.set()
        self._lifecycle += 1
        self.enabled = False
        with self._lock:
            self.ticket = self.pending = None
            self.qr = ""
            self._details.clear(); self._detail_bytes = 0
        if self._server is not None:
            self._server.close(); self._server = None
        if self._bonjour is not None:
            if self._service is not None:
                self._bonjour.unregister_service(self._service)
            self._bonjour.close(); self._bonjour = self._service = None
        self._announce("同步已停止")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop()
        self._project_pool.shutdown(wait=False, cancel_futures=True)

    def _serve(self, context, server):
        while not self._stop.is_set():
            try:
                client, _ = server.accept()
                if not self._clients.acquire(blocking=False):
                    client.close()
                    continue
                threading.Thread(target=self._client, args=(context, client), daemon=True).start()
            except (OSError, TimeoutError):
                if self._stop.is_set():
                    return

    def _retry_loop(self, generation):
        while generation == self._lifecycle and not self._stop.wait(30):
            if generation == self._lifecycle and self.enabled:
                self.upload()

    def _client(self, context, client):
        try:
            with context.wrap_socket(client, server_side=True) as stream:
                stream.settimeout(10)
                while not self._stop.is_set():
                    message = _receive(stream)
                    if not message:
                        continue
                    response = self._message(message)
                    if response is not None:
                        _send(stream, response)
        except (EOFError, OSError, ValueError, ssl.SSLError):
            pass
        finally:
            client.close()
            self._clients.release()

    def _message(self, message: dict) -> dict | None:
        with self._lock:
            action = message.get("action")
            if action == "ack":
                return None
            reader = message.get("reader") if isinstance(message.get("reader"), dict) else {}
            known_reader = next((value for value in self.readers if value.get("id") == reader.get("id")
                                 and isinstance(reader.get("localSecret"), str)
                                 and hmac.compare_digest(value.get("localSecret", ""), reader["localSecret"])), None)
            if known_reader:
                candidate_name = reader.get("name")
                if isinstance(candidate_name, str) and 0 < len(candidate_name.strip()) <= 40 and candidate_name != known_reader.get("name"):
                    known_reader["name"] = candidate_name.strip()
                    self._save()
                    self.changed.emit()
                if action == "detail":
                    return self._detail_message(message)
                known = message.get("known") if isinstance(message.get("known"), dict) else {}
                envelopes = [value for value in self.datasets.values()
                             if value["revision"] > (known.get(value["dataset"]) if isinstance(known.get(value["dataset"]), int) else 0)]
                return dict(action="sync", known={k:v["revision"] for k,v in self.datasets.items()},
                            datasets=envelopes, seen=time.time(), supportsAck=True,
                            cloud=ORIGIN if self.cloud_enabled and reader.get("id") in self._cloud_readers else None,
                            capabilities=["request-details-v1"],
                            detailVersions={key:value["revision"] for key,value in self._details.items()})
            ticket = self.ticket
            if (action == "pair" and ticket and ticket["expires"] > time.time()
                    and hmac.compare_digest(str(message.get("ticket") or ""), ticket["ticket"])
                    and isinstance(reader.get("id"), str) and re.fullmatch(r"[0-9a-fA-F-]{36}", reader["id"])
                    and isinstance(reader.get("name"), str) and len(reader["name"]) <= 40
                    and all(isinstance(reader.get(key), str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", reader[key])
                            for key in ("localSecret", "cloudSecret"))
                    and (self.pending is None or self.pending.get("id") == reader.get("id"))):
                self.pending = reader
                self.changed.emit()
                return dict(action="pending")
            return dict(action="revoked" if action == "sync" else "error",
                        error="REVOKED" if action == "sync" else "配对已过期或被拒绝")

    def pair(self):
        if not self.enabled or self.host is None or len(self.readers) >= 3:
            return
        with self._lock:
            self.ticket = dict(version=1, host=self.host["id"], name=self.name, pin=self.host["pin"],
                               ticket=_secret(), expires=time.time()+300)
            if self.cloud_enabled:
                self.ticket["cloud"] = ORIGIN
            self.pending = None
            self.qr = _json(self.ticket).decode("utf-8")
        self.changed.emit()
        issued = self.ticket["ticket"]
        from PySide6.QtCore import QTimer
        def expire():
            if self.ticket is not None and self.ticket["ticket"] == issued:
                self.ticket = self.pending = None
                self.qr = ""
                self.changed.emit()
        QTimer.singleShot(300_000, self, expire)

    def approve(self, allowed: bool):
        with self._lock:
            if self.pending is None or self.ticket is None or self.ticket["expires"] <= time.time():
                return
            if allowed and len(self.readers) < 3:
                self.host["readers"].append(self.pending)
                self._save()
            self.ticket = self.pending = None
            self.qr = ""
        self.changed.emit()
        if allowed:
            self.upload()

    def revoke(self, reader_id: str):
        with self._lock:
            self.host["readers"] = [reader for reader in self.readers if reader["id"] != reader_id]
            self.host.setdefault("revoked", []).append(reader_id)
            self._cloud_readers.discard(reader_id)
            self._save()
        self.changed.emit()
        self.upload()

    def _cache_detail(self, value: dict):
        ident = value["id"]
        payload = _json(value)
        if len(payload) > 1_048_576:
            return None
        digest = hashlib.sha256(payload).hexdigest()
        previous = self._details.get(ident)
        if previous and previous["digest"] == digest:
            self._details.move_to_end(ident)
            return previous
        revision = max(previous["revision"] if previous else 0, int(time.time()*1000))+1
        envelope = dict(dataset="detail-"+ident, revision=revision, digest=digest,
                        payload=payload.decode("utf-8"), bytes=len(payload), started=value["started"],
                        expires=max(value["started"], value.get("completed") or value["started"])+7*86_400)
        if previous:
            self._detail_bytes -= previous["bytes"]
        self._details[ident] = envelope
        self._details.move_to_end(ident)
        self._detail_bytes += len(payload)
        while len(self._details) > 64 or self._detail_bytes > 8*1024*1024:
            _, old = self._details.popitem(last=False)
            self._detail_bytes -= old["bytes"]
        return envelope

    def _detail_message(self, message: dict):
        from codexio.mobile_request_detail import request_detail
        ident = message.get("detailID")
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident):
            return dict(action="detail", error="INVALID", detailID=ident)
        canonical = self._request_ids.get(ident)
        if canonical is None or self._query_path is None:
            return dict(action="detail", error="DETAIL_UNAVAILABLE", detailID=ident)
        value = self._details.get(ident)
        if value is None or value["expires"] <= time.time() or json.loads(value["payload"]).get("availability") in ("partial", "unavailable"):
            source = request_detail(self._query_path, canonical,
                                    list(self.config_provider().get("codex_roots") or []))
            value = self._cache_detail(source) if source is not None else None
            if source is not None:
                self.upload()
        if value is None or value["expires"] <= time.time():
            return dict(action="detail", error="DETAIL_UNAVAILABLE", detailID=ident)
        full = message.get("full") is True
        source = json.loads(value["payload"])
        if full:
            if source.get("availability") == "capacity":
                return dict(action="detail", error="DETAIL_CAPACITY", detailID=ident, full=True)
            raw = value["payload"].encode("utf-8")
            count = (len(raw)+65_535)//65_536
            index = message.get("detailPart", 0)
            if not isinstance(index, int) or not 0 <= index < count:
                return dict(action="detail", error="INVALID", detailID=ident, full=True)
            return dict(action="detail", detailID=ident, full=True, detailPart=index,
                        detailManifest=dict(id=ident, revision=value["revision"], digest=value["digest"],
                                            bytes=len(raw), parts=count),
                        detailChunk=base64.b64encode(raw[index*65_536:(index+1)*65_536]).decode("ascii"))
        source["user"] = source["user"].encode("utf-8")[:1200].decode("utf-8", "ignore")
        source["final"] = source["final"].encode("utf-8")[:5000].decode("utf-8", "ignore")
        source["attachments"] = [dict(item, thumbnail=None) for item in source["attachments"]]
        source["full"] = False
        preview = _json(source)
        return dict(action="detail", detailID=ident, full=False,
                    detail=dict(dataset=value["dataset"], revision=value["revision"],
                                digest=hashlib.sha256(preview).hexdigest(), payload=preview.decode("utf-8")))

    def set_name(self, name: str):
        value = name.strip()[:40]
        if value:
            self.name = value
            if self.host is not None:
                self.host["name"] = value
                self._save()
            self.changed.emit()

    def set_note(self, reader_id: str, note: str):
        if self.host is None or not any(reader.get("id") == reader_id for reader in self.readers):
            return
        with self._lock:
            self.host.setdefault("notes", {})[reader_id] = note.strip()[:80]
            self._save()
        self.changed.emit()

    def enroll(self, invite: str):
        if not self.enabled or not self.host or not invite.strip():
            return
        def work():
            try:
                with self._lock:
                    if not self.host.get("writer"):
                        self.host["writer"] = _secret()
                        self._save()
                _cloud("/v1/enroll", "POST", invite.strip(),
                       dict(host=self.host["id"], writer=self.host["writer"], name=self.name))
                with self._lock:
                    self.host["cloud_enabled"] = True
                    self.cloud_enabled = True
                    self._save()
                self._announce("云端已启用")
                self.upload()
            except (OSError, ValueError, RuntimeError) as exc:
                self.error.emit(str(exc))
        threading.Thread(target=work, name="Codexio-mobile-enroll", daemon=True).start()

    def remove_cloud_key(self):
        if not self.host or not self.cloud_enabled:
            return
        def work():
            try:
                _cloud("/v1/hosts/" + self.host["id"] + "/key", "DELETE", self.host["writer"])
                with self._lock:
                    self.host["cloud_enabled"] = self.cloud_enabled = False
                    self.host["writer"] = ""
                    self._cloud_sent.clear()
                    self._cloud_readers.clear()
                    self._sent_details.clear()
                    self._cloud_details = None
                    self._save()
                self._announce("云端密钥已移除")
            except (OSError, ValueError, RuntimeError) as exc:
                self.error.emit(str(exc))
        threading.Thread(target=work, name="Codexio-mobile-key", daemon=True).start()

    def update(self, data: dict, quota) -> None:
        if self._closed:
            return
        self._latest_data, self._latest_quota = data, quota
        if not self.enabled or not data or not data.get("query_path"):
            return
        generation = data.get("query_generation")
        now = datetime.now().astimezone()
        success = getattr(quota, "last_success_at", None)
        five = getattr(quota, "five_hour", None)
        week = getattr(quota, "week", None)
        quota_key = (getattr(quota, "account_key", None),
                     success.timestamp() if success is not None else None,
                     str(getattr(quota, "status", "")), getattr(quota, "last_error", None),
                     getattr(five, "remaining_percent", None), getattr(week, "remaining_percent", None),
                     getattr(five, "resets_at", None), getattr(week, "resets_at", None))
        key = (generation, now.date(), now.utcoffset(), quota_key)
        if self._generation == key:
            return
        self._generation = key
        self._query_path = data["query_path"]
        if self._detail_generation != generation:
            self._details.clear(); self._detail_bytes = 0
            self._detail_generation = generation
        self._project_pool.submit(self._project, data, quota, key)

    def _put(self, dataset: str, value: dict | list):
        payload = _json(value)
        maximum = dict(live=8000, recent=120000, trends=80000)[dataset]
        if len(payload) >= maximum:
            raise ValueError("Mobile summary exceeds capacity")
        digest = hashlib.sha256(payload).hexdigest()
        with self._lock:
            old = self.datasets.get(dataset)
            if old and old["digest"] == digest:
                return
            self.datasets[dataset] = dict(dataset=dataset, revision=max(old["revision"] if old else 0, int(time.time()*1000))+1,
                                          digest=digest, payload=payload.decode("utf-8"))

    def _project(self, data: dict, quota, key):
        from codexio.usage_queries import UsageQueries
        from codexio.charts import period_bounds
        try:
            if not self.enabled or key != self._generation:
                return
            queries = UsageQueries(data["query_path"])
            today = data.get("menu_bar_today") or {}
            metric = dict(tokens=today.get("tokens"), cost=today.get("usd"), requests=today.get("user_requests") or 0,
                          costComplete=today.get("usd") is not None, hitRate=today.get("cache_hit_rate"))
            current = queries.widget_request()
            def request(row):
                if not row:
                    return None
                return dict(id=hashlib.sha256(row["id"].encode()).hexdigest(),
                            started=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp(),
                            status=row.get("request_status") or "completed", preview=(row.get("prompt_preview") or "")[:80],
                            model=row.get("model") or "", effort=row.get("reasoning_effort"), speed=row.get("service_tier"),
                            tokens=row.get("total_tokens"), cost=row.get("cost_usd"),
                            duration=(row.get("duration_ms") or 0)/1000)
            task = request(current)
            def window(value):
                if value is None:
                    return dict(remaining=None, reset=None, observed=None, retained=False)
                success = getattr(quota, "last_success_at", None)
                status = getattr(quota, "status", None)
                return dict(remaining=value.remaining_percent,
                            reset=value.resets_at.timestamp() if value.resets_at else None,
                            observed=success.timestamp() if success is not None else None,
                            retained=getattr(status, "value", status) in ("stale", "error"))
            success = getattr(quota, "last_success_at", None)
            sampled = success.timestamp() if success is not None else time.time()
            live = dict(name=self.name, timeZone=get_localzone_name(), observed=math.floor(sampled/300)*300,
                        task=task, runningCount=1 if task and task["status"] == "running" else 0,
                        today=metric, five=window(getattr(quota, "five_hour", None)), week=window(getattr(quota, "week", None)))
            history_key = key[:3]
            if self._history_generation != history_key:
                start, end = period_bounds("week")
                recent = queries.recent_mobile_requests(start=start-timedelta(days=1), end=end, limit=200)
                requests = [request(row) for row in recent]
                trends = queries.mobile_trends()
                if not self.enabled or key != self._generation:
                    return
                self._request_ids = {item["id"]: row["id"] for item,row in zip(requests,recent)}
                self._put("recent", requests)
                from codexio.mobile_request_detail import request_detail
                for row in recent[:16]:
                    try:
                        detail = request_detail(data["query_path"], row["id"], [], recover=False, include_thumbnails=False)
                        if detail is not None and detail["availability"] != "unavailable":
                            self._cache_detail(detail)
                    except (OSError, ValueError, sqlite3.Error):
                        continue
                self._put("trends", trends)
                self._history_generation = history_key
            if not self.enabled or key != self._generation:
                return
            self._put("live", live)
            self.changed.emit()
            self.upload()
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            self.error.emit(str(exc))

    def upload(self):
        if not self.enabled or not self.cloud_enabled or not self.host or not self.host.get("writer"):
            return
        with self._lock:
            if (not self.host.get("revoked") and all(reader["id"] in self._cloud_readers for reader in self.readers)
                    and all(self._cloud_sent.get(name) == value["revision"] for name,value in self.datasets.items())
                    and (self._cloud_details is False and time.time() < self._next_capability or
                         self._cloud_details is True and time.time() < self._next_capability
                         and all(self._sent_details.get(ident) == value["digest"] for ident,value in self._details.items()))):
                return
            if self._sending:
                self._upload_again = True
                return
            self._sending = True
        def work():
            try:
                host = self.host["id"]
                token = self.host["writer"]
                for reader in list(self.host.get("revoked") or []):
                    _cloud("/v1/hosts/%s/readers/%s" % (host, reader), "DELETE", token)
                for reader in self.readers:
                    if reader["id"] not in self._cloud_readers:
                        _cloud("/v1/hosts/%s/readers" % host, "PUT", token,
                               dict(id=reader["id"], secret=reader["cloudSecret"]))
                        self._cloud_readers.add(reader["id"])
                for dataset, envelope in list(self.datasets.items()):
                    if self._cloud_sent.get(dataset) != envelope["revision"]:
                        _cloud("/v1/hosts/%s/data/%s" % (host, dataset), "PUT", token, envelope)
                        self._cloud_sent[dataset] = envelope["revision"]
                if self._cloud_details is None or time.time() >= self._next_capability:
                    try:
                        capability = _cloud("/v1/hosts/%s/capabilities" % host, "GET", token)
                        self._cloud_details = "request-details-v1" in capability.get("capabilities", [])
                    except (OSError, ValueError, RuntimeError):
                        self._cloud_details = False
                    self._next_capability = time.time()+3600
                if self._cloud_details:
                    changes = [(ident, value) for ident,value in list(self._details.items())
                               if self._sent_details.get(ident) != value["digest"]]
                    for ident,value in changes[:4]:
                        try:
                            body = {key:value[key] for key in ("dataset", "revision", "digest", "payload")}
                            _cloud("/v1/hosts/%s/details/%s" % (host, ident), "PUT", token, body)
                            self._sent_details[ident] = value["digest"]
                        except (OSError, ValueError, RuntimeError):
                            break
                    if len(changes) > 4:
                        self._upload_again = True
                with self._lock:
                    self.host["revoked"] = []
                    self._save()
                self._announce("云端已同步")
            except (OSError, ValueError, RuntimeError) as exc:
                self.error.emit(str(exc))
            finally:
                with self._lock:
                    again = self._upload_again
                    self._upload_again = self._sending = False
                if again:
                    self.upload()
        threading.Thread(target=work, name="Codexio-mobile-cloud", daemon=True).start()
