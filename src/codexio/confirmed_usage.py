"""Confirmed display metrics over the collector's already deduplicated records.

This does not change the ledger or the completeness rules for extrapolation.
Each metric has its own evidence: a missing price does not erase a known call.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation


METRICS = ("tokens", "usd", "requests")


def confirmed_count(value):
    """A nonnegative integer, or None; never turn malformed counters into zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    try:
        number = Decimal(value)
        return int(number) if number.is_finite() and number >= 0 and number == number.to_integral_value() else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def confirmed_tokens(record):
    total = confirmed_count(record.get("total_tokens"))
    if total is None or str(record.get("quality", "")).lower().startswith("invalid"):
        return None
    # Older imports can have just a total. If components are present, require
    # the same input + output identity that the collector uses for Total Token.
    if "input_tokens" in record or "output_tokens" in record:
        inp, out = (confirmed_count(record.get(key)) for key in ("input_tokens", "output_tokens"))
        if inp is None or out is None or total != inp + out:
            return None
    return total


def confirmed_cost(record):
    if str(record.get("pricing_status") or "").lower() not in ("", "priced", "estimated"):
        return None
    cost = record.get("cost_usd")
    if cost is None or isinstance(cost, bool):
        return None
    try:
        cost = float(cost)
        return cost if math.isfinite(cost) and cost >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def confirmed_call(record):
    quality = str(record.get("quality") or "").lower().split(":", 1)[0]
    # Missing quality is the legacy normalized model-call payload. A cumulative
    # delta alone cannot prove one call; a canonical response identity can.
    return quality in ("", "response", "legacy_last") or (
        quality == "cumulative_delta" and str(record.get("id") or "").startswith("response:"))


def _add_cost(partials, value):
    """Keep fsum-style partials instead of retaining every historical cost."""
    index = 0
    for other in partials:
        if abs(value) < abs(other):
            value, other = other, value
        high = value + other
        low = other - (high - value)
        if low:
            partials[index] = low
            index += 1
        value = high
    partials[index:] = [value]


class ConfirmedUsage:
    def __init__(self):
        self.totals = dict(tokens=0, requests=0)
        self.valid = dict.fromkeys(METRICS, 0)
        self.skipped = dict.fromkeys(METRICS, 0)
        self.costs = []
        self.records = self.unpriced_tokens = 0

    def add(self, record):
        self.records += 1
        tokens, cost = confirmed_tokens(record), confirmed_cost(record)
        values = dict(tokens=tokens, usd=cost, requests=1 if confirmed_call(record) else None)
        for metric, value in values.items():
            if value is None:
                self.skipped[metric] += 1
                continue
            self.valid[metric] += 1
            if metric == "usd":
                _add_cost(self.costs, value)
            else:
                self.totals[metric] += value
        if cost is None and tokens is not None:
            self.unpriced_tokens += tokens

    def summary(self):
        totals = dict(self.totals, usd=math.fsum(self.costs))
        return dict({key: totals[key] if self.valid[key] else None for key in METRICS},
                    records=self.records, valid_counts=dict(self.valid), skipped=dict(self.skipped),
                    unpriced_tokens=self.unpriced_tokens)


def summarize_confirmed_usage(records):
    totals = ConfirmedUsage()
    for record in records:
        totals.add(record)
    return totals.summary()
