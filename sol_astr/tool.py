"""压缩上下文的 LLM 工具。

工具被调用 = 一次话题边界（对应上游 plan 步骤完成）。这里先判账再动手：
不划算直接退回原因，一分钱不花；划算才调摘要模型。判账本身不做任何
LLM 调用，所以「调错了」的代价是零。

用 FunctionTool 子类而不是 @filter.llm_tool：只有重写 call() 的工具才拿得到
ContextWrapper，也就是 agent 循环正在用的 run_context.messages。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from . import ledger
from .economics import MEMO_ESTIMATE, decide
from .split import (
    TOOL_NAME,
    count_tokens,
    leading_system,
    rewrite,
    split_for_compact,
)
from .state import average_increment
from .summarize import summarize

COMPACTED_FLAG = "sol_astr_compacted"
CALLED_FLAG = "sol_astr_called"

DESCRIPTION = (
    "一个话题聊完、彻底翻篇了，就调用我把早前的记录收拾成摘要。"
    "我会先算一笔账：现在压划算就压；不划算就原样退回并告诉你原因，"
    "不会动任何内容。算账本身不花钱，放心调。"
    "还在聊同一件事，或者还有没办完的事，就别调。"
    "用户消息尾部的 context 提示越过了窗口保护线时，必须先调我再回答。"
    "别跟用户提这个工具。"
)

# 各退回原因给模型的说法。判账细节在台账和面板里，给模型的只要一句能指导
# 下一步行为的话。
REPLIES = {
    "non_positive_saving": "早前可压的内容还太少，压不出节省，先不压。接着回答用户。",
    "horizon_unavailable": "还没摸清这段对话的推进速度，估不出后面还能聊几轮，"
    "这次先不压。下个话题结束时再调我。",
    "price_unavailable": "拿不到模型价目，算不了经济账，先不压。接着回答用户。",
    "deferred_economic": "现在压不划算：打平需要的轮数比估计还能聊的多。"
    "继续聊，下个话题结束时再调我。",
    "deferred_subsequent_margin": "上次压过没多久，这次要打平的门槛更高，"
    "现在还够不着。继续聊，下个话题结束时再调我。",
    "deferred_carried_debt": "上一次压缩的成本还没省回来，现在再压是赔上加赔。"
    "继续聊，下个话题结束时再调我。",
    "empty_summary": "这次没压成，接着回答用户。",
    "dry_run": "记下了，这次先不动（影子模式），接着回答用户。",
    "compacted": "早前的对话收成摘要了，接着回答用户。",
}


@dataclass
class CompactContextTool(FunctionTool):
    name: str = TOOL_NAME
    description: str = DESCRIPTION
    parameters: dict = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    plugin: Any = None
    """注册本工具的 SoLAstr 实例，由 initialize() 注入。"""

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> str:
        started = time.monotonic()
        plugin = self.plugin
        event = context.context.event
        messages = context.messages
        event.set_extra(CALLED_FLAG, True)

        key = plugin.session_key(event)
        state = plugin.states.record_boundary(key)

        limits = await plugin.limits(event.unified_msg_origin)
        keep_recent = limits["keep_recent"]
        head = leading_system(messages)
        archive, keep = split_for_compact(messages, keep_recent)
        write_tokens = count_tokens(messages)
        archive_tokens = count_tokens(archive)

        main_entry, summ_entry = await plugin.priced_pair(event.unified_msg_origin)
        decision = decide(
            write_tokens=write_tokens,
            archive_tokens=archive_tokens,
            context_window=limits["window"],
            keep_recent=keep_recent,
            average_increment=average_increment(state),
            prior_compactions=state["compaction_count"],
            carried_debt=state["carried_debt"],
            debt_repayment=state["debt_repayment"],
            main=main_entry,
            summarizer=summ_entry,
        )

        record = {
            "umo": event.unified_msg_origin,
            "cid": self._conversation_id(event),
            "total_tokens": write_tokens,
            "archive_tokens": archive_tokens,
            "keep_tokens": count_tokens(keep),
            "archive_messages": len(archive),
            "keep_messages": len(keep),
            "keep_recent": keep_recent,
            "window": limits["window"],
            "reason": decision.reason,
            "window_protection": decision.window_protection,
            "breakeven_requests": decision.breakeven_requests,
            "horizon_requests": decision.horizon_requests,
            "one_time_cost": decision.one_time_cost,
            "per_request_saving": decision.per_request_saving,
            "carried_debt": decision.carried_debt,
        }

        if not decision.compact:
            return self._finish(record, decision.reason, started)

        if plugin.config["dry_run"]:
            return self._finish(record, "dry_run", started)

        provider = await plugin.summarize_provider(event.unified_msg_origin)
        memo, usage = await summarize(
            provider, archive, plugin.summarize_instruction()
        )
        if not memo:
            logger.warning(
                "SoL-Astr: summarizer returned an empty memo, skipping compaction"
            )
            return self._finish(record, "empty_summary", started)

        rewrite(messages, head, memo, keep)
        event.set_extra(COMPACTED_FLAG, True)
        record["memo_tokens"] = int(len(memo) * 0.6)
        if usage:
            record["summarizer_input_other"] = usage.input_other
            record["summarizer_input_cached"] = usage.input_cached
            record["summarizer_output"] = usage.output
        # 记债：这次的一次性成本，之后每轮按 per_request_saving 偿还
        plugin.states.record_compaction(
            key, decision.one_time_cost, decision.per_request_saving
        )
        return self._finish(record, "compacted", started)

    def _finish(self, record: dict, outcome: str, started: float) -> str:
        record["outcome"] = outcome
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        ledger.append(self.plugin.ledger_path, record)
        return REPLIES.get(outcome, REPLIES["deferred_economic"])

    @staticmethod
    def _conversation_id(event) -> str:
        req = event.get_extra("provider_request")
        conversation = getattr(req, "conversation", None)
        return getattr(conversation, "cid", "") or ""
