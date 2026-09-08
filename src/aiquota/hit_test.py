from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPoint, QRect

RESIZE_MARGIN = 8
BORDER_HIT_THICKNESS = 12
CARD_RADIUS = 16
HIT_BUTTON = "button"
HIT_RESIZE = "resize"
HIT_DRAG = "drag"


def resize_edge_on_ring(
    pos: QPoint,
    rect: QRect,
    margin: int = RESIZE_MARGIN,
    blocked: str = "",
) -> str:
    if rect.isEmpty() or not rect.contains(pos):
        return ""
    left = pos.x() - rect.left() <= margin and "l" not in blocked
    right = rect.right() - pos.x() <= margin and "r" not in blocked
    top = pos.y() - rect.top() <= margin and "t" not in blocked
    bottom = rect.bottom() - pos.y() <= margin and "b" not in blocked
    edge = ""
    if top:
        edge += "t"
    elif bottom:
        edge += "b"
    if left:
        edge += "l"
    elif right:
        edge += "r"
    return edge


def orb_contains(pos: QPoint, rect: QRect, pad: float = 0.0) -> bool:
    if rect.isEmpty():
        return False
    radius = min(rect.width(), rect.height()) / 2.0 + pad
    cx = rect.left() + rect.width() / 2.0
    cy = rect.top() + rect.height() / 2.0
    return math.hypot(pos.x() - cx, pos.y() - cy) <= radius


def orb_resize_band(rect: QRect) -> int:
    return max(12, min(22, int(min(rect.width(), rect.height()) * 0.14)))


def orb_resize_edge(pos: QPoint, rect: QRect, blocked: str = "") -> str:
    if rect.isEmpty() or not orb_contains(pos, rect):
        return ""
    cx = rect.left() + rect.width() / 2.0
    cy = rect.top() + rect.height() / 2.0
    dx = pos.x() - cx
    dy = pos.y() - cy
    dist = math.hypot(dx, dy)
    radius = min(rect.width(), rect.height()) / 2.0
    if dist < radius - orb_resize_band(rect):
        return ""
    angle = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    sector = int(((angle + 22.5) % 360.0) // 45)
    edge = ("r", "br", "b", "bl", "l", "tl", "t", "tr")[sector]
    return "" if any(part in blocked for part in edge) else edge


def chrome_hit(
    pos: QPoint,
    window_rect: QRect,
    button_rect: QRect,
    radius: int = CARD_RADIUS,
    ring_thickness: int = BORDER_HIT_THICKNESS,
) -> Optional[str]:
    del radius, ring_thickness
    if not window_rect.contains(pos):
        return None
    if button_rect.contains(pos):
        return HIT_BUTTON
    if resize_edge_on_ring(pos, window_rect):
        return HIT_RESIZE
    return HIT_DRAG
