"""整轮切分、上下文重写与工具痕迹剥离。"""

from __future__ import annotations

from astrbot.core.agent.context.round_utils import split_into_rounds
from astrbot.core.agent.context.token_counter import EstimateTokenCounter
from astrbot.core.agent.message import Message

TOOL_NAME = "sol_astr_compact"
COMPACT_MARK = "[sol-astr compact]"

_counter = EstimateTokenCounter()


def count_tokens(messages: list[Message]) -> int:
    """估算一组消息的 token 数。"""
    return _counter.count_tokens(messages)


def leading_system(messages: list[Message]) -> list[Message]:
    """取开头连续的 system 消息。落盘时只有第一条会被跳过，其余照常保留。"""
    head: list[Message] = []
    for message in messages:
        if message.role != "system":
            break
        head.append(message)
    return head


def split_for_compact(
    messages: list[Message],
    keep_recent_tokens: int,
) -> tuple[list[Message], list[Message]]:
    """把消息切成 (归档段, 保留段)。

    切分以整轮为粒度：一轮从 user 开始，含其后的 assistant/tool 消息。最近一轮
    （即当前用户消息所在轮）始终保留，此外从后往前累加到 keep_recent_tokens 为止。
    开头的 system 消息不属于任何一段，由调用方单独处理。
    """
    head = leading_system(messages)
    body = messages[len(head) :]
    rounds = [
        [seg for seg in rnd if isinstance(seg, Message)]
        for rnd in split_into_rounds(body)
    ]
    if len(rounds) < 2:
        return [], body

    used = count_tokens(rounds[-1])
    keep_start = len(rounds) - 1
    for index in range(len(rounds) - 2, -1, -1):
        round_tokens = count_tokens(rounds[index])
        if used + round_tokens > keep_recent_tokens:
            break
        used += round_tokens
        keep_start = index

    archive = [msg for rnd in rounds[:keep_start] for msg in rnd]
    keep = [msg for rnd in rounds[keep_start:] for msg in rnd]
    return archive, keep


def rewrite(
    messages: list[Message],
    head: list[Message],
    memo: str,
    keep: list[Message],
) -> list[Message]:
    """就地把 messages 换成 system 头 + 摘要对 + 保留段，并返回新列表。"""
    rewritten = [
        *head,
        Message(
            role="user",
            content=f"{COMPACT_MARK} 早前对话的摘要：\n{memo}",
        ),
        Message(role="assistant", content="好，早前对话我记住了。"),
        *keep,
    ]
    messages[:] = rewritten
    return rewritten


def strip_tool_trace(messages: list[Message]) -> int:
    """删除本插件工具的 tool_call 与对应 tool 结果，返回删除的消息条数。

    assistant 消息可能同时携带其他工具的调用，只摘掉属于本工具的那些；
    若摘完后该 assistant 既无内容也无其他调用，则整条删除。
    """
    orphan_ids: set[str] = set()
    survivors: list[Message] = []
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            kept_calls = []
            for call in message.tool_calls:
                name, call_id = _tool_call_identity(call)
                if name == TOOL_NAME:
                    if call_id:
                        orphan_ids.add(call_id)
                else:
                    kept_calls.append(call)
            if len(kept_calls) != len(message.tool_calls):
                message.tool_calls = kept_calls or None
                if not kept_calls and not message.content:
                    continue
        elif message.role == "tool" and message.tool_call_id in orphan_ids:
            continue
        survivors.append(message)

    removed = len(messages) - len(survivors)
    messages[:] = survivors
    return removed


def _tool_call_identity(call) -> tuple[str, str]:
    """从 ToolCall 或其 dict 形态里取出 (工具名, 调用 id)。"""
    if isinstance(call, dict):
        return str(call.get("function", {}).get("name", "")), str(call.get("id", ""))
    return str(call.function.name), str(call.id)
