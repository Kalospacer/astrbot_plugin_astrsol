"""压缩的经济账：两条烧钱曲线比谁缓。

钱花出去不会回来，没有什么"回本"：

- 不压：每轮按缓存读价重读整个上下文，越聊越贵
- 压：一次性付 C = (K+M)(Pi−Pc+Pw) + A·Si + M·So，之后每轮少花 S = A×Pc

聊到第 C/S 轮，两条累计曲线打平；再往后压的那条一直更低。C/S 随归档段 A
单调下降（固定成本被摊薄），所以高的线在经济上只会更划算，它的代价是：
话题早早结束、没到线的对话一次都压不到，那些轮次一直在按全价缓存读烧钱。

经济线 = 让 "N 轮净省 > 0" 的最小归档段，N 是 target_turns（估计话题结束后
还会再聊几轮）。低于经济线压缩是赔钱买卖，所以它是许可线的硬下限。
N×Pc ≤ Si 时经济线不存在：摘要模型读一遍比 N 轮省下的还贵，压多少次都赔。
"""

from __future__ import annotations

from dataclasses import dataclass, field

MEMO_TOKENS = 800


@dataclass
class SweetSpot:
    keep_recent: int
    min_archive: int = 0
    breakeven_turns: float = 0.0  # 打平点：第几轮两条成本线相交
    solvable: bool = True
    reason: str = ""
    per_round_saving: float = 0.0
    one_time_cost: float = 0.0
    floor_archive: int = 0  # 经济线：低于它的压缩是赔钱买卖
    net_saving: float = 0.0  # N 轮净省，为负就是赔
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
    """在给定归档段上算经济账：打平点、N 轮净省、经济线。"""
    pi, pw = main["input"], main["cache_write"]
    pc = effective_cache_read(main)
    si, so = summarizer["input"], summarizer["output"]

    fixed = (keep_recent + memo_tokens) * (pi - pc + pw) + memo_tokens * so
    one_time = fixed + archive * si
    saving = archive * pc
    crossover = one_time / saving if saving > 0 else 0.0
    net = target_turns * saving - one_time

    floor = 0
    if target_turns * pc > si:
        floor = int(fixed / (target_turns * pc - si)) + 1

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

    spot = SweetSpot(
        keep_recent=keep_recent,
        min_archive=archive,
        breakeven_turns=crossover,
        per_round_saving=saving,
        one_time_cost=one_time,
        floor_archive=floor,
        net_saving=net,
        detail=detail,
    )

    if target_turns * pc <= si:
        spot.solvable = False
        spot.reason = (
            f"摘要模型读一遍归档段的单价（${si * 1e6:.2f}/1M）不低于 {target_turns} 轮"
            f"少花的（${target_turns * pc * 1e6:.2f}/1M），归档段多大都是赔钱。"
            "请在核心配置里把「用于上下文压缩的模型提供商 ID」换成更便宜的模型。"
        )
    elif net <= 0 and (not floor or archive < floor):
        spot.solvable = False
        spot.reason = (
            f"在这条线上按 {target_turns} 轮算，压缩比不压还贵 ${-net:.4f}。"
            "把压缩线调高（归档段越大，固定成本摊得越薄），或换更便宜的压缩模型。"
        )
    return spot
