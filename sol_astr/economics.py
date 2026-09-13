"""在压缩线上验证划不划算。

压缩线的位置由窗口定（窗口 × threshold_ratio），价格不管线画在哪，只负责验收：

    一次性成本 C = (K+M)(输入价−缓存读价) + (K+M)缓存写价 + A×摘要输入价 + M×摘要输出价
    之后每轮省 S = A × 缓存读价
    回本轮数 = C / S

回本轮数随归档段 A 单调下降（固定成本被摊薄），所以线画得高在经济上只会更划算，
它真正的代价是：话题结束得早、没到线的对话一次都压不到。

N×缓存读价 ≤ 摘要输入价 时，无论归档段多大都不可能在 N 轮内回本。
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
    archive: int,
    target_turns: int,
    memo_tokens: int = MEMO_TOKENS,
) -> SweetSpot:
    """在给定归档段上算回本轮数，并对照 target_turns 验收。"""
    pi, pw = main["input"], main["cache_write"]
    pc = effective_cache_read(main)
    si, so = summarizer["input"], summarizer["output"]

    one_time = (
        (keep_recent + memo_tokens) * (pi - pc + pw)
        + archive * si
        + memo_tokens * so
    )
    saving = archive * pc
    breakeven = one_time / saving if saving > 0 else 0.0

    detail = {
        "main_input": pi,
        "main_cache_read": pc,
        "main_cache_write": pw,
        "summarizer_input": si,
        "summarizer_output": so,
        "memo_tokens": memo_tokens,
        "target_turns": target_turns,
        "archive": archive,
    }

    if target_turns * pc <= si:
        return SweetSpot(
            keep_recent=keep_recent,
            min_archive=archive,
            solvable=False,
            reason=(
                f"摘要模型读一遍归档段的单价（${si * 1e6:.2f}/1M）不低于 {target_turns} 轮"
                f"省下的（${target_turns * pc * 1e6:.2f}/1M），归档段多大都回不了本。"
                "请在核心配置里把「用于上下文压缩的模型提供商 ID」换成更便宜的模型。"
            ),
            breakeven_turns=breakeven,
            per_round_saving=saving,
            one_time_cost=one_time,
            detail=detail,
        )

    if breakeven > target_turns:
        return SweetSpot(
            keep_recent=keep_recent,
            min_archive=archive,
            solvable=False,
            reason=(
                f"在这条线上压一次约 ${one_time:.4f}，之后每轮省 ${saving:.4f}，"
                f"回本要 {breakeven:.1f} 次请求，超过目标的 {target_turns} 次。"
                "把目标回本轮数调大，或换更便宜的压缩模型。"
            ),
            breakeven_turns=breakeven,
            per_round_saving=saving,
            one_time_cost=one_time,
            detail=detail,
        )

    return SweetSpot(
        keep_recent=keep_recent,
        min_archive=archive,
        breakeven_turns=breakeven,
        per_round_saving=saving,
        one_time_cost=one_time,
        detail=detail,
    )
