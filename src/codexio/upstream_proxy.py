"""Independent loopback relay and recovery guardian. This module never imports Qt."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
from pathlib import Path
import sys
import ssl
import time

from aiohttp import ClientSession, ClientTimeout, ClientError, DummyCookieJar, TCPConnector, WSMsgType, WSServerHandshakeError, web
import certifi
import psutil

from codexio.upstream_client import alive, identity, route_dependents
from codexio.upstream_config import OFFICIAL_ORIGIN, ROUTE_HEADER, OfficialRoute, UpstreamError, private_json, read_json, exclusive_lock
from codexio.upstream_store import ResponseObserver, UpstreamStore

HOP_HEADERS = {"host", "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade"}


def relay_headers(headers):
    excluded = HOP_HEADERS | {v.strip().lower() for v in headers.get("Connection", "").split(",")}
    return {k: v for k, v in headers.items() if k.lower() not in excluded | {ROUTE_HEADER.lower()}}


class Relay:
    def __init__(self, directory, state, *, origin=OFFICIAL_ORIGIN, dependents=route_dependents):
        self.directory, self.state = Path(directory), dict(state)
        self.route = OfficialRoute(Path(state["config"]), self.directory)
        self.store = UpstreamStore(self.directory.parent / "upstream.sqlite")
        self.origin = origin.rstrip("/")
        self.dependents = dependents
        self.capture = False
        self.owner = state.get("owner")
        self.guards = state.get("guards", [])
        self.error = ""
        self.active_requests = 0
        self.handoff_until = 0
        self.last_control = time.monotonic()
        self.stopping = asyncio.Event()
        self.queue = asyncio.Queue(maxsize=2048)
        self.app = web.Application(client_max_size=1024**3)
        self.app.router.add_post("/_codexio/{action}", self.control)
        self.app.router.add_route("*", "/v1/{path:.*}", self.forward)
        self.app.router.add_route("*", "/{route}/v1/{path:.*}", self.forward)

    def persist(self):
        self.state.update(owner=self.owner, guards=self.guards, capture=self.capture,
                          handoff_until=self.handoff_until, error=self.error, protocol_version=2)
        private_json(self.directory / "session.json", self.state)

    async def start(self):
        context = ssl.create_default_context()
        context.load_verify_locations(certifi.where())
        self.session = ClientSession(timeout=ClientTimeout(total=None, connect=30, sock_read=600),
                                     connector=TCPConnector(ssl=context), cookie_jar=DummyCookieJar(),
                                     auto_decompress=False, trust_env=True)
        # Preserve compressed request bytes together with their Content-Encoding
        # and Content-Length. Automatic decoding here would corrupt the relay.
        self.runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=3, auto_decompress=False)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", int(self.state.get("port", 0)))
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.state.update(port=port, process=identity(psutil.Process()), url=f"http://127.0.0.1:{port}")
        # A previous process may have died with an installed route. Restore only
        # after its old port accepts traffic again, retaining the route token.
        if self.route.journal.exists():
            self.guards = self.dependents()
            self.restore()
        self.persist()
        self.writer = asyncio.create_task(self.write_observations())
        self.guardian = asyncio.create_task(self.watch_owner())
        return self.state["url"]

    def restore(self):
        try:
            self.route.restore()
            self.error = ""
        except (UpstreamError, OSError):
            self.error = "配置恢复未完成，正在保留官方转发并重试"

    def detach(self):
        was_routed = self.capture or self.route.journal.exists()
        self.capture = False
        known = {item["pid"]: item for item in self.guards if alive(item)}
        if was_routed:
            known.update({item["pid"]: item for item in self.dependents()})
        self.guards = list(known.values())
        self.restore()
        self.persist()

    async def control(self, request):
        token = request.headers.get("Authorization", "")
        if not hmac.compare_digest(token, "Bearer " + self.state["control_token"]):
            raise web.HTTPForbidden()
        self.last_control = time.monotonic()
        try:
            data = await request.json() if request.can_read_body else {}
        except ValueError:
            raise web.HTTPBadRequest()
        action = request.match_info["action"]
        if action == "claim":
            new_owner = data.get("owner")
            if self.owner and alive(self.owner) and self.owner != new_owner:
                return web.json_response({"error": "另一个 Codexio 正在使用上游检测"}, status=409)
            if not alive(new_owner):
                raise web.HTTPBadRequest()
            handed_off = self.handoff_until > time.time() and self.capture and data.get("update") is True
            if not handed_off:
                self.detach()
            self.owner = new_owner
            self.handoff_until = 0
            self.persist()
        elif action == "activate":
            if not self.capture:
                if self.route.journal.exists():
                    return web.json_response({"error": self.error or "路由恢复尚未完成"}, status=409)
                try:
                    self.route.install(self.state["url"] + "/v1", self.state["route_token"])
                except (UpstreamError, OSError) as exc:
                    return web.json_response({"error": str(exc) if isinstance(exc, UpstreamError) else "无法写入路由配置"}, status=409)
                self.capture = True
                self.persist()
        elif action == "deactivate":
            self.detach()
        elif action == "handoff":
            self.handoff_until = time.time() + 120
            self.persist()
        elif action == "abandon":
            self.detach()
            self.owner = None
            self.handoff_until = 0
            self.persist()
        elif action == "shutdown":
            self.detach()
            if not self.error:
                self.owner = None
                self.guards = []
                self.handoff_until = 0
                self.persist()
                self.stopping.set()
        elif action != "health":
            raise web.HTTPNotFound()
        return web.json_response({"capture": self.capture, "error": self.error, "process": self.state["process"]})

    def observe(self, *value):
        if self.capture:
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(value)

    async def write_observations(self):
        while True:
            value = await self.queue.get()
            try:
                await asyncio.to_thread(self.store.record, *value)
            except Exception:
                # Disk/database failure must never fail a user's model request.
                pass
            finally:
                self.queue.task_done()

    async def forward(self, request):
        supplied = request.match_info.get("route") or request.headers.get(ROUTE_HEADER, "")
        if not hmac.compare_digest(supplied, self.state["route_token"]):
            raise web.HTTPForbidden()
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            raise web.HTTPUnauthorized()
        # Never accept a destination URL from a client. Paths and redirects cannot
        # change the official origin or send its OAuth credentials to another host.
        target = self.origin + "/" + request.raw_path.split("/v1/", 1)[1]
        headers = relay_headers(request.headers)
        headers["Accept-Encoding"] = "identity"
        self.active_requests += 1
        response = None
        try:
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return await self.forward_websocket(request, target, headers)
            async with self.session.request(request.method, target, headers=headers,
                                            data=request.content.iter_chunked(65536) if request.can_read_body else None,
                                            allow_redirects=False) as upstream:
                response = web.StreamResponse(status=upstream.status, headers=relay_headers(upstream.headers))
                await response.prepare(request)
                observable = upstream.status == 200 and upstream.headers.get("Content-Encoding", "identity") == "identity"
                # The official Codex endpoint can omit Content-Type or return
                # application/octet-stream for SSE. Inspect response bytes rather
                # than trusting a MIME label that the client itself ignores.
                is_response = request.match_info["path"].split("/", 1)[0] == "responses"
                observer = ResponseObserver(self.observe, sse=None) if observable and is_response else None
                async for chunk in upstream.content.iter_any():
                    await response.write(chunk)
                    if observer:
                        try:
                            observer.feed(chunk)
                        except Exception:
                            observer = None
                if observer:
                    with contextlib.suppress(Exception):
                        observer.finish()
                await response.write_eof()
                return response
        except (ClientError, OSError, asyncio.TimeoutError):
            if response is None:
                return web.Response(status=502, text="Official upstream connection unavailable. Retry the request.")
            if request.transport:
                request.transport.close()
            return response
        finally:
            self.active_requests -= 1

    async def forward_websocket(self, request, target, headers):
        headers = {key: value for key, value in headers.items() if not key.lower().startswith("sec-websocket-")}
        protocols = [value.strip() for value in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if value.strip()]
        target = target.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        try:
            async with self.session.ws_connect(target, headers=headers, protocols=protocols, max_msg_size=0) as upstream:
                response = web.WebSocketResponse(protocols=[upstream.protocol] if upstream.protocol else (), max_msg_size=0)
                response.headers.update({key: value for key, value in relay_headers(upstream._response.headers).items()
                                         if not key.lower().startswith("sec-websocket-") and key.lower() not in
                                         ("content-type", "content-length", "content-encoding")})
                await response.prepare(request)
                observer = ResponseObserver(self.observe)
                async def pump(source, destination, observe=False):
                    async for message in source:
                        if message.type == WSMsgType.TEXT:
                            await destination.send_str(message.data)
                        elif message.type == WSMsgType.BINARY:
                            await destination.send_bytes(message.data)
                        else:
                            break
                        if observe and len(message.data) <= observer.MAX_EVENT:
                            with contextlib.suppress(Exception):
                                observer._observe(message.data)
                tasks = [asyncio.create_task(pump(response, upstream)), asyncio.create_task(pump(upstream, response, True))]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await response.close()
                return response
        except WSServerHandshakeError as error:
            return web.Response(status=error.status, text="Official WebSocket connection unavailable.")

    async def watch_owner(self):
        while not self.stopping.is_set():
            await asyncio.sleep(1)
            owner_alive = self.owner and alive(self.owner)
            if owner_alive and self.capture:
                continue
            if self.handoff_until > time.time():
                continue
            if self.capture or self.route.journal.exists():
                self.detach()
            if not owner_alive and not self.route.journal.exists():
                self.stopping.set()
                continue
            self.guards = [item for item in self.guards if alive(item)]
            if owner_alive and time.monotonic() - self.last_control < 10:
                continue
            if not self.guards and not self.active_requests and not self.route.journal.exists():
                self.stopping.set()

    async def close(self):
        self.guardian.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.guardian
        await self.runner.cleanup()
        await self.session.close()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.queue.join(), 3)
        self.writer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.writer


def run_helper(raw_directory):
    directory = Path(raw_directory).resolve()
    async def run():
        relay = Relay(directory, read_json(directory / "session.json"))
        await relay.start()
        try:
            await relay.stopping.wait()
        finally:
            relay.detach()
            await relay.close()
    try:
        with exclusive_lock(directory / "helper.lock"):
            asyncio.run(run())
        return 0
    except Exception:
        # No exception text is logged: HTTP exceptions can contain request data.
        return 1


if __name__ == "__main__":
    sys.exit(run_helper(sys.argv[1]))
