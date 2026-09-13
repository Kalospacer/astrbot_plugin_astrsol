"""面板的数据组装。main.py 只留薄壳，取数和聚合都在这里。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .economics import MEMO_TOKENS, effective_cache_read
from .split import TOOL_NAME
from .summarize import build_prompt

PER_MILLION = 1_000_000
OUTCOMES = ("not_called", "too_small", "dry_run", "empty_summary", "compacted")


def _model_view(entry: dict | None, model_name: str, role: str) -> dict:
    """一个模型在面板上的呈现：解析结果 + 价目（换算成每百万 tokens）。"""
    view = {
        "role": role,
        "configured_name": model_name,
        "resolved": bool(entry),
    }
    if not entry:
        return view
    view.update(
        {
            "id": entry["id"],
            "name": entry["name"],
            "context": entry["context"],
            "hits": entry["hits"],
            "muted": entry["muted"],
            "score": entry["score"],
            "price": {
                "input": entry["input"] * PER_MILLION,
                "output": entry["output"] * PER_MILLION,
                "cache_read": entry["cache_read"] * PER_MILLION,
                "cache_write": entry["cache_write"] * PER_MILLION,
                "cache_read_effective": effective_cache_read(entry) * PER_MILLION,
                "has_cache_discount": bool(entry["cache_read"]),
            },
        }
    )
    return view


async def _alerts(plugin, limits: dict) -> list[dict]:
    """正在悄悄让插件不工作的东西。这些现在只出现在 error 日志里，没人会看。"""
    alerts: list[dict] = []
    compression = plugin.compression_config()

    max_turns = compression.get("max_turns", -1)
    if max_turns != -1:
        alerts.append(
            {
                "level": "warn",
                "title": "核心开启了按轮截断",
                "body": f"max_turns = {max_turns}，它会抢在压缩之前丢掉最老的几轮消息，"
                "和本插件互相干扰。",
                "fix": "到「上下文管理策略 → 压缩前最多保留对话轮数」改回 -1。",
            }
        )

    blocked = await _personas_blocking_tool(plugin)
    if blocked:
        alerts.append(
            {
                "level": "error",
                "title": "有人格把压缩工具挡在门外",
                "body": f"人格 {'、'.join(blocked)} 配置了工具白名单，但没把 {TOOL_NAME} "
                "加进去。这些人格下模型根本看不到这个工具，插件完全静默失效。",
                "fix": f"在这些人格的工具列表里加上 {TOOL_NAME}，或者清空白名单。",
            }
        )

    spot = limits.get("sweet_spot")
    if spot is not None and not spot.solvable:
        alerts.append(
            {
                "level": "error",
                "title": "这套模型组合永远不回本",
                "body": spot.reason,
                "fix": "换一个更便宜的压缩模型，或把「目标回本轮数」调大。",
            }
        )

    if plugin.catalog.error:
        alerts.append(
            {
                "level": "warn",
                "title": "拿不到 OpenRouter 价目",
                "body": f"{plugin.catalog.error}。压缩线按窗口画，不受影响；"
                "只是算不出这套组合划不划算。",
                "fix": "检查容器能否访问 openrouter.ai。",
            }
        )

    return alerts


async def _personas_blocking_tool(plugin) -> list[str]:
    """列出配了工具白名单、却没放行本插件工具的人格。"""
    personas = await plugin.context.persona_manager.get_all_personas()
    blocked = []
    for persona in personas:
        tools = getattr(persona, "tools", None)
        if isinstance(tools, list) and tools and TOOL_NAME not in tools:
            blocked.append(getattr(persona, "persona_id", None) or str(persona))
    return blocked


async def status(plugin) -> dict:
    """面板主数据：当前模式、阈值三件套及其来源、告警。"""
    limits = await plugin.thresholds()
    compression = plugin.compression_config()
    spot = limits.get("sweet_spot")

    mode = "off"
    if plugin.config["enable_compact"]:
        mode = "dry_run" if plugin.config["dry_run"] else "live"

    return {
        "mode": mode,
        "enabled": plugin.enabled,
        "thresholds": {
            "keep_recent": limits["keep_recent"],
            "min_archive": limits["min_archive"],
            "threshold": limits["keep_recent"] + limits["min_archive"],
            "window": limits["window"],
            "builtin_fallback": int(limits["window"] * 0.82),
            "keep_source": limits["keep_source"],
            "archive_source": limits["archive_source"],
            "window_source": limits["window_source"],
            "ratio": plugin.config.get("threshold_ratio", 0.72),
        },
        "sweet_spot": _spot_view(spot),
        "config": {k: plugin.config[k] for k in plugin.config.keys()},
        "core": {
            "provider_id": compression.get("provider_id", ""),
            "max_turns": compression.get("max_turns", -1),
            "overflow_strategy": compression.get("overflow_strategy", ""),
            "keep_recent_ratio": compression.get("keep_recent_ratio", 0),
            "fallback_max_tokens": compression.get("fallback_max_tokens", 0),
            "instruction": compression.get("instruction", ""),
            "instruction_source": "核心配置"
            if compression.get("instruction", "").strip()
            else "插件默认",
        },
        "prompt_preview": build_prompt(plugin.summarize_instruction()),
        "alerts": await _alerts(plugin, limits),
    }


def _spot_view(spot) -> dict | None:
    if spot is None:
        return None
    return {
        "solvable": spot.solvable,
        "reason": spot.reason,
        "min_archive": spot.min_archive,
        "breakeven_turns": spot.breakeven_turns,
        "one_time_cost": spot.one_time_cost,
        "per_round_saving": spot.per_round_saving,
        "detail": spot.detail,
    }


async def models(plugin) -> dict:
    """两个模型的解析结果和价目，供面板算模拟器。"""
    main_provider = await plugin.context.get_using_provider_async()
    summ_provider = await plugin.summarize_provider()
    main_entry, summ_entry = await plugin.priced_pair()

    return {
        "catalog_size": len(plugin.catalog.models),
        "catalog_error": plugin.catalog.error,
        "fetched_at": plugin.catalog.fetched_at,
        "memo_tokens": MEMO_TOKENS,
        "main": _model_view(
            main_entry, main_provider.get_model() if main_provider else "", "主模型"
        ),
        "summarizer": _model_view(
            summ_entry, summ_provider.get_model() if summ_provider else "", "摘要模型"
        ),
    }


async def sessions(plugin, limit: int = 40) -> dict:
    """各会话当前用量，标尺的游标就是它们。"""
    limits = await plugin.thresholds()
    conversations = await plugin.context.conversation_manager.get_conversations()
    rows = [
        {
            "cid": c.cid,
            "title": c.title or "未命名会话",
            "user_id": c.user_id,
            "tokens": c.token_usage,
            "updated_at": c.updated_at,
        }
        for c in conversations
        if c.token_usage
    ]
    rows.sort(key=lambda r: r["tokens"], reverse=True)
    return {
        "threshold": limits["keep_recent"] + limits["min_archive"],
        "keep_recent": limits["keep_recent"],
        "window": limits["window"],
        "builtin_fallback": int(limits["window"] * 0.82),
        "total": len(rows),
        "sessions": rows[:limit],
    }


def _read_ledger(path: Path, limit: int) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows[-limit:]


def ledger_view(plugin, limit: int = 200) -> dict:
    """台账聚合。触发率是判断这插件值不值得开的首要依据。"""
    rows = _read_ledger(plugin.ledger_path, limit)
    counts = Counter(r.get("outcome", "unknown") for r in rows)

    called = sum(
        counts[o] for o in ("too_small", "dry_run", "empty_summary", "compacted")
    )
    eligible = called + counts["not_called"]

    archives = [r["archive_tokens"] for r in rows if r.get("archive_tokens")]
    durations = [r["duration_ms"] for r in rows if r.get("duration_ms")]
    compacted = [r for r in rows if r.get("outcome") == "compacted"]

    return {
        "total": len(rows),
        "counts": {o: counts[o] for o in OUTCOMES},
        "call_rate": (called / eligible) if eligible else 0.0,
        "eligible": eligible,
        "archive_tokens": archives,
        "duration_ms_avg": (sum(durations) / len(durations)) if durations else 0,
        "compacted": [
            {
                "ts": r.get("ts"),
                "before": r.get("total_tokens", 0),
                "after": r.get("keep_tokens", 0) + r.get("memo_tokens", 0),
                "archive": r.get("archive_tokens", 0),
                "memo": r.get("memo_tokens", 0),
                "duration_ms": r.get("duration_ms", 0),
            }
            for r in compacted
        ],
        "recent": list(reversed(rows[-30:])),
    }


def save_config(plugin, body: dict) -> dict:
    """只写插件自己的配置项，核心配置一律不碰。"""
    allowed = set(plugin.config.keys())
    written = {}
    for key, value in (body or {}).items():
        if key in allowed:
            plugin.config[key] = value
            written[key] = value
    plugin.config.save_config()
    plugin._spot_cache.clear()
    return {"ok": True, "written": written}
