"""Validate a frozen headless helper against a temporary config, without a client restart."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.request import Request, ProxyHandler, build_opener

import psutil


def verify(executable, output, env=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=output) as raw:
        directory = Path(raw)
        config = directory / "isolated-codex.toml"
        original = '# isolated bundle check\nmodel="gpt-6-astra"\n'
        config.write_text(original, encoding="utf-8")
        descriptor = directory / "session.json"
        owner = psutil.Process()
        state = dict(config=str(config), owner=dict(pid=owner.pid, created=owner.create_time()),
                     control_token=secrets.token_urlsafe(32), route_token=secrets.token_urlsafe(32), port=0)
        descriptor.write_text(json.dumps(state), encoding="utf-8")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        process = subprocess.Popen([str(executable), "--upstream-proxy", str(directory)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        state = {}
        def control(action):
            request = Request(state["url"] + "/_codexio/" + action, b"{}",
                              headers={"Authorization": "Bearer " + state["control_token"], "Content-Type": "application/json"})
            with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
                return json.load(response)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = json.loads(descriptor.read_text(encoding="utf-8"))
                if state.get("url"):
                    break
                if process.poll() is not None:
                    raise RuntimeError("Frozen upstream helper exited before becoming ready")
                time.sleep(.1)
            assert control("health")["capture"] is False
            assert control("activate")["capture"] is True
            assert 'codexio-upstream' in config.read_text(encoding="utf-8")
            assert control("deactivate")["capture"] is False
            assert config.read_text(encoding="utf-8") == original
            assert not (directory / "route.json").exists()
            control("abandon")
        finally:
            # Only terminate this isolated test helper, never a user client.
            ident = state.get("process", {})
            try:
                child = psutil.Process(ident["pid"])
                if abs(child.create_time() - ident["created"]) < .01:
                    child.terminate()
            except (psutil.Error, KeyError):
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    result = {"ok": True, "checks": ["headless-start", "local-control-auth", "temporary-route", "exact-restore"]}
    (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Frozen upstream helper: startup, routing and restoration passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    verify(arguments.executable.resolve(), arguments.output.resolve())
