"""Presentation aggregates over priced, deduplicated calls; never a second ledger."""
from __future__ import annotations

import math
from collections import defaultdict

from codexio.durations import request_duration_fields, valid_milliseconds

COUNTERS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")
REQUEST_STATUSES = {"completed": "完成", "running": "回复中", "aborted": "已中断", "unknown": "未知"}


def turn_key(session_id, turn_id):
    return "turn:{}:{}".format(session_id, turn_id)


def normalized_tier(value):
    value = str(value or "").lower()
    if value in ("priority", "fast"):
        return "priority"
    if value in ("default", "standard"):
        return "default"
    return "unknown"


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _cost(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _merge_turns(turns):
    merged = {}
    for incoming in sorted(turns, key=lambda row: (str(row.get("observed_at") or row.get("ended_at") or ""), str(row.get("id") or ""))):
        if not incoming.get("session_id") or not incoming.get("turn_id") or incoming.get("verified") is False:
            continue
        key = turn_key(incoming["session_id"], incoming["turn_id"])
        old = merged.get(key, {})
        row = dict(old)
        row.update({name: value for name, value in incoming.items() if value not in (None, "", [])})
        if old.get("ended_at") and not incoming.get("ended_at"):
            row["status"], row["ended_at"] = old.get("status", "unknown"), old["ended_at"]
        if old.get("started_at") and not old.get("started_inferred"):
            if incoming.get("started_inferred") or not incoming.get("started_at"):
                row["started_at"], row["started_inferred"] = old["started_at"], False
            else:
                row["started_at"] = min(old["started_at"], incoming["started_at"])
        row["first_turn"] = bool(old.get("first_turn") or incoming.get("first_turn"))
        row["input_hashes"] = list(dict.fromkeys(old.get("input_hashes", []) + incoming.get("input_hashes", [])))
        row["source_ids"] = list(dict.fromkeys(old.get("source_ids", []) + (incoming.get("source_ids") or [])
                                             + ([incoming["source_id"]] if incoming.get("source_id") else [])))
        merged[key] = row
    return merged


def _alias_resolver(metadata):
    aliases = {key: row["alias_of"] for key, row in metadata.items() if row.get("alias_of")}
    def resolve(key):
        original, visited = key, set()
        while key in aliases:
            if key in visited:
                return original
            visited.add(key)
            key = aliases[key]
        return key
    return resolve


def _parent_edges(bases, links, resolve):
    links = list({row.get("id", str(index)): row for index, row in enumerate(links)}.values())
    by_parent_session = defaultdict(list)
    by_child_session = defaultdict(list)
    for link in links:
        by_parent_session[link.get("parent_session_id")].append(link)
        if link.get("child_session_id"):
            by_child_session[link["child_session_id"]].append(link)
    edges = {}
    for key, base in bases.items():
        meta = base["meta"]
        direct = by_child_session.get(meta.get("session_id"), [])
        if not meta.get("is_subagent") and not direct:
            continue
        if direct:
            meta["is_subagent"] = True
        candidates = set()
        parent_session = meta.get("parent_session_id")
        if parent_session and meta.get("parent_turn_id"):
            candidates.add(resolve(turn_key(parent_session, meta["parent_turn_id"])))
        hashes = set(meta.get("input_hashes") or [])
        relevant = {link.get("id", str(index)): link for index, link in enumerate(by_parent_session.get(parent_session, []) + direct)}.values()
        for link in relevant:
            exact_id = link.get("child_session_id") == meta.get("session_id")
            exact_path = bool(parent_session and link.get("parent_session_id") == parent_session and meta.get("agent_path") and link.get("target") == meta["agent_path"])
            if not (exact_id or exact_path):
                continue
            explicit_turn = link.get("child_turn_id")
            if explicit_turn:
                matches = explicit_turn == meta.get("turn_id")
            elif link.get("kind") == "spawn":
                matches = bool(meta.get("first_turn"))
            else:
                matches = bool(link.get("message_hash") and link["message_hash"] in hashes)
            if matches and link.get("parent_turn_id"):
                candidates.add(resolve(turn_key(link["parent_session_id"], link["parent_turn_id"])))
        if len(candidates) == 1:
            parent = next(iter(candidates))
            if parent in bases and parent != key:
                edges[key] = parent
        if len(candidates) > 1:
            base["association_note"] = "存在多个父轮次，未合并费用"
    return edges


def iter_user_requests(records, turns=(), agent_links=(), sources=(), *, detail_keys=False):
    """Yield complete groups; callers may persist them without retaining a second list."""
    agent_links = agent_links if isinstance(agent_links, list) else list(agent_links)
    metadata = _merge_turns(turns)
    resolve = _alias_resolver(metadata)
    referenced_parents = {resolve(turn_key(link.get("parent_session_id"), link.get("parent_turn_id")))
                          for link in agent_links if link.get("parent_session_id") and link.get("parent_turn_id")}
    bases = {}
    for key, meta in metadata.items():
        if resolve(key) != key:
            continue
        if (meta.get("prompt_preview") or meta.get("has_usage") or key in referenced_parents) and not (
                meta.get("synthetic") and not meta.get("has_usage") and key not in referenced_parents):
            bases[key] = {"meta": meta, "records": []}
    unique = {}
    for index, record in enumerate(records):
        identity = str(record.get("id") or "display:{}".format(index))
        if identity not in unique:
            unique[identity] = record if record.get("id") == identity else dict(record, id=identity)
    for record in unique.values():
        session, turn = record.get("session_id"), record.get("request_turn_id") or record.get("turn_id")
        key = resolve(turn_key(session, turn)) if session and turn else "unassigned:" + record["id"]
        base = bases.setdefault(key, {"meta": metadata.get(key, {"session_id": session or "", "turn_id": turn or ""}), "records": []})
        base["records"].append(record)
    edges = _parent_edges(bases, agent_links, resolve)
    pending_parents = set()
    for link in agent_links:
        if link.get("kind") != "spawn":
            continue
        parent = resolve(turn_key(link.get("parent_session_id"), link.get("parent_turn_id")))
        found = any((link.get("child_session_id") and base["meta"].get("session_id") == link["child_session_id"])
                    or (link.get("target") and base["meta"].get("agent_path") == link["target"]
                        and base["meta"].get("parent_session_id") == link.get("parent_session_id")) for base in bases.values())
        if parent in bases and not found:
            pending_parents.add(parent)
    def root_of(key):
        original, seen = key, set()
        while key in edges:
            if key in seen:
                return original
            seen.add(key)
            key = edges[key]
        return key
    components = defaultdict(list)
    for key in bases:
        components[root_of(key)].append(key)
    names = {str(source.get("id") or source.get("source_id") or ""): str(source.get("name") or source.get("source_name") or source.get("id") or "") for source in sources}
    for root, keys in components.items():
        primary = bases[root]
        meta = primary["meta"]
        rows = sorted((record for key in keys for record in bases[key]["records"]), key=lambda row: (str(row.get("timestamp") or ""), row["id"]))
        own_rows = sorted(primary["records"], key=lambda row: str(row.get("timestamp") or ""))
        first = own_rows[0] if own_rows else {}
        primary_status = meta.get("status", "unknown")
        statuses = [bases[key]["meta"].get("status", "unknown") for key in keys]
        if "running" in statuses:
            status = "running"
        elif primary_status == "aborted":
            status = "aborted"
        elif primary_status == "completed" and all(value in ("completed", "aborted") for value in statuses) and not pending_parents.intersection(keys):
            status = "completed"
        else:
            status = "unknown"
        models = sorted({str(row.get("model") or "未知模型") for row in rows})
        tiers = sorted({normalized_tier(row.get("service_tier")) for row in rows})
        source_ids = set() if rows else set(meta.get("source_ids") or [])
        for row in rows:
            source_ids.update(row.get("source_ids") or ([row["source_id"]] if row.get("source_id") else []))
            if row.get("source_id") and row.get("source_name"):
                names.setdefault(row["source_id"], row["source_name"])
        source_ids = sorted(source_ids)
        source_names = [names.get(source) or source for source in source_ids]
        costs = [_cost(row.get("cost_usd")) for row in rows]
        known = [value for value in costs if value is not None]
        missing = len(costs) - len(known)
        pricing = ("unmetered" if not rows else "unpriced" if not known else "partial" if missing else
                   "estimated" if any(row.get("pricing_status") == "estimated" for row in rows) else "priced")
        preview = meta.get("output_preview") if primary_status in ("completed", "aborted") else meta.get("latest_output_preview")
        preview = preview or meta.get("output_preview") or next((row.get("output_preview") for row in reversed(own_rows) if row.get("output_preview")), "")
        association = primary.get("association_note") or ("未能明确关联父请求，子代理单独计量" if meta.get("is_subagent") else "")
        if pending_parents.intersection(keys):
            association = "部分子代理日志尚未采集，当前费用为已记录调用的累计"
        started = meta.get("started_at") or first.get("timestamp") or (rows[0].get("timestamp") if rows else "")
        if meta.get("started_inferred", True) and first.get("timestamp"):
            started = min(filter(None, (started, first["timestamp"])))
        group = {
            "id": root, "record_kind": "unassigned" if root.startswith("unassigned:") else "user_request",
            "session_id": meta.get("session_id") or first.get("session_id") or "",
            "turn_id": meta.get("turn_id") or first.get("turn_id") or "",
            "timestamp": started,
            "started_at": started, "started_inferred": meta.get("started_inferred", True),
            "ended_at": max((str(bases[key]["meta"].get("ended_at") or "") for key in keys), default=""),
            "request_status": status, "status_label": REQUEST_STATUSES[status], "association_note": association,
            "is_subagent": bool(meta.get("is_subagent")), "subagent_count": len({bases[key]["meta"].get("session_id") for key in keys if key != root}),
            "call_count": len(rows), "member_ids": [row["id"] for row in rows],
            "models": models, "model": models[0] if len(models) == 1 else "多模型（%d）" % len(models) if models else "等待调用",
            "service_tiers": tiers, "service_tier": tiers[0] if len(tiers) == 1 else "mixed" if tiers else None,
            "source_ids": source_ids, "source_names": source_names,
            "source_id": source_ids[0] if len(source_ids) == 1 else "multiple" if source_ids else "",
            "source_name": source_names[0] if len(source_names) == 1 else "多来源（%d）" % len(source_names) if source_names else "—",
            "session_title": first.get("session_title") or meta.get("session_title") or "",
            "prompt_preview": meta.get("prompt_preview") or first.get("prompt_preview") or "",
            "output_preview": preview, "cost_usd": math.fsum(known) if known else None,
            "pricing_status": pricing, "unpriced_calls": missing,
            "pricing_reason": "已知费用；另有 %d 条调用未定价" % missing if pricing == "partial" else
                              "等待计量记录" if pricing == "unmetered" else "逐条调用按各自模型与档位计价后合计",
        }
        if group["record_kind"] == "unassigned":
            group.update(duration_ms=valid_milliseconds(first.get("duration_ms")), duration_running=False, duration_started_at=None)
        else:
            group.update(request_duration_fields(meta, [bases[key]["meta"] for key in keys], status,
                                                 pending=bool(pending_parents.intersection(keys))))
        group.update({field: sum(_count(row.get(field)) for row in rows) for field in COUNTERS})
        group["total_tokens"] = group["input_tokens"] + group["output_tokens"]
        if detail_keys:
            group["_primary_call_id"] = first.get("id")
            group["_preview_call_id"] = next((row["id"] for row in reversed(own_rows) if row.get("output_preview")), None)
        yield group


def aggregate_user_requests(records, turns=(), agent_links=(), sources=()):
    return sorted(iter_user_requests(records, turns, agent_links, sources),
                  key=lambda row: (str(row.get("timestamp") or ""), row["id"]), reverse=True)


def matches_call(record, source="", model="", tier=""):
    return (not source or source in (record.get("source_ids") or [record.get("source_id")])) and (
        not model or (record.get("model") or "未知模型") == model) and (
        not tier or normalized_tier(record.get("service_tier")) == normalized_tier(tier))
