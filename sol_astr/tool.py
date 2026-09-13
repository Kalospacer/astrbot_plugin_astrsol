"""压缩上下文的 LLM 工具。

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
from .split import (
    TOOL_NAME,
    count_tokens,
    leading_system,
    rewrite,
    split_for_compact,
)
from .summarize import summarize

COMPACTED_FLAG = "sol_astr_compacted"
CALLED_FLAG = "sol_astr_called"

DESCRIPTION = (
    "把早前聊过的内容收成一段摘要，腾出上下文。"
    "用户消息尾部的 context 提示里有三个数：当前用量、压缩线、模型上限。"
    "压缩线按省钱划不划算定，比上限低不少。"
    "用量过了压缩线，而且刚才那个话题确实翻篇了、也没留下什么没办完的事，就调用。"
    "还在聊同一件事，或者没过线，就跳过。"
    "调完你会拿到一段早前对话的摘要，接着回答用户。别跟用户提这个工具。"
)


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
        config = self.plugin.config
        event = context.context.event
        messages = context.messages
        event.set_extra(CALLED_FLAG, True)

        limits = await self.plugin.thresholds(event.unified_msg_origin)
        keep_recent, min_archive = limits["keep_recent"], limits["min_archive"]
        head = leading_system(messages)
        archive, keep = split_for_compact(messages, keep_recent)
        record = {
            "umo": event.unified_msg_origin,
            "cid": self._conversation_id(event),
            "total_tokens": count_tokens(messages),
            "archive_tokens": count_tokens(archive),
            "keep_tokens": count_tokens(keep),
            "archive_messages": len(archive),
            "keep_messages": len(keep),
            "keep_recent": keep_recent,
            "min_archive": min_archive,
            "window": limits["window"],
            "archive_source": limits["archive_source"],
        }

        if record["archive_tokens"] < min_archive:
            return self._finish(
                record,
                "too_small",
                started,
                "早前的内容还不多，先不压，接着回答用户。",
            )

        if config["dry_run"]:
            return self._finish(
                record, "dry_run", started, "记下了，这次先不动，接着回答用户。"
            )

        provider = await self.plugin.summarize_provider(event.unified_msg_origin)
        memo, usage = await summarize(
            provider, archive, self.plugin.summarize_instruction()
        )
        if not memo:
            logger.warning(
                "SoL-Astr: summarizer returned an empty memo, skipping compaction"
            )
            return self._finish(
                record, "empty_summary", started, "这次没压成，接着回答用户。"
            )

        rewrite(messages, head, memo, keep)
        event.set_extra(COMPACTED_FLAG, True)
        record["memo_tokens"] = int(len(memo) * 0.6)
        if usage:
            record["summarizer_input_other"] = usage.input_other
            record["summarizer_input_cached"] = usage.input_cached
            record["summarizer_output"] = usage.output
        return self._finish(
            record, "compacted", started, "早前的对话收成摘要了，接着回答用户。"
        )

    def _finish(self, record: dict, outcome: str, started: float, reply: str) -> str:
        record["outcome"] = outcome
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        ledger.append(self.plugin.ledger_path, record)
        return reply

    @staticmethod
    def _conversation_id(event) -> str:
        req = event.get_extra("provider_request")
        conversation = getattr(req, "conversation", None)
        return getattr(conversation, "cid", "") or ""
