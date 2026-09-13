"""压缩判账：SoL-Pi online-context-compact 的移植。

上游源码：NVlabs/SoL-Pi src/sol-pi/extensions/online-context-compact/economics.ts
（项目页 https://nvlabs.github.io/SoL-Pi/ ，没有论文，算法以源码为准）。

与上游一一对应：

- 没有"压缩线"。每次话题边界现场算一笔账：打平轮数 = 一次性成本 ÷ 每轮少花；
  打平轮数 ≤ 估计还能聊的轮数，才压。估计轮数用「窗口还装得下几轮」封顶
  （estimateRemainingRequests 的 windowRequestUpperBound）。
- 窗口保护线 = 窗口 − 16384（windowReserveTokens）：过线无条件压，不算经济账。
- 首次压缩视野 ×2（firstCompactionRequestScale），后续压缩更严：打平轮数
  ×1.5（subsequentCompactionMargin）仍 ≤ 估计轮数，且带上前一次没还完的债
  合并计算仍 ≤ 估计轮数。
- 债：一次压缩的一次性成本记为债，之后每轮按「每轮少花」偿还；没还完又压，
  合并门槛卡你（combinedBreakevenRequests）。

两处适配（与上游不同的地方，都在这里，没有第三处）：

1. 上游的剩余轮数估计 = 剩余计划步数 × 每步实测轮数，因为它有 plan 工具。
   聊天没有计划，改用窗口封顶值本身：horizon = (窗口 − 当前) ÷ 每轮平均增量，
   增量从本会话历史实测；实测不出来就是 horizon_unavailable，不压。
   首次压缩的 ×2 因此恒被窗口封顶吃掉（min(2H, H) = H），公式保留，语义如实。
2. 上游用原生压缩，一次性成本只有缓存重写溢价。本插件的压缩是一次真实的
   摘要模型调用，一次性成本加上：归档段按摘要模型输入价读一遍 + memo 按
   摘要模型输出价写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 上游 DEFAULT_NATIVE_SUMMARY_TOKEN_ESTIMATE
MEMO_ESTIMATE = 1000
# 上游 windowReserveTokens
WINDOW_RESERVE = 16384
# 上游 firstCompactionRequestScale / subsequentCompactionMargin
FIRST_HORIZON_SCALE = 2.0
SUBSEQUENT_MARGIN = 1.5

# 窗口保护线触发：当前用量 ≥ 窗口 − WINDOW_RESERVE（decideCompaction 的
# windowProtection 分支），聊天里模型可能始终不调工具，所以钩子在每个
# provider 请求前也检查这条线并下发强制指令。


@dataclass
class Decision:
    compact: bool
    reason: str  # 与上游 CompactionReason 对齐，外加 price_unavailable
    window_protection: bool = False
    breakeven_requests: float | None = None
    horizon_requests: int | None = None
    effective_horizon: float | None = None
    combined_breakeven: float | None = None
    one_time_cost: float = 0.0
    per_request_saving: float = 0.0
    saving_tokens: int = 0
    carried_debt: float = 0.0
    detail: dict = field(default_factory=dict)


def effective_cache_read(model: dict) -> float:
    """没有缓存折扣的模型，省下的就是全价输入。"""
    return model["cache_read"] or model["input"]


def one_time_cost(
    main: dict,
    summarizer: dict,
    keep_recent: int,
    archive: int,
    memo: int = MEMO_ESTIMATE,
) -> float:
    """压一次要花的钱：重建保留段+memo 的缓存溢价 + 摘要调用本身。

    重建项 = (K+M)(Pi−Pc+Pw)：新前缀先按输入价写缓存，换掉了本来可以
    按缓存读价命中的旧前缀。适配点 2：上游没有 A·Si 和 M·So 这两项。
    """
    pi, pw = main["input"], main["cache_write"]
    pc = effective_cache_read(main)
    si, so = summarizer["input"], summarizer["output"]
    return (keep_recent + memo) * (pi - pc + pw) + memo * so + archive * si


def per_request_saving(main: dict, archive: int, memo: int = MEMO_ESTIMATE) -> float:
    """压完之后每轮少花的钱：归档段与 memo 的差值不再按缓存读价重读。

    对应上游 savingTokens = archiveTokens − memoTokens，换成钱。
    """
    return max(0, archive - memo) * effective_cache_read(main)


def decide(
    *,
    write_tokens: int,
    archive_tokens: int,
    context_window: int,
    keep_recent: int,
    average_increment: float | None,
    prior_compactions: int,
    carried_debt: float,
    debt_repayment: float,
    main: dict | None,
    summarizer: dict | None,
    memo: int = MEMO_ESTIMATE,
) -> Decision:
    """在一次话题边界上判账：压，还是不压。

    参数全部是现场实测或价目查表，没有一个需要用户填。
    """
    saving_tokens = archive_tokens - memo
    protection = context_window > 0 and write_tokens >= context_window - WINDOW_RESERVE

    if saving_tokens <= 0:
        return Decision(
            compact=False,
            reason="non_positive_saving",
            window_protection=protection,
            saving_tokens=saving_tokens,
            carried_debt=carried_debt,
        )

    horizon = None
    if average_increment and average_increment > 0 and context_window > 0:
        horizon = max(
            0, int((context_window - write_tokens) / average_increment)
        )

    if not (main and summarizer):
        # 对应上游 cache_ratio_unavailable：没有价目，经济判断缺席，
        # 只剩窗口保护线能压。
        return Decision(
            compact=protection,
            reason="window_protection" if protection else "price_unavailable",
            window_protection=protection,
            horizon_requests=horizon,
            saving_tokens=saving_tokens,
            carried_debt=carried_debt,
        )

    cost = one_time_cost(main, summarizer, keep_recent, archive_tokens, memo)
    saving = per_request_saving(main, archive_tokens, memo)
    breakeven = cost / saving if saving > 0 else None

    base = Decision(
        compact=False,
        reason="",
        window_protection=protection,
        breakeven_requests=breakeven,
        horizon_requests=horizon,
        one_time_cost=cost,
        per_request_saving=saving,
        saving_tokens=saving_tokens,
        carried_debt=carried_debt,
    )

    if protection:
        base.compact = True
        base.reason = "window_protection"
        return base
    if horizon is None:
        base.reason = "horizon_unavailable"
        return base
    if breakeven is None:
        base.reason = "non_positive_saving"
        return base

    first = prior_compactions == 0
    if first:
        # min(horizon × FIRST_HORIZON_SCALE, horizon) = horizon：聊天里没有
        # 计划边界估计，视野就是窗口封顶本身，×2 恒被吃掉。保留公式出处。
        effective = min(horizon * FIRST_HORIZON_SCALE, horizon)
        base.effective_horizon = effective
        if breakeven <= effective:
            base.compact = True
            base.reason = "economic"
        else:
            base.reason = "deferred_economic"
        return base

    base.effective_horizon = horizon
    base_ok = breakeven <= horizon
    margin_ok = breakeven * SUBSEQUENT_MARGIN <= horizon
    combined = (carried_debt + cost) / saving if saving > 0 else None
    base.combined_breakeven = combined
    debt_ok = combined is not None and combined <= horizon

    if base_ok and margin_ok and debt_ok:
        base.compact = True
        base.reason = "economic"
    elif base_ok and not margin_ok:
        base.reason = "deferred_subsequent_margin"
    elif base_ok and not debt_ok:
        base.reason = "deferred_carried_debt"
    else:
        base.reason = "deferred_economic"
    return base
