"""Presentation labels; raw model IDs remain unchanged for pricing and filters."""
from __future__ import annotations

EFFORT_LABELS = {
    "low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra High",
    "max": "Max", "ultra": "Ultra",
}


def display_model(value) -> str:
    model = str(value or "").strip()
    if not model or model.lower() == "unknown":
        return "未知模型"
    parts = []
    for part in model.split("-"):
        if part.lower() == "gpt":
            parts.append("GPT")
        elif part.isalpha() and part:
            parts.append(part[:1].upper() + part[1:])
        else:
            parts.append(part)
    return "-".join(parts)


def display_effort(value) -> str:
    return EFFORT_LABELS.get(str(value or "").strip().lower(), "")
