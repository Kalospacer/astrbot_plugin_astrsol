"""每个会话一份判账状态，落盘 state.json，写穿。

对应上游 state.ts 的 OnlineState：请求计数、context 增量统计（学每轮平均
增速）、边界间隔历史、压缩次数、携带债。聊天里没有 CORRECTION 信号，
recordCorrection 不移植。
"""

from __future__ import annotations

import json
from pathlib import Path

from astrbot.api import logger

MAX_BOUNDARY_SAMPLES = 64

_FIELDS = {
    "request_count": 0,
    "last_context_tokens": None,
    "pos_delta_total": 0,
    "pos_delta_count": 0,
    "boundary_counts": [],
    "last_boundary_request_count": 0,
    "compaction_count": 0,
    "carried_debt": 0.0,
    "debt_repayment": 0.0,
}


class SessionStates:
    """data_dir/state.json 的薄封装：按会话 key 取状态 dict，改完即写盘。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._states: dict[str, dict] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._states = {
                        str(k): {**_FIELDS, **v}
                        for k, v in raw.items()
                        if isinstance(v, dict)
                    }
            except (OSError, ValueError) as exc:
                logger.warning("SoL-Astr：state.json 读取失败，按空状态起步：%s", exc)

    def get(self, key: str) -> dict:
        return self._states.setdefault(key, dict(_FIELDS, boundary_counts=[]))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._states, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("SoL-Astr：state.json 写盘失败：%s", exc)

    # ---------- 三个状态变迁，与上游一一对应 ----------

    def record_request(self, key: str, context_tokens: int) -> dict:
        """上游 recordProviderRequest：每个 provider 请求记一次。

        顺带按「每轮少花」偿还上一笔压缩债。
        """
        st = self.get(key)
        last = st["last_context_tokens"]
        delta = context_tokens - last if isinstance(last, int) else 0
        if delta > 0:
            st["pos_delta_total"] += delta
            st["pos_delta_count"] += 1
        st["request_count"] += 1
        st["last_context_tokens"] = context_tokens
        st["carried_debt"] = max(0.0, st["carried_debt"] - st["debt_repayment"])
        if st["carried_debt"] == 0:
            st["debt_repayment"] = 0.0
        self.save()
        return st

    def record_boundary(self, key: str) -> dict:
        """上游 recordBoundary：一次话题边界（工具被调用）记一次间隔。"""
        st = self.get(key)
        interval = max(0, st["request_count"] - st["last_boundary_request_count"])
        st["boundary_counts"] = (st["boundary_counts"] + [interval])[
            -MAX_BOUNDARY_SAMPLES:
        ]
        st["last_boundary_request_count"] = st["request_count"]
        self.save()
        return st

    def record_compaction(self, key: str, debt: float, repayment: float) -> dict:
        """上游 recordCompaction：压缩成功后记债，增量统计清零重学。"""
        st = self.get(key)
        st["compaction_count"] += 1
        st["carried_debt"] = max(0.0, debt)
        st["debt_repayment"] = max(0.0, repayment)
        st["last_context_tokens"] = None
        st["pos_delta_total"] = 0
        st["pos_delta_count"] = 0
        self.save()
        return st


def average_increment(st: dict) -> float | None:
    """每轮平均 context 增量，实测不出来就是 None（horizon_unavailable）。"""
    if not st["pos_delta_count"]:
        return None
    return st["pos_delta_total"] / st["pos_delta_count"]
