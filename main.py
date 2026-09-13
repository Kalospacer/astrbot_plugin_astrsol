"""SoL-Astr：让 LLM 在话题边界自主压缩旧上下文。"""

from __future__ import annotations

from quart import jsonify, request

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart, bind_checkpoint_messages
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.utils.llm_metadata import LLM_METADATAS

from .sol_astr import api, ledger
from .sol_astr.catalog import Catalog, resolve
from .sol_astr.economics import sweet_spot
from .sol_astr.split import TOOL_NAME, count_tokens, strip_tool_trace
from .sol_astr.summarize import DEFAULT_INSTRUCTION
from .sol_astr.tool import CALLED_FLAG, COMPACTED_FLAG, CompactContextTool

PLUGIN_NAME = "astrbot_plugin_astrsol"
ELIGIBLE_FLAG = "sol_astr_eligible"

# 没有价目时的回落阈值：按窗口取比例，再用绝对值封顶。比例保证小窗口模型也够得着
# （否则绝对阈值就是死值），封顶保证大窗口不会留着 150k 不压——那时压缩等于没压。
KEEP_RATIO, KEEP_CAP = 0.15, 40000
ARCHIVE_RATIO, ARCHIVE_CAP = 0.25, 60000


class SoLAstr(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.config = config
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.ledger_path = self.data_dir / "ledger.jsonl"
        self.catalog = Catalog(self.data_dir / "openrouter.json")
        self.tool = CompactContextTool(plugin=self)
        self.enabled = False
        self.window_source: dict[str, str] = {}
        self._model_cache: dict[str, dict | None] = {}
        self._spot_cache: dict[tuple, object] = {}

    async def initialize(self) -> None:
        for route, handler, method, desc in (
            ("status", self.api_status, "GET", "SoL-Astr 运行状态"),
            ("models", self.api_models, "GET", "SoL-Astr 模型与价目"),
            ("sessions", self.api_sessions, "GET", "SoL-Astr 会话用量"),
            ("ledger", self.api_ledger, "GET", "SoL-Astr 台账"),
            ("config", self.api_save_config, "POST", "SoL-Astr 保存配置"),
        ):
            self.context.register_web_api(
                f"/{PLUGIN_NAME}/{route}", handler, [method], desc
            )

        if not self.config["enable_compact"]:
            logger.info("SoL-Astr 未启用。")
            return

        compression = self.compression_config()
        if compression.get("max_turns", -1) != -1:
            logger.warning(
                "SoL-Astr：核心配置开启了按轮截断（max_turns=%s），它会在压缩之前丢掉旧消息，"
                "与本插件相互干扰，建议改回 -1。",
                compression.get("max_turns"),
            )

        self.context.add_llm_tools(self.tool)
        self.enabled = True
        logger.info(
            "SoL-Astr 已启用，摘要模型：%s，压缩提示词来自%s。",
            compression.get("provider_id") or "当前聊天模型",
            "核心配置" if compression.get("instruction", "").strip() else "插件默认",
        )

    # ---------- 配置读取 ----------

    def compression_config(self) -> dict:
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
        configured = self.compression_config().get("instruction", "").strip()
        return configured or DEFAULT_INSTRUCTION

    async def summarize_provider(self, umo: str | None = None):
        """核心配的压缩模型，没配就回落当前会话的聊天模型。

        回落行为与内置压缩器一致（astr_main_agent.py:1331-1345）。
        """
        provider_id = self.compression_config().get("provider_id", "").strip()
        if provider_id:
            provider = self.context.get_provider_by_id(provider_id)
            if provider is not None:
                return provider
            logger.warning(
                "SoL-Astr：压缩模型 %s 不可用，回落当前聊天模型。", provider_id
            )
        return await self.context.get_using_provider_async(umo=umo)

    # ---------- 模型与价目 ----------

    async def catalog_entry(self, model_name: str) -> dict | None:
        """把模型名解析成目录条目（含价目）。解析不到返回 None。"""
        if model_name in self._model_cache:
            return self._model_cache[model_name]
        models = await self.catalog.load()
        entry = resolve(model_name, models) if models else None
        self._model_cache[model_name] = entry
        return entry

    async def priced_pair(
        self, umo: str | None = None
    ) -> tuple[dict | None, dict | None]:
        """(主模型, 摘要模型) 的目录条目。"""
        main = await self.context.get_using_provider_async(umo=umo)
        summarizer = await self.summarize_provider(umo)
        return (
            await self.catalog_entry(main.get_model()) if main else None,
            await self.catalog_entry(summarizer.get_model()) if summarizer else None,
        )

    # ---------- 阈值 ----------

    async def thresholds(self, umo: str | None = None) -> dict:
        """本次会话生效的保留段、最小归档段、窗口，以及它们各自的来源。"""
        provider = await self.context.get_using_provider_async(umo=umo)
        window = provider.provider_config["max_context_tokens"]
        provider_id = str(provider.provider_config.get("id", ""))

        keep = self.config["keep_recent_tokens"] or min(
            KEEP_CAP, int(window * KEEP_RATIO)
        )
        result = {
            "keep_recent": keep,
            "window": window,
            "window_source": self.window_source.get(provider_id, "provider"),
            "keep_source": "配置" if self.config["keep_recent_tokens"] else "按窗口",
        }

        if self.config["min_archive_tokens"]:
            result["min_archive"] = self.config["min_archive_tokens"]
            result["archive_source"] = "配置"
            return result

        spot = await self.compute_sweet_spot(umo, keep)
        if spot is not None:
            result["sweet_spot"] = spot
            if spot.solvable:
                result["min_archive"] = spot.min_archive
                result["archive_source"] = "价目"
                return result

        result["min_archive"] = min(ARCHIVE_CAP, int(window * ARCHIVE_RATIO))
        result["archive_source"] = "按窗口"
        return result

    async def compute_sweet_spot(self, umo: str | None, keep: int):
        """按真实价目算甜点值。目录不可用或模型认不出时返回 None。"""
        main, summarizer = await self.priced_pair(umo)
        if not main or not summarizer:
            return None
        target = self.config["target_turns"]
        key = (main["id"], summarizer["id"], keep, target)
        if key not in self._spot_cache:
            self._spot_cache[key] = sweet_spot(main, summarizer, keep, target)
        return self._spot_cache[key]

    # ---------- 钩子 ----------

    @filter.on_waiting_llm_request()
    async def fill_context_window(self, event: AstrMessageEvent) -> None:
        """在 build_main_agent 之前，用目录里的真实窗口补上没配的 max_context_tokens。

        核心的补法是 models.dev → fallback_max_tokens（astr_main_agent.py:1674-1684），
        后者是个与模型无关的固定数，拿它当任何模型的窗口都是瞎猜。这里赶在它之前，
        只填用户留空的字段，只写内存不落盘。
        """
        if not self.enabled:
            return
        provider = await self.context.get_using_provider_async(
            umo=event.unified_msg_origin
        )
        provider_id = str(provider.provider_config.get("id", ""))
        if provider_id in self.window_source:
            return

        if provider.provider_config.get("max_context_tokens", 0) > 0:
            self.window_source[provider_id] = "provider 配置"
            return

        model = provider.get_model()
        if LLM_METADATAS.get(model):
            self.window_source[provider_id] = "models.dev"
            return

        entry = await self.catalog_entry(model)
        if entry and entry["context"]:
            provider.provider_config["max_context_tokens"] = entry["context"]
            self.window_source[provider_id] = "OpenRouter 目录"
            logger.info(
                "SoL-Astr：%s 未配置窗口，按目录条目 %s 填入 %d。",
                model,
                entry["id"],
                entry["context"],
            )
            return

        self.window_source[provider_id] = "兜底值"

    @filter.on_llm_request()
    async def inject_context_size(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """在当轮用户消息尾部追加一行上下文状态。

        三个数一起给：当前用量、建议压缩线、模型真实上限。只给「当前/压缩线」会让
        模型把压缩线当成容量上限——用掉 105k 明明还很宽裕，写成 105000/98400 却像
        是已经溢出了。

        体积判断交给插件、语义判断留给模型：裸报一个 token 数模型无从判断算大算小。

        放在消息尾部而不是 system prompt，是为了不破坏上游的前缀缓存；这一行会随
        历史落盘，不要剥离，否则下一轮的前缀又和缓存对不上了。
        """
        if not self.enabled:
            return
        tokens = req.conversation.token_usage if req.conversation else 0
        if not tokens:
            tokens = count_tokens(bind_checkpoint_messages(req.contexts))

        limits = await self.thresholds(event.unified_msg_origin)
        threshold = limits["keep_recent"] + limits["min_archive"]
        status = (
            f"context: 当前 {tokens} / 压缩线 {threshold} / "
            f"模型上限 {limits['window']} tokens"
        )
        if tokens < threshold:
            text = f"[{status}]"
        else:
            event.set_extra(ELIGIBLE_FLAG, tokens)
            text = (
                f"[{status} —— 用量过压缩线了。要是刚才那个话题聊完了、"
                f"也没留下什么没办完的事，就调 {TOOL_NAME} 把早前的记录收一收。"
                "还在聊同一件事就先放着。]"
            )
        req.extra_user_content_parts.append(TextPart(text=text))

    @filter.on_agent_done()
    async def after_agent(
        self,
        event: AstrMessageEvent,
        run_context: ContextWrapper[AstrAgentContext],
        response: LLMResponse,
    ) -> None:
        """压缩成功就摘掉工具痕迹；够格却没被调用则记一笔。

        剥离的理由：这一轮的前缀本来就因为压缩断掉了，删这对消息不额外花钱；留着
        反而会当成样例，诱导模型下一轮接着调。没压缩的轮次不动，那句「不需要压缩」
        正好是告诉模型别急着重试的信号。
        """
        if event.get_extra(COMPACTED_FLAG):
            event.set_extra(COMPACTED_FLAG, False)
            if self.config["strip_tool_trace"]:
                removed = strip_tool_trace(run_context.messages)
                logger.info("SoL-Astr：已剥离 %d 条压缩工具消息。", removed)
            return

        eligible = event.get_extra(ELIGIBLE_FLAG)
        if eligible and not event.get_extra(CALLED_FLAG):
            ledger.append(
                self.ledger_path,
                {
                    "umo": event.unified_msg_origin,
                    "outcome": "not_called",
                    "total_tokens": eligible,
                },
            )

    # ---------- Web API（薄壳，数据组装在 api.py）----------

    async def api_status(self):
        return jsonify(await api.status(self))

    async def api_models(self):
        return jsonify(await api.models(self))

    async def api_sessions(self):
        return jsonify(await api.sessions(self))

    async def api_ledger(self):
        return jsonify(api.ledger_view(self, int(request.args.get("limit", 200))))

    async def api_save_config(self):
        return jsonify(api.save_config(self, await request.get_json()))
