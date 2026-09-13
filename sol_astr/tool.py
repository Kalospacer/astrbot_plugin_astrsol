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
    "把早前的聊天记录压成摘要，腾出上下文。"
    "用户消息尾部的 context 提示会给出当前用量、建议压缩线和模型上限三个数；"
    "压缩线只是省成本的建议值，不是容量上限，过线不代表快装不下了。"
    "两个条件同时满足才调用：一是用户的话题已经转到别处、之前那个话题不再重要、"
    "也没有未完成的事；二是 context 提示显示当前用量已经过了压缩线。"
    "同一个话题的延续绝对不要调用，没过线时也不要调用。"
    "调用后你会看到一段早前对话的摘要，照常回答用户即可。不要向用户提起这个工具。"
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

        keep_recent, min_archive, window = await self.plugin.thresholds(event)
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
            "window": window,
        }

        if record["archive_tokens"] < min_archive:
            return self._finish(
                record,
                "too_small",
                started,
                "早前的内容还不多，不需要压缩，接着回答用户就好。",
            )

        if config["dry_run"]:
            return self._finish(
                record, "dry_run", started, "已记录本次压缩时机，接着回答用户就好。"
            )

        provider = await self.plugin.summarize_provider(event)
        memo, usage = await summarize(
            provider, archive, self.plugin.summarize_instruction()
        )
        if not memo:
            logger.warning(
                "SoL-Astr: summarizer returned an empty memo, skipping compaction"
            )
            return self._finish(
                record, "empty_summary", started, "这次没能压缩，接着回答用户就好。"
            )

        rewrite(messages, head, memo, keep)
        event.set_extra(COMPACTED_FLAG, True)
        record["memo_tokens"] = int(len(memo) * 0.6)
        if usage:
            record["summarizer_input_other"] = usage.input_other
            record["summarizer_input_cached"] = usage.input_cached
            record["summarizer_output"] = usage.output
        return self._finish(
            record, "compacted", started, "早前的对话已经压成摘要了，照常回答用户。"
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
