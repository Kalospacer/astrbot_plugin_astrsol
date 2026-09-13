"""OpenRouter 模型目录：拉取、缓存，以及把怪名字解析成目录里的真实模型。

解析算法移植自 Kalo 的 model-alias-resolver：零词表、零黑白名单，全部从目录数据
现场统计。要点是 df=0 自动消音（目录里没有任何模型含这个 token，它就是噪声，
直接丢），以及打分用 idf——版本号天生比品牌词权重高。
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import aiohttp

from astrbot.api import logger

CATALOG_URL = "https://openrouter.ai/api/v1/models"
TTL_SECONDS = 24 * 3600


def tokenize(text: str) -> list[str]:
    """切词。版本形态（3.8、4-6）优先整体抽出，再按字母/数字边界切。"""
    toks: list[str] = []

    def _take_version(m: re.Match) -> str:
        toks.append(f"{m.group(1)}.{m.group(2)}")
        return " "

    text = re.sub(r"(\d+)[.\-](\d{1,2})(?!\d)", _take_version, str(text).lower())
    for part in re.split(r"[-_./\s]+", text):
        toks.extend(re.findall(r"[a-z]+|\d+(?:\.\d+)?", part))

    seen: set[str] = set()
    return [t for t in toks if not (t in seen or seen.add(t))]


def _version_of(toks: list[str]) -> str | None:
    for t in toks:
        if re.fullmatch(r"\d+(\.\d+)?", t) and ("." in t or len(t) < 4):
            return t
    return None


def _price(pricing: dict, key: str) -> float:
    try:
        return float(pricing.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def prepare(raw: list[dict]) -> list[dict]:
    """把目录原始条目加工成匹配用的形态。带冒号的 id（:free、:latest）会虚高，剔除。"""
    out = []
    for m in raw:
        mid = m.get("id")
        if not mid or ":" in mid:
            continue
        toks = tokenize(mid.split("/")[-1].lstrip("~"))
        ver = _version_of(toks)
        vi = toks.index(ver) if ver else -1
        pricing = m.get("pricing") or {}
        out.append(
            {
                "id": mid,
                "name": m.get("name") or mid,
                "context": m.get("context_length") or 0,
                "created": m.get("created") or 0,
                "toks": toks,
                "ver": ver,
                "post_ver": toks[vi + 1 :] if vi >= 0 else [],
                "input": _price(pricing, "prompt"),
                "output": _price(pricing, "completion"),
                "cache_read": _price(pricing, "input_cache_read"),
                "cache_write": _price(pricing, "input_cache_write"),
            }
        )
    return out


def _match(tok: str, model: dict) -> float:
    """版本 token 要求相等（5 不等于 5.3），主版本相同给部分分；词 token 按边界匹配。"""
    if tok[0].isdigit():
        if not model["ver"]:
            return 0.0
        if model["ver"] == tok:
            return 1.0
        if model["ver"].split(".")[0] == tok.split(".")[0]:
            return 0.3
        return 0.0
    return 1.0 if tok in model["toks"] else 0.0


def resolve(alias: str, models: list[dict]) -> dict | None:
    """把别名解析为最置信的目录模型，没有任何证据时返回 None。"""
    qtoks = tokenize(alias)
    total = len(models)
    if not qtoks or not total:
        return None

    df = {t: sum(1 for m in models if _match(t, m) == 1.0) for t in qtoks}
    ranked = []
    for m in models:
        score, hits = 0.0, []
        for t in qtoks:
            if not df[t]:
                continue
            w = _match(t, m)
            if w > 0:
                score += math.log(total / df[t]) * w
                hits.append(t)
        if score <= 0:
            continue
        # 未命中的后置变体（image/code/0731…）越少越贴切
        extra = sum(1 for t in m["post_ver"] if t not in qtoks)
        ranked.append((m, score, hits, extra))

    if not ranked:
        return None

    def version_key(m: dict) -> list[int]:
        if not m["ver"]:
            return [-1, 0, 0, 0]
        parts = [int(x) for x in m["ver"].split(".")]
        return (parts + [0, 0, 0, 0])[:4]

    # 命中数优先于总分：一个偶然的高 idf token 不该压过多个 token 的共同命中
    ranked.sort(
        key=lambda r: (
            -len(r[2]),
            -r[1],
            tuple(-x for x in version_key(r[0])),
            r[3],
            -r[0]["created"],
        )
    )
    best, score, hits, _ = ranked[0]
    return {
        **best,
        "score": round(score, 3),
        "hits": hits,
        "query_tokens": qtoks,
        "muted": [t for t in qtoks if not df[t]],
    }


class Catalog:
    """目录的拉取与落盘缓存。"""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self.models: list[dict] = []
        self.fetched_at = 0.0
        self.error = ""

    async def load(self) -> list[dict]:
        """返回可用的模型列表。缓存未过期就用缓存，否则拉一次。"""
        if self.models and time.time() - self.fetched_at < TTL_SECONDS:
            return self.models

        if not self.models and self.cache_path.exists():
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.fetched_at = cached.get("ts", 0)
            self.models = prepare(cached.get("data", []))
            if time.time() - self.fetched_at < TTL_SECONDS:
                return self.models

        await self._fetch()
        return self.models

    async def _fetch(self) -> None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(CATALOG_URL, timeout=30) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
        except Exception as e:
            self.error = str(e)
            logger.warning(
                "SoL-Astr：拉取 OpenRouter 目录失败（%s），价格功能不可用。", e
            )
            return

        data = payload.get("data", [])
        self.fetched_at = time.time()
        self.models = prepare(data)
        self.error = ""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps({"ts": self.fetched_at, "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("SoL-Astr：已加载 %d 个模型的价目。", len(self.models))
