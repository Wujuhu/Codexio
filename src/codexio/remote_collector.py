"""Read-only SSH transport using an existing OpenSSH configuration/identity.

No remote files are created, no password is collected and no packages installed.
The scanner and its JSON configuration are sent through stdin to Python 3.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path

from . import usage_collector

_HOST = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_PYTHON = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*|(?:/[A-Za-z0-9_.-]+)+)$")


def build_ssh_command(source: dict, timeout=30) -> list:
    host = source.get("host") or source.get("hostname") or source.get("alias") or ""
    if not isinstance(host, str) or not _HOST.fullmatch(host) or len(host) > 253:
        raise ValueError("SSH 主机必须是主机名或已有 SSH 配置别名")
    seconds = min(120, max(1, int(timeout)))
    python = source.get("python") or "python3"
    if not isinstance(python, str) or not _PYTHON.fullmatch(python):
        raise ValueError("远程 Python 必须是可执行文件名或无空格绝对路径")
    # Arguments are a list with shell=False. Host input cannot inject options,
    # shell syntax, command substitutions, quotes, or additional arguments.
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout={}".format(min(seconds, 15)),
            "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2",
            host, python + " -"]


def _remote_script(source, cursors) -> str:
    config = {
        "root": source.get("codex_home") or source.get("root") or "~/.codex",
        "cursors": cursors or {},
        "source_id": source.get("id") or source.get("source_id") or "ssh:" + str(source.get("host") or source.get("alias") or source.get("hostname")),
        "source_name": source.get("name") or source.get("source_name") or source.get("host") or "SSH",
        "account_since": source.get("account_since"),
    }
    if not isinstance(config["root"], str) or "\x00" in config["root"]:
        raise ValueError("Codex 数据目录无效")
    encoded = base64.b64encode(json.dumps(config, ensure_ascii=False).encode("utf-8")).decode("ascii")
    # __file__ is present in the source distribution and PyInstaller collect-data
    # build. get_source fallback supports import loaders that expose source.
    module_path = Path(usage_collector.__file__)
    try:
        scanner = module_path.with_suffix(".py").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        loader = getattr(usage_collector, "__loader__", None)
        scanner = loader.get_source(usage_collector.__name__) if loader and hasattr(loader, "get_source") else None
        if not scanner:
            raise RuntimeError("打包中缺少远程采集器源码")
    return scanner + "\n\nimport base64\n_config = json.loads(base64.b64decode('" + encoded + "'))\n" + (
        "_result = scan_directory(**_config)\n"
        "print(json.dumps(_result, ensure_ascii=True, separators=(',', ':'), allow_nan=False))\n"
    )


def collect_ssh(source: dict, cursors: dict, timeout=30) -> dict:
    command = build_ssh_command(source, timeout)
    script = _remote_script(source, cursors)
    kwargs = {"input": script.encode("utf-8"), "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
              "timeout": min(300, max(1, int(timeout))), "check": False, "shell": False}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("SSH 采集超时，稍后重试") from error
    except OSError as error:
        raise RuntimeError("无法启动系统 SSH 客户端") from error
    if process.returncode:
        # Do not echo remote stderr; it can contain private paths or banners.
        raise RuntimeError("SSH 采集失败（退出码 {}），请检查已有主机认证和 Python 3".format(process.returncode))
    try:
        result = json.loads(process.stdout.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("SSH 返回的数据格式无效") from error
    if not isinstance(result, dict) or not isinstance(result.get("records"), list) or not isinstance(result.get("observations"), list) or not isinstance(result.get("cursors"), dict):
        raise RuntimeError("SSH 返回的数据结构无效")
    return result
