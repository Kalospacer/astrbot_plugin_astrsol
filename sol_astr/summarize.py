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
    "1. 逐个覆盖聊过的话题和各自的结论，并点明最后停在哪个话题上。\n"
    "2. 保留用户提到的个人信息、偏好、约定和称呼方式。\n"
    "3. 如果读过文件、链接或资料，列出它们的路径与作用。\n"
    "4. 如果有还没做完的事，写清当前进度和下一步。\n"
    "5. 已经是摘要的部分照常合并进来，不要重复叙述。\n"
    "6. 用用户的语言书写。"
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
