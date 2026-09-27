"""Persist one explicitly selected reset attempt until its result is known."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
import uuid

from codexio.i18n import tr
from codexio.rate_limits import ResetCredit


class ResetAttempts:
    def __init__(self, directory):
        self.path = directory / "reset_operations.json"

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, values):
        temporary = self.path.with_suffix(".json.tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, default=str)
            stream.flush(); os.fsync(stream.fileno())
        temporary.replace(self.path)

    def pending(self, account):
        value = self._read().get(account)
        return value if isinstance(value, dict) else None

    def begin(self, account, credit):
        values = self._read()
        existing = values.get(account)
        if existing:
            if existing.get("creditId") != credit.id:
                raise RuntimeError(tr("请先确认上一次重置的结果"))
            return existing
        value = dict(creditId=credit.id, idempotencyKey=str(uuid.uuid4()), credit=asdict(credit), startedAt=datetime.now(timezone.utc).isoformat())
        values[account] = value; self._save(values)
        return value

    def finish(self, account):
        values = self._read(); values.pop(account, None); self._save(values)

    @staticmethod
    def credit(operation):
        raw = dict(operation.get("credit") or {})
        for key in ("expires_at", "granted_at"):
            value = raw.get(key)
            try:
                raw[key] = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
            except ValueError:
                raw[key] = None
        try:
            return ResetCredit(**raw)
        except TypeError:
            return None
