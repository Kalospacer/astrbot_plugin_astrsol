"""SoL-Astr：让 LLM 在话题边界自主压缩旧上下文。"""

from __future__ import annotations

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart, bind_checkpoint_messages
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext

from .sol_astr.split import count_tokens, strip_tool_trace
from .sol_astr.summarize import DEFAULT_INSTRUCTION
from .sol_astr.tool import COMPACTED_FLAG, CompactContextTool


class SoLAstr(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.config = config
        self.ledger_path = StarTools.get_data_dir("astrbot_plugin_astrsol") / "ledger.jsonl"
        self.tool = CompactContextTool(plugin=self)
        self.enabled = False

    async def initialize(self) -> None:
        if not self.config["enable_compact"]:
            logger.info("SoL-Astr 未启用。")
            return

        compression = self._compression_config()
        if compression.get("max_turns", -1) != -1:
            logger.warning(
                "SoL-Astr：核心配置开启了按轮截断（max_turns=%s），它会在压缩之前丢掉旧消息，"
                "与本插件相互干扰，建议改回 -1。",
                compression.get("max_turns"),
            )
        if not compression.get("provider_id", "").strip():
            logger.warning(
                "SoL-Astr：核心配置没填「用于上下文压缩的模型提供商 ID」，摘要会用当前聊天模型，"
                "既慢又贵。建议指一个便宜的模型。",
            )

        self.context.add_llm_tools(self.tool)
        self.enabled = True
        logger.info(
            "SoL-Astr 已启用，摘要模型：%s，压缩提示词来自%s。",
            compression.get("provider_id") or "当前聊天模型",
            "核心配置" if compression.get("instruction", "").strip() else "插件默认",
        )

    def _compression_config(self) -> dict:
        """核心的「上下文管理策略」配置块。

        摘要模型和压缩提示词都从这里取，不在插件里另开一套：用户在 WebUI 配一次，
        内置压缩和本插件用的是同一份。
        """
        return (
            self.context.get_config()
            .get("agent_runner", {})
            .get("config", {})
            .get("compression", {})
        )

    def summarize_instruction(self) -> str:
        """核心配的「上下文压缩提示词」，没填才用插件默认。"""
        configured = self._compression_config().get("instruction", "").strip()
        return configured or DEFAULT_INSTRUCTION

    async def summarize_provider(self, event: AstrMessageEvent):
        """核心配的压缩模型，没配就回落当前会话的聊天模型。

        回落行为与内置压缩器一致（astr_main_agent.py:1331-1345）。
        """
        provider_id = self._compression_config().get("provider_id", "").strip()
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is not None:
                return provider
            logger.warning(
                "SoL-Astr：压缩模型 %s 不可用，回落当前聊天模型。", provider_id
            )
        return await self.context.get_using_provider_async(umo=event.unified_msg_origin)

    @filter.on_llm_request()
    async def inject_context_size(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在当轮用户消息尾部追加一行上下文规模，供模型判断该不该压缩。

        放在消息尾部而不是 system prompt，是为了不破坏上游的前缀缓存；这一行会随
        历史落盘，不要剥离，否则下一轮的前缀又和缓存对不上了。
        """
        if not self.enabled:
            return
        tokens = req.conversation.token_usage if req.conversation else 0
        if not tokens:
            tokens = count_tokens(bind_checkpoint_messages(req.contexts))
        req.extra_user_content_parts.append(
            TextPart(text=f"[context: {tokens} tokens]")
        )

    @filter.on_agent_done()
    async def drop_tool_trace(
        self,
        event: AstrMessageEvent,
        run_context: ContextWrapper[AstrAgentContext],
        response: LLMResponse,
    ) -> None:
        """压缩成功的那一轮，落盘前摘掉本插件的工具调用痕迹。

        这一轮的前缀本来就因为压缩断掉了，删这对消息不额外花钱；留着它反而会当成
        样例，诱导模型下一轮接着调。没压缩的轮次不动，那句「不需要压缩」正好是
        告诉模型别急着重试的信号。
        """
        if not event.get_extra(COMPACTED_FLAG):
            return
        event.set_extra(COMPACTED_FLAG, False)
        if not self.config["strip_tool_trace"]:
            return
        removed = strip_tool_trace(run_context.messages)
        logger.info("SoL-Astr：已剥离 %d 条压缩工具消息。", removed)
