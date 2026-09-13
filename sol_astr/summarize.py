"""调用压缩模型把归档段压成一段摘要。

包裹方式与内置 LLMSummaryCompressor 保持一致（compressor.py:280-290），这样同一条
「上下文压缩提示词」在内置压缩和本插件里表现一样。
"""

from __future__ import annotations

from astrbot.core.agent.message import Message
from astrbot.core.provider.modalities import (
    log_context_sanitize_stats,
    sanitize_contexts_by_modalities,
)
from astrbot.core.provider.provider import Provider

DEFAULT_INSTRUCTION = (
    "1. 聊过哪些话题、各自聊出什么结论，一个个说清楚，最后停在哪个话题上也点一下。\n"
    "2. 用户提过的个人信息、喜好、约定、怎么称呼，都留着。\n"
    "3. 读过的文件、链接、资料，把路径和用途列出来。\n"
    "4. 还没办完的事，写清楚进度和下一步。\n"
    "5. 里面已经是摘要的部分，合并进来就行，别重复说一遍。\n"
    "6. 用用户的语言写。"
)

TASK_CONTINUATION_INSTRUCTION = (
    "If a task appears to be in progress, end the summary with the latest "
    "known result and the concrete next step to continue the task."
)


def build_prompt(instruction: str) -> str:
    """把摘要要求包成一条 user 指令。"""
    return (
        "Generate a summary of our previous conversation history.\n"
        f"<extra_instruction>\n{instruction}\n\n"
        f"{TASK_CONTINUATION_INSTRUCTION}</extra_instruction>\n"
        "Respond ONLY with the summary content, without any additional text or formatting."
    )


async def summarize(
    provider: Provider,
    archive: list[Message],
    instruction: str,
) -> tuple[str, object]:
    """生成摘要，返回 (摘要正文, usage)。摘要为空时正文为空字符串。"""
    contexts = list(archive)
    if contexts[-1].role != "assistant":
        contexts.append(Message(role="assistant", content="Acknowledged."))
    contexts.append(Message(role="user", content=build_prompt(instruction)))

    sanitized, stats = sanitize_contexts_by_modalities(
        contexts,
        provider.provider_config.get("modalities", None),
    )
    log_context_sanitize_stats(stats)

    response = await provider.text_chat(contexts=sanitized)
    return (response.completion_text or "").strip(), response.usage
