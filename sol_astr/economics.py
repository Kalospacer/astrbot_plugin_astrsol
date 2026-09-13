"""按真实价目算压缩的甜点值。

保留段决定对话质量，不归价格管；归档段决定划不划算，由价格反推：

    每轮省 = A × 缓存读价
    一次性 = (K+M)(输入价−缓存读价) + (K+M)×缓存写价 + A×摘要输入价 + M×摘要输出价

    要求 breakeven ≤ N ⇒
    A ≥ [(K+M)(Pi−Pc) + (K+M)Pw + M·So] / (N·Pc − Si)

分母 ≤ 0 表示摘要模型读一遍归档段的钱比 N 轮省下的还多，这套组合永远不回本。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MEMO_TOKENS = 800


@dataclass
class SweetSpot:
    keep_recent: int
    min_archive: int = 0
    breakeven_turns: float = 0.0
    solvable: bool = True
    reason: str = ""
    per_round_saving: float = 0.0
    one_time_cost: float = 0.0
    detail: dict = field(default_factory=dict)

    @property
    def threshold(self) -> int:
        return self.keep_recent + self.min_archive


def effective_cache_read(model: dict) -> float:
    """没有缓存折扣的模型，省下的就是全价输入。"""
    return model["cache_read"] or model["input"]


def sweet_spot(
    main: dict,
    summarizer: dict,
    keep_recent: int,
    target_turns: int,
    memo_tokens: int = MEMO_TOKENS,
) -> SweetSpot:
    """算出让 breakeven ≤ target_turns 的最小归档段。"""
    pi, pw = main["input"], main["cache_write"]
    pc = effective_cache_read(main)
    si, so = summarizer["input"], summarizer["output"]

    one_time = (keep_recent + memo_tokens) * (pi - pc + pw) + memo_tokens * so
    denominator = target_turns * pc - si

    detail = {
        "main_input": pi,
        "main_cache_read": pc,
        "main_cache_write": pw,
        "summarizer_input": si,
        "summarizer_output": so,
        "memo_tokens": memo_tokens,
        "target_turns": target_turns,
    }

    if denominator <= 0:
        return SweetSpot(
            keep_recent=keep_recent,
            solvable=False,
            reason=(
                f"摘要模型读一遍归档段的单价（${si * 1e6:.2f}/1M）不低于 {target_turns} 轮"
                f"省下的（${target_turns * pc * 1e6:.2f}/1M），这套组合永远不回本。"
                "请在核心配置里把「用于上下文压缩的模型提供商 ID」换成更便宜的模型。"
            ),
            one_time_cost=one_time,
            detail=detail,
        )

    min_archive = int(one_time / denominator)
    return SweetSpot(
        keep_recent=keep_recent,
        min_archive=min_archive,
        breakeven_turns=float(target_turns),
        per_round_saving=min_archive * pc,
        one_time_cost=one_time,
        detail=detail,
    )


def breakeven_for(
    main: dict,
    summarizer: dict,
    keep_recent: int,
    archive: int,
    memo_tokens: int = MEMO_TOKENS,
) -> float:
    """给定归档段，反过来算要几轮回本。无法回本时返回 0。"""
    pi, pw = main["input"], main["cache_write"]
    pc = effective_cache_read(main)
    si, so = summarizer["input"], summarizer["output"]

    saving = archive * pc - archive * si
    if saving <= 0:
        return 0.0
    one_time = (keep_recent + memo_tokens) * (pi - pc + pw) + memo_tokens * so
    return one_time / (archive * pc)
