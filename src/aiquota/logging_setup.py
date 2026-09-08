from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from aiquota.settings import data_dir

_LOGGER_NAME = "aiquota"

_SENSITIVE_JSON = re.compile(
    r'(?i)("(?:access_token|refresh_token|id_token|api_key|authorization|'
    r'password|secret|token|chatgpt_account_id)"\s*:\s*")[^"]*(")'
)
_BEARER = re.compile(r"(?i)(bearer\s+)[a-z0-9._\-+/=]+")
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)\S+")


def redact_text(text: str) -> str:
    redacted = _SENSITIVE_JSON.sub(r"\1[redacted]\2", text)
    redacted = _BEARER.sub(r"\1[redacted]", redacted)
    return _AUTH_HEADER.sub(r"\1[redacted]", redacted)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_text(formatted)


def setup_logging(log_dir: Optional[Path] = None) -> Path:
    directory = Path(log_dir) if log_dir is not None else data_dir() / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "aiquota.log"

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return log_path


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if name:
        return logging.getLogger("%s.%s" % (_LOGGER_NAME, name))
    return logging.getLogger(_LOGGER_NAME)
