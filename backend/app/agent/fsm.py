"""有限状态 Agent 编排（文档难点 6）。

为什么不用 LangGraph：
文档写的是「LangGraph 或自定义工作流」，但对「有限状态」这个需求，
一个显式状态机比通用图框架更可控 —— 转移关系一眼能看完、好写单测、
不受框架版本变动影响，答辩时也能讲清楚每个节点在干什么。

机器结构：
    INTENT → CONTEXT → DIAGNOSE → RETRIEVE →[闸门A]→ RULE_CHECK →[闸门B]→
    COMPOSE → PERSIST → REMIND → DONE

终止态：NEEDS_MORE_INFO（要补拍）/ REJECTED（无法识别）/ FAILED（异常）
"""
from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent import prompts
from app.agent.prompts import (CONSULT_SYSTEM, COMPOSE_SYSTEM, INTENT_SCHEMA,
                               INTENT_SYSTEM, build_retrieval_block,
                               build_rule_block, history_block)
from app.agent.state import (INTENT_ROUTES, MAIN_CHAIN, AgentContext, AgentState)
from app.core.config import settings
from app.models.diagnosis import (ST_FAILED, ST_NEEDS_MORE_INFO, ST_REJECTED,
                                  ST_SUCCEEDED, DiagnosisImage, DiagnosisRecord)
from app.models.farm import Crop
from app.models.misc import ChatMessage
from app.services import guard as guard_svc
from app.services import imaging
from app.services.confidence import compute_confidence, supplementary_shot_hint
from app.services.llm import LLMUnavailable, llm
from app.services.rag.retriever import HybridRetriever
from app.services.rag.rewrite import rewrite_query
from app.services.rules.pesticide_rules import PesticideRuleEngine
from app.services.storage import get_storage
from app.services.weather import WeatherService, assess_risk

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    pass


class AgriAgent:
    MAX_STEPS = 24
    NODE_TIMEOUT_SECONDS = 120

    def __init__(self, db: Session, on_event=None) -> None:
        self.db = db
        # 可选的事件回调，签名 (dict) -> None。用于把执行进度实时推给
        # SSE 前端 —— 一次诊断要跑好几秒（检索 + 规则 + 生成），
        # 没有进度反馈的话用户会以为页面卡死。
        # 注意：回调可能在工作线程里被调用，实现方需自行保证线程安全。
        self.on_event = on_event
        # ★ 显式注册工具，不依赖模块导入顺序。
        #   注册逻辑原本只写在 tool_impl 模块顶层，而本模块从不导入它，
        #   导致运行时所有工具都是「未注册」，每个节点都抛
        #   「未注册的工具: xxx」，还会被节点级 try/except 吞成
        #   「上下文加载失败」这类误导性错误。register_default_tools 幂等。
        from app.agent.tool_impl import register_default_tools
        register_default_tools()

    def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001
            # 推送失败绝不能影响主流程
            logger.debug("事件回调失败", exc_info=True)

    # ================================================================== #
    # 主循环
    # ================================================================== #
    async def run(self, ctx: AgentContext) -> AgentContext:
        handlers = {
            AgentState.INTENT: self._node_intent,
            AgentState.CONTEXT: self._node_context,
            AgentState.DIAGNOSE: self._node_diagnose,
            AgentState.RETRIEVE: self._node_retrieve,
            AgentState.TOOL_LOOP: self._node_tool_loop,
            AgentState.RULE_CHECK: self._node_rule_check,
            AgentState.COMPOSE: self._node_compose,
            AgentState.PERSIST: self._node_persist,
            AgentState.REMIND: self._node_remind,
        }

        # 多轮记忆：run 开始时把本会话最近几轮读进 ctx.history ——
        # intent 靠它识别「那辣椒呢」是在追问，compose 靠它不重复已说过的结论
        if ctx.session_id:
            ctx.history = self._load_history(ctx)

        ctx.state = AgentState.INTENT
        route: list[AgentState] | None = None

        while ctx.steps < self.MAX_STEPS:
            ctx.steps += 1
            node = ctx.state

            if node in (AgentState.DONE, AgentState.FAILED,
                        AgentState.NEEDS_MORE_INFO, AgentState.REJECTED):
                break

            handler = handlers.get(node)
            if handler is None:
                ctx.add_trace(node.value, status="error",
                              detail=f"未知状态 {node.value}，转入失败终止")
                ctx.state = AgentState.FAILED
                break

            t0 = time.monotonic()
            try:
                nxt = await handler(ctx)
            except LLMUnavailable as exc:
                # LLM 不可用不该让整条链路失败，节点内部应自行降级；
                # 走到这里说明降级逻辑有漏，记录后继续用兜底推进
                logger.warning("节点 %s LLM 不可用: %s", node.value, exc)
                ctx.degraded = True
                ctx.errors.append(f"{node.value}: 模型暂不可用")
                ctx.add_trace(node.value, status="error",
                              detail="模型不可用，走降级分支",
                              elapsed_ms=int((time.monotonic() - t0) * 1000))
                nxt = self._fallback_next(node)
            except Exception as exc:  # noqa: BLE001
                logger.exception("节点 %s 执行异常", node.value)
                ctx.errors.append(f"{node.value}: 执行失败")
                ctx.add_trace(node.value, status="error", detail="执行失败，请稍后重试",
                              elapsed_ms=int((time.monotonic() - t0) * 1000))
                nxt = self._fallback_next(node)
            else:
                ctx.add_trace(node.value, status="ok",
                              elapsed_ms=int((time.monotonic() - t0) * 1000))

            self._emit({
                "type": "trace",
                "node": node.value,
                "next": nxt.value if nxt else None,
                "elapsed_ms": ctx.trace[-1].elapsed_ms if ctx.trace else 0,
                "detail": ctx.trace[-1].detail if ctx.trace else None,
                "steps": ctx.steps,
            })

            if nxt is None:
                nxt = self._fallback_next(node)

            # 路由裁剪：不在本次意图路线里的节点直接跳过，
            # 避免「问个施肥问题也被拉去跑农药规则校验」
            if route is None and node == AgentState.INTENT:
                route = INTENT_ROUTES.get(ctx.intent, INTENT_ROUTES["consult"])
                if AgentState.DONE not in route:
                    route = route + [AgentState.DONE]
            if route and nxt not in route and nxt not in (
                    AgentState.NEEDS_MORE_INFO, AgentState.REJECTED,
                    AgentState.FAILED):
                nxt = self._next_in_route(node, nxt, route) or AgentState.DONE

            ctx.state = nxt

        if ctx.state not in (AgentState.DONE, AgentState.FAILED,
                             AgentState.NEEDS_MORE_INFO, AgentState.REJECTED):
            ctx.add_trace(ctx.state.value, status="error", detail="超过最大步数")
            ctx.state = AgentState.FAILED

        self._emit({"type": "final", "state": ctx.state.value,
                    "intent": ctx.intent, "answer": ctx.answer})
        return ctx

    @staticmethod
    def _next_in_route(node: AgentState, nxt: AgentState,
                       route: list[AgentState]) -> AgentState | None:
        """把不在路线里的目标态替换成路线中下一个合法节点。

        ★ 兜底必须沿**路线**而不是主链找：consult 路线里的 TOOL_LOOP 不在
          主链上——旧写法沿 MAIN_CHAIN 往后找，CONTEXT 返回 DIAGNOSE 后会
          一路跳过 TOOL_LOOP 直达 COMPOSE，工具循环永远不执行（实测踩过）。
        """
        if nxt in route:
            return nxt
        if node in route:
            idx = route.index(node)
            for cand in route[idx + 1:]:
                if cand != node:
                    return cand
        return AgentState.DONE
        del idx
        return AgentState.DONE

    @staticmethod
    def _fallback_next(node: AgentState) -> AgentState:
        """节点异常时的兜底推进：沿主链路走一步，绝不原地打转（防循环调用）。"""
        try:
            return MAIN_CHAIN[MAIN_CHAIN.index(node) + 1]
        except (ValueError, IndexError):
            # TOOL_LOOP 等不在主链路上的节点：跳过它本身，向 COMPOSE 推进 ——
            # 工具循环失败大不了没有工具证据，compose 用检索结果兜底
            return AgentState.COMPOSE

    def _load_history(self, ctx: AgentContext) -> list[dict[str, str]]:
        """读本会话最近几条消息（从旧到新）。失败返回空 ——
        历史读不到只影响连续性，绝不能挡住本次提问。"""
        try:
            rows = list(self.db.scalars(
                select(ChatMessage)
                .where(ChatMessage.session_id == ctx.session_id,
                       ChatMessage.user_id == ctx.user_id,
                       ChatMessage.role.in_(["user", "assistant"]))
                .order_by(ChatMessage.id.desc())
                .limit(prompts.HISTORY_MAX_MESSAGES)).all())
        except Exception:  # noqa: BLE001
            logger.warning("会话历史加载失败，按无历史继续", exc_info=True)
            return []
        rows.reverse()
        return [{"role": r.role, "content": r.content or ""} for r in rows]

    # ================================================================== #
    # 节点 1：意图识别
    # ================================================================== #
    async def _node_intent(self, ctx: AgentContext) -> AgentState:
        ctx.intent = self._heuristic_intent(ctx)
        if settings.llm_enabled and not ctx.has_images:
            try:
                # ★ 历史必须进意图识别：「那辣椒呢」单独看是病句（辣椒怎么了？），
                #   带上上一轮「番茄叶子发黄怎么办」才能识别成追问诊断、
                #   并解析出作物=辣椒 —— 这是多轮记忆的第一用途
                hist = history_block(ctx.history)
                user_content = (hist + "\n\n" if hist else "") + \
                    f"当前问题：{ctx.query or '（无文字描述）'}"
                data, _ = await llm.chat_json(
                    [{"role": "system", "content": INTENT_SYSTEM},
                     {"role": "user", "content": user_content}],
                    schema_hint=INTENT_SCHEMA, temperature=0.0, max_tokens=200)
                if isinstance(data, dict) and data.get("intent") in INTENT_ROUTES:
                    ctx.intent = data["intent"]
                    ctx.crop_name = ctx.crop_name or data.get("crop")
                    ctx.growth_stage = ctx.growth_stage or data.get("growth_stage")
            except LLMUnavailable:
                ctx.degraded = True
        if ctx.has_images and ctx.intent == "consult":
            ctx.intent = "diagnosis"
        return AgentState.CONTEXT

    @staticmethod
    def _heuristic_intent(ctx: AgentContext) -> str:
        if ctx.has_images:
            return "diagnosis"
        q = ctx.query or ""
        if any(k in q for k in ("用什么药", "打什么药", "能不能用", "农药", "剂量",
                                "用量", "间隔期", "兑水", "稀释")):
            return "pesticide"
        if any(k in q for k in ("提醒", "待办", "安排", "什么时候去", "任务")):
            return "task"
        if any(k in q for k in ("天气", "下雨", "湿度", "温度", "风险", "会不会发病")):
            return "farm_analysis"
        # ★ 「为什么 / 是什么原因 / 怎么回事」是在求解释，不是求诊断。
        #   不单独区分的话，「黄瓜叶子为什么发黄」会因为含「发黄」被判成
        #   诊断，接着被闸门 A 要求补拍照片 —— 用户问的是原因，
        #   不是让你看图看病。放在症状关键词之前判断。
        if any(k in q for k in ("为什么", "什么原因", "怎么回事", "咋回事",
                                "什么原理", "如何预防", "怎么预防", "有什么办法")):
            return "consult"
        if any(k in q for k in ("叶子", "叶片", "发黄", "卷", "斑点", "烂", "虫",
                                "萎蔫", "枯", "病", "不长", "畸形", "掉果")):
            return "diagnosis"
        return "consult"

    # ================================================================== #
    # 节点 2：取用户上下文
    # ================================================================== #
    async def _node_context(self, ctx: AgentContext) -> AgentState:
        from app.agent.tools import registry
        data = await registry.invoke(AgentState.CONTEXT, "load_farm_context", ctx,
                                     db=self.db)
        ctx.user_context = data
        if not ctx.crop_name:
            ctx.crop_name = data.get("crop_name")
        if not ctx.growth_stage:
            ctx.growth_stage = data.get("growth_stage")
        if ctx.field_id is None:
            ctx.field_id = data.get("field_id")
        if ctx.days_to_harvest is None:
            ctx.days_to_harvest = data.get("days_to_harvest")
        return AgentState.DIAGNOSE if ctx.intent == "diagnosis" else AgentState.RETRIEVE

    # ================================================================== #
    # 节点 3：视觉诊断
    # ================================================================== #
    async def _node_diagnose(self, ctx: AgentContext) -> AgentState:
        if not ctx.has_images:
            return AgentState.RETRIEVE

        if not settings.vision_enabled:
            # ★ 诚实降级：没有视觉模型时不假装能看图。
            #   文档难点 1 的兜底要求是「提示继续拍照或咨询专业人员」，
            #   在无模型环境下正确的兜底是「请用文字描述」。
            ctx.degraded = True
            ctx.answer_sections = {
                "发现什么": "当前系统没有配置视觉识别模型，我看不了照片。",
                "现在做什么": "你可以直接用文字描述一下：叶子哪个部位出问题、"
                              "是什么颜色、有没有霉层或虫子、最近天气怎么样。",
                "什么时候再看": "描述完我就能帮你查资料。",
            }
            # ★ 必须组装 answer。只填 answer_sections 不填 answer 的后果是：
            #   接口返回 answer=""，前端弹出一个空白气泡 —— 用户看到的是
            #   「系统毫无反应」，而实际原因（没配视觉模型）只存在于
            #   answer_sections 里，前端不解析就永远看不到。
            #   对外只暴露一个 answer 字段的接口，就必须把它填满。
            ctx.answer = "\n\n".join(
                f"【{k}】{v}" for k, v in ctx.answer_sections.items())
            ctx.add_trace("diagnose", status="skipped",
                          detail="未配置视觉模型，转入文字描述引导")
            # ★ 同样要落库：这次提问带着照片，是「有图但没识别」的真实样本。
            #   不落库的话，将来接入视觉模型后无法回答「当初有多少张图因为
            #   没配模型而浪费了」，也没法把这些历史照片捞出来补跑一次识别。
            #   注意这里要在调 persist 之前把 state 设好，persist 按 state 映射状态。
            ctx.state = AgentState.NEEDS_MORE_INFO
            await self._node_persist(ctx)
            return AgentState.NEEDS_MORE_INFO

        from app.agent.tools import registry
        result = await registry.invoke(AgentState.DIAGNOSE, "vision_diagnose", ctx,
                                       db=self.db)
        ctx.candidates = result.get("candidates", [])
        ctx.image_top1_names = result.get("per_image_top1", [])
        ctx.user_context["vision"] = result
        return AgentState.RETRIEVE

    # ================================================================== #
    # 节点 4：RAG 检索 + 闸门 A
    # ================================================================== #
    # ================================================================== #
    # 节点 3.5：咨询路径的工具循环（consult 专属，见 state.py INTENT_ROUTES）
    # ================================================================== #
    async def _node_tool_loop(self, ctx: AgentContext) -> AgentState:
        """LLM 自主决定调什么工具收集证据；失败/无模型时降级回固定检索。

        ★ 降级路径必须真的能走通：工具循环依赖 Function calling 能力，
          端点不支持时 LLMUnavailable 会从 llm.chat_with_tools 抛上来 ——
          此时整条 consult 链路退回「固定检索 + 模板/普通生成」，功能不丢。
        """
        if not settings.llm_enabled:
            ctx.degraded = True
            return await self._node_retrieve(ctx)
        try:
            from app.agent.tool_loop import run_tool_loop
            out = await run_tool_loop(
                ctx, self.db, max_rounds=settings.tool_loop_max_rounds)
        except LLMUnavailable as exc:
            logger.warning("工具循环不可用，consult 降级为固定检索: %s", exc)
            ctx.degraded = True
            ctx.errors.append("tool_loop: 模型暂不可用")
            return await self._node_retrieve(ctx)
        logger.info("工具循环完成：rounds=%s evidence=%s calls=%s",
                    out["rounds"], len(out["evidence"]),
                    sum(1 for t in ctx.tool_calls if t.get("node") == "tool_loop"))
        return AgentState.COMPOSE

    async def _node_retrieve(self, ctx: AgentContext) -> AgentState:
        crop_rows = list(self.db.scalars(
            select(Crop).where(Crop.deleted_at.is_(None))).all())
        crops = [c.name for c in crop_rows]
        # 别名 → 标准名：农户说「西红柿」「土豆」比说学名更常见，
        # 不映射的话作物过滤失效、别名在知识库里也检索不到。
        aliases = {a: c.name for c in crop_rows
                   for a in (c.aliases or []) if isinstance(c.aliases, list)}
        stages = [s for c in crop_rows
                  for s in ((c.growth_stages or []) if isinstance(c.growth_stages, list)
                            else [])]
        stage_names = [s.get("name") for s in stages if isinstance(s, dict)
                       and s.get("name")]

        query_text = self._build_retrieval_query(ctx)
        rq = await rewrite_query(query_text, known_crops=crops,
                                 known_stages=stage_names,
                                 crop_aliases=aliases)
        if ctx.crop_name:
            rq.crop = ctx.crop_name
        if ctx.growth_stage:
            rq.growth_stage = ctx.growth_stage

        retriever = HybridRetriever(self.db)
        retrieval = retriever.retrieve(rq, crop=ctx.crop_name,
                                       growth_stage=ctx.growth_stage)
        ctx.retrieval = retrieval
        ctx.retrieval_result = {
            "query_original": rq.original,
            "keywords": rq.keywords,
            "expanded_terms": rq.expanded_terms,
            "by_llm": rq.by_llm,
            "vector_hits": retrieval.vector_hits,
            "keyword_hits": retrieval.keyword_hits,
            "top_score": retrieval.top_score,
            "chunks": [c.to_dict() for c in retrieval.chunks],
        }

        # ---- 闸门 A：置信度判定 ----
        top1 = self._resolve_top1(ctx)
        # 分类器证据：从合并后的候选里取 source=classifier 的最强与次强，
        # 传给置信度模块参与一致性/区分度判定（无分类器候选时为 None）
        cls_cands = sorted(
            (c for c in ctx.candidates if c.get("source") == "classifier"),
            key=lambda c: c.get("confidence") or 0, reverse=True)
        classifier_top = None
        if cls_cands:
            classifier_top = {
                "name": cls_cands[0]["name"],
                "prob": cls_cands[0].get("confidence") or 0.0,
                "second_prob": (cls_cands[1].get("confidence") or 0.0)
                if len(cls_cands) > 1 else 0.0,
            }
        bd = compute_confidence(
            top1_name=top1, retrieval=retrieval,
            image_top1_names=ctx.image_top1_names,
            shot_types=ctx.image_shot_types,
            classifier_top=classifier_top)
        ctx.confidence = bd
        ctx.user_context["top1"] = top1

        # ★ 闸门 A 的作用域要同时满足两个条件：意图是「诊断」且确实上传了照片。
        #
        #   文档难点 1 的原意是「**图片**识别不准时要求用户补充照片」——
        #   闸门 A 管的是「照片够不够、清不清楚」。所以：
        #     · 有照片的诊断 → 硬闸门。置信度不足就要求补拍 / 直接拒答，
        #       因为诊断是在断言「你这株得的是 XX 病」，说错会导致用错药。
        #     · 纯文字描述   → 不硬拦。用户没拍照，让他「补拍」等于答非所问；
        #       正确做法是给出基于文字与知识库的初步判断，明确标注
        #       「仅凭文字描述」，并建议拍照确认（见 degraded 标记与
        #       COMPOSE 节点的措辞）。
        #     · 咨询 / 农情分析 / 用药查询 → 不拦。这些意图本来就不要求拍照；
        #       用药查询由闸门 B（农药规则引擎）把关。
        gate_a_applies = ctx.intent == "diagnosis" and ctx.has_images

        if gate_a_applies and bd.rejected:
            ctx.state = AgentState.REJECTED
            ctx.answer_sections = {
                "发现什么": "从目前提供的信息里，我没法判断出具体是什么问题。",
                "现在做什么": "请换一张更清楚的照片，或者用文字说说症状。",
                "什么时候再看": "重新上传后我立刻再看一次。",
            }
            ctx.answer = "\n\n".join(
                f"【{k}】{v}" for k, v in ctx.answer_sections.items())
            # ★ 被拦下也要落库，不能直接 return 走人。
            #   闸门 A 拦掉的这两类（rejected / needs_more_info）恰恰是最该记录的
            #   数据：「有多少次没认出来」是评估模型好坏的核心指标，也是农户补拍
            #   照片后做二次判断的起点。之前这里直接终止，PERSIST 节点永远执行不到，
            #   后果是：
            #     · diagnosis_records.status 里的 ST_REJECTED / ST_NEEDS_MORE_INFO
            #       成了死代码，永远写不进库（而这两个状态的定义本身说明设计意图
            #       就是要落库的）
            #     · 响应里 diagnosis_id 为 null，前端没法把「补拍的照片」关联到
            #       上一次提问上
            #     · 对话历史里整段缺失，农户回头看不到自己问过什么
            await self._node_persist(ctx)
            return AgentState.REJECTED

        if gate_a_applies and not bd.passed:
            ctx.state = AgentState.NEEDS_MORE_INFO
            hint = supplementary_shot_hint(bd.missing_shots)
            ctx.answer_sections = {
                "发现什么": (f"我怀疑可能是「{self._display_disease(ctx, top1)}」，"
                            "但把握不大。" if top1 else "我暂时判断不出来。"),
                "现在做什么": hint,
                "什么时候再看": "补拍之后重新提交，我再判断一次。",
            }
            ctx.answer = "\n\n".join(
                f"【{k}】{v}" for k, v in ctx.answer_sections.items())
            await self._node_persist(ctx)   # 同上：拦截也要留痕
            return AgentState.NEEDS_MORE_INFO

        if not bd.passed:
            # 低置信度但意图不是「诊断」：不拦截，转通用建议。
            # 只置 degraded 标记，不再单独记一条轨迹 —— 主循环每步已经记过
            # 一次，节点内重复记录会让轨迹出现「retrieve -> retrieve」这种
            # 看起来像执行了两次的假象。判定依据本身在 confidence.reasons 里。
            ctx.degraded = True

        return AgentState.RULE_CHECK

    def _build_retrieval_query(self, ctx: AgentContext) -> str:
        parts: list[str] = []
        if ctx.query:
            parts.append(ctx.query)
        if ctx.candidates:
            parts.append("；".join(str(c.get("name", "")) for c in ctx.candidates[:3]))
        vision = ctx.user_context.get("vision") or {}
        for c in (vision.get("candidates") or [])[:2]:
            ev = c.get("evidence") or []
            if ev:
                parts.append("；".join(str(e) for e in ev[:3]))
        return " ".join(p for p in parts if p).strip() or "作物异常"

    @staticmethod
    def _resolve_top1(ctx: AgentContext) -> str | None:
        """确定第一候选：优先模型给的，其次用检索证据推。"""
        if ctx.candidates:
            return str(ctx.candidates[0].get("name") or "") or None
        if ctx.retrieval is None:
            return None
        from app.services.confidence import candidate_evidence
        ev = candidate_evidence(ctx.retrieval)
        if not ev:
            return None
        return max(ev.items(), key=lambda kv: kv[1])[0]

    def _crop_name_set(self) -> set[str]:
        """知识库里出现过的作物标准名（实例内缓存，避免每次回答都查库）。"""
        cached = getattr(self, "_crop_cache", None)
        if cached is None:
            cached = set(self.db.scalars(
                select(Crop.name).where(Crop.deleted_at.is_(None))).all())
            self._crop_cache = cached
        return cached

    def _display_disease(self, ctx: AgentContext, name: str | None) -> str | None:
        """展示用的病害名：去掉与当前作物不符的作物前缀。

        ★ 知识库标准名常带作物前缀（「黄瓜白粉病」），但这条知识往往同时
          登记在多个作物上（crop_names = ["黄瓜","番茄","辣椒"]）。按「番茄」
          检索到它时，直接念「你种的番茄疑似出现了黄瓜白粉病」会让农户
          一头雾水 —— 明明是番茄，怎么说是黄瓜的病。
          展示时把不属于当前作物的前缀去掉，只留「白粉病」。
          只改展示，不改库里的标准名 —— 标准名要用于检索与规则匹配。
        """
        if not name:
            return name
        crop = (ctx.crop_name or "").strip()
        if not crop:
            return name
        for c in self._crop_name_set():
            if c != crop and name.startswith(c) and len(name) > len(c) + 1:
                return name[len(c):]
        return name

    # ================================================================== #
    # 节点 5：农药规则校验 + 闸门 B
    # ================================================================== #
    async def _node_rule_check(self, ctx: AgentContext) -> AgentState:
        disease = self._resolve_top1(ctx)
        if not ctx.crop_name or not disease:
            # 不在这里 add_trace：主循环每步都会记一条，节点内重复记录会让
            # 轨迹出现「rule_check -> rule_check」这种像跑了两次的假象。
            ctx.user_context["rule"] = {"skipped": "缺少作物或结论"}
            return AgentState.COMPOSE

        engine = PesticideRuleEngine(self.db)
        result = engine.check(crop_name=ctx.crop_name, disease_name=disease,
                              growth_stage=ctx.growth_stage,
                              user_id=ctx.user_id,
                              days_to_harvest=ctx.days_to_harvest)
        ctx.rule_result = result
        ctx.user_context["rule"] = result.to_dict()

        if not result.has_recommendation:
            # ---- 闸门 B：规则不通过，绝不放行任何具体用药 ----
            # 同样不 add_trace，原因见上面 skipped 分支的注释；
            # 拦截详情已经落在 ctx.user_context["rule"] 里，会被持久化。
            ctx.user_context["gate_b"] = "blocked"
        return AgentState.COMPOSE

    # ================================================================== #
    # 节点 6：生成回答
    # ================================================================== #
    async def _node_compose(self, ctx: AgentContext) -> AgentState:
        # 天气风险（辅助特征，不是诊断依据）
        try:
            ws = WeatherService(self.db)
            # ★ get_recent 在 provider=qweather 时会发真实 HTTP 请求（超时 8 秒×2），
            #   必须丢进线程池 —— 这里是 async 方法，直接同步调会把整个事件流
            #   （包括正在推送的 SSE）冻结到请求返回为止。
            snaps = await asyncio.to_thread(ws.get_recent, ctx.field_id)
            disease = self._resolve_top1(ctx)
            conditions = None
            susceptibility = None
            if disease:
                from app.models.knowledge import DiseaseRecord
                rec = self.db.scalar(select(DiseaseRecord).where(
                    DiseaseRecord.standard_name == disease,
                    DiseaseRecord.deleted_at.is_(None)))
                if rec:
                    conditions = dict(rec.conditions or {})
                    conditions["_disease_name"] = rec.standard_name
                    susceptibility = rec.stage_susceptibility
            ra = assess_risk(conditions, snaps, ctx.growth_stage, susceptibility)
            if ra:
                ctx.risk = [ra.to_dict()]
        except Exception as exc:  # noqa: BLE001
            # 文档第七章：天气接口失败不影响基础诊断，只关闭天气增强
            logger.warning("天气风险计算失败，关闭该增强: %s", exc)
            ctx.errors.append("weather: 天气服务暂不可用")

        if settings.llm_enabled:
            try:
                ctx.answer = await self._compose_with_llm(ctx)
            except LLMUnavailable as exc:
                logger.warning("生成回答降级: %s", exc)
                ctx.degraded = True
                ctx.answer = self._compose_by_template(ctx)
                # 流式通道在降级时也要有产出：整段作为单个 delta 推出，
                # 前端的打字机区域不会空白（与真实流式同一事件协议）
                if ctx.stream_queue is not None:
                    await ctx.stream_queue.put(
                        {"type": "delta", "text": ctx.answer})
        else:
            ctx.degraded = True
            ctx.answer = self._compose_by_template(ctx)
            if ctx.stream_queue is not None:
                await ctx.stream_queue.put({"type": "delta", "text": ctx.answer})

        # ---- 输出护栏：剂量拦截 + 白名单外产品提示 + 免责声明 ----
        whitelist = [c.product_name for c in
                     (ctx.rule_result.approved if ctx.rule_result else [])]
        g = guard_svc.apply_output_guard(
            ctx.answer, whitelist_products=whitelist,
            pesticide_involved=bool(ctx.rule_result))
        ctx.answer = g.text
        ctx.guard_info = g.to_dict()

        if not ctx.answer_sections:
            ctx.answer_sections = self._split_sections(ctx.answer)
        return AgentState.PERSIST

    async def _compose_with_llm(self, ctx: AgentContext) -> str:
        chunks = ctx.retrieval.chunks if ctx.retrieval else []
        system = COMPOSE_SYSTEM if ctx.intent == "diagnosis" else CONSULT_SYSTEM
        vision = ctx.user_context.get("vision") or {}
        top1 = self._resolve_top1(ctx)
        shown = self._display_disease(ctx, top1)
        hist = history_block(ctx.history)
        facts = [
            f"用户问题：{ctx.query or '（仅上传了照片）'}",
            f"作物：{ctx.crop_name or '未提供'}",
            f"生长阶段：{ctx.growth_stage or '未提供'}",
            f"系统判定的第一候选：{top1 or '未确定'}"
            + (f"（标准名；该条目同时适用于{ctx.crop_name}，"
               f"对外表述时请写成「{shown}」，不要出现其他作物的名字）"
               if shown and shown != top1 else ""),
            f"置信度：{getattr(ctx.confidence, 'score', 0):.2f}"
            f"（阈值 {settings.confidence_threshold}）",
        ]
        if vision:
            facts.append("视觉模型给出的候选与依据：" +
                         str(vision.get("candidates"))[:800])
        if ctx.risk:
            facts.append("天气风险评估：" + str(ctx.risk))
        if ctx.user_context.get("recent_usage"):
            facts.append("用户近期用药记录：" +
                         str(ctx.user_context["recent_usage"])[:400])

        # ---- 证据来源二选一：诊断走固定检索块，consult 走工具循环证据 ----
        if ctx.tool_loop_evidence:
            from app.agent.tool_loop import build_evidence_block
            evidence_block = "【工具收集到的信息】\n" \
                + build_evidence_block(ctx.tool_loop_evidence)
        else:
            evidence_block = "【知识库检索结果】\n" + build_retrieval_block(chunks)
            ctx.citations = [{"index": i, "title": c.title,
                              "source": c.source_name, "url": c.source_url}
                             for i, c in enumerate(chunks, start=1)]

        # 与模板通道同一套判定：没确诊就不把放行产品交给模型，
        # 否则模型会「照着清单念」，等于绕过置信度闸门推荐了药。
        confident = bool(ctx.confidence and ctx.confidence.passed)
        allow_products = bool(
            ctx.rule_result and ctx.rule_result.has_recommendation
            and (confident or ctx.intent == "pesticide"))

        user_msg = (
            "\n".join(facts)
            + ("\n\n" + hist if hist else "")
            + "\n\n" + evidence_block
            + "\n\n【规则引擎已放行产品（只能引用这些）】\n"
            + (build_rule_block(ctx.rule_result) if allow_products
               else "（本轮结论置信度不足，禁止推荐任何具体农药，"
                    "只能给非用药处理建议并提示用户补充照片或咨询植保员）")
            + ("\n\n如提供了对话历史，请避免重复上一轮已经说过的结论，"
               "直接回应本次的追问。" if hist else "")
            + "\n\n请按三段式输出回答。"
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user_msg}]

        # ---- 流式分支：SSE 路由注入了队列就走真 token 流，边生成边推 ----
        # ★ 流出去的是「护栏前」的原文，final 事件才是护栏后（剂量拦截等）
        #   的权威版本 —— 前端以 final 覆盖，极端情况下用户会看到个别句子
        #   被 final 收回，这是把「立刻看到字」与「输出合规」同时保住的折中。
        if ctx.stream_queue is not None:
            buf: list[str] = []
            async for piece in llm.stream(messages, temperature=0.3):
                buf.append(piece)
                await ctx.stream_queue.put({"type": "delta", "text": piece})
            text = "".join(buf)
            ctx.llm_model = None        # stream 路径拿不到 usage/model 元数据
            ctx.token_usage = {"streamed": True}
            return text

        res = await llm.chat(messages, temperature=0.3, max_tokens=900)
        ctx.llm_model = res.model
        ctx.token_usage = res.usage
        return res.text

    # ------------------------------------------------------------------ #
    @staticmethod
    def _pick_evidence_text(chunks: list, top1: str | None,
                            limit: int = 2, max_chars: int = 220) -> str:
        """挑出与结论直接相关的知识原文。

        ★ 不能简单地把检索前 3 条拼起来。检索结果里常常混着好几个不同问题
          （问「叶子为什么发黄」会同时召回缺素、霜霉病、白粉虱），直接拼接
          会得到一段前言不搭后语、还在句子中间被截断的文字 —— 实测出现过
          「……药害表现为 黄瓜霜霉病俗称跑马干」这种断句。
          正确做法：只取与结论同名的条目，并按标点截断。
        """
        if not chunks:
            return ""
        picked = [c for c in chunks if (c.disease_name or "") == top1] \
            if top1 else []
        if not picked:
            # 结论名与知识库标准名没对齐时，退回用第一条，聊胜于无
            picked = chunks[:1]
        out: list[str] = []
        for c in picked[:limit]:
            text = (c.content or "").strip()
            if not text:
                continue
            if len(text) > max_chars:
                head = text[:max_chars]
                for sep in ("。", "；", "！", "？"):
                    idx = head.rfind(sep)
                    if idx >= max_chars // 3:
                        head = head[:idx + 1]
                        break
                text = head
            out.append(text)
        return " ".join(out)

    def _compose_by_template(self, ctx: AgentContext) -> str:
        """无模型时的模板化回答。信息量不如模型，但结构完整、事实不编造。"""
        top1 = self._resolve_top1(ctx)
        # 展示名可能去掉作物前缀（「黄瓜白粉病」→「白粉病」）；
        # 检索证据匹配必须用标准名 top1，不能用展示名。
        shown = self._display_disease(ctx, top1)
        chunks = list(ctx.retrieval.chunks) if ctx.retrieval else []
        seen_text = self._pick_evidence_text(chunks, top1)

        if top1 and ctx.intent in ("diagnosis", "pesticide"):
            what = f"你种的{ctx.crop_name or '作物'}疑似出现了「{shown}」。"
        elif top1:
            # 咨询类问题不该断言成「你得病了」，只说明最相关的知识条目
            what = f"你问的情况，知识库里最相关的是「{shown}」。"
        else:
            what = "从你提供的信息里，我还没法确定具体是什么问题。"
        if seen_text:
            what += f" 知识库里的描述是：{seen_text}"

        # ★ 报出具体用药需要同时满足两个条件：规则引擎放行 + 结论置信度达标。
        #   规则引擎管的是「这个药能不能用在这个作物、这个防治对象上」，
        #   它不管「你到底得的是不是这个病」。如果结论本身没把握，即使药是
        #   合规的也不该报出来 —— 那等于拿合规的药用在错的地方。
        #   例外：用户自己点名了防治对象的「用药查询」意图（「番茄晚疫病用什么药」），
        #   目标由用户给定而不是系统猜的，此时以规则引擎结论为准。
        confident = bool(ctx.confidence and ctx.confidence.passed)
        allow_products = bool(
            ctx.rule_result and ctx.rule_result.has_recommendation
            and (confident or ctx.intent == "pesticide"))

        todo: list[str] = []
        if allow_products:
            for c in ctx.rule_result.approved[:2]:
                line = f"{c.product_name}"
                if c.label_dosage_text:
                    line += f"（标签用法：{c.label_dosage_text}）"
                if c.safety_interval_days is not None:
                    line += f"，安全间隔期 {c.safety_interval_days} 天"
                todo.append(line)
            todo.insert(0, "以下用药信息来自登记数据，请对照产品标签确认：")
        else:
            if ctx.rule_result and ctx.rule_result.has_recommendation and not confident:
                todo.append("虽然查到有对应的登记药剂，但因为还没确诊，"
                            "我先不报具体药名 —— 药用错了比不用更麻烦。")
            todo.append("暂时不能给你推荐具体的农药，"
                        "建议先做非用药处理：摘除病叶、改善通风、控制浇水。")
            todo.append("需要用药的话，请带着照片咨询当地植保部门，"
                        "或者把产品标签拿给他们看。")
        if ctx.risk:
            r = ctx.risk[0]
            todo.append(f"天气方面：{r.get('advice', '')}")

        when = "建议 3~5 天后复查一次，如果情况变差就尽快找当地农技员。"
        if ctx.growth_stage:
            when = f"当前处于{ctx.growth_stage}，{when}"

        # 没拍照时的说明放在最前面：让农户一眼就知道这个判断的依据有多强
        if ctx.intent == "diagnosis" and not ctx.has_images:
            what = ("（说明：你这次只用文字描述了症状，没有上传照片，"
                    "下面的判断是基于文字和知识库做的，仅供参考。）\n" + what)

        ctx.answer_sections = {
            "发现什么": what,
            "现在做什么": "\n".join(f"{i}. {t}" for i, t in enumerate(todo, 1)),
            "什么时候再看": when,
        }
        # 引用只列与结论相关的条目。把整批检索结果（top_k 通常 8 条）
        # 全堆上去，里面一半是不相关病害，农户反而不知道该信哪个。
        cited = [c for c in chunks if (c.disease_name or "") == top1][:3] \
            if top1 else []
        if not cited:
            cited = chunks[:3]
        ctx.citations = [{"index": i, "title": c.title, "source": c.source_name,
                          "url": c.source_url}
                         for i, c in enumerate(cited, start=1)]
        body = "\n\n".join(f"【{k}】\n{v}" for k, v in ctx.answer_sections.items())
        if ctx.citations:
            body += "\n\n参考来源：\n" + "\n".join(
                f"[{c['index']}] {c['title']}"
                + (f"（{c['source']}）" if c["source"] else "")
                for c in ctx.citations[:5])
        return body

    @staticmethod
    def _split_sections(text: str) -> dict[str, str]:
        import re
        pattern = re.compile(r"【(发现什么|现在做什么|什么时候再看)】")
        parts = pattern.split(text or "")
        out: dict[str, str] = {}
        for i in range(1, len(parts) - 1, 2):
            out[parts[i]] = parts[i + 1].strip()
        return out

    # ================================================================== #
    # 节点 7：落库
    # ================================================================== #
    async def _node_persist(self, ctx: AgentContext) -> AgentState:
        top1 = self._resolve_top1(ctx)

        # ★ 状态只在「诊断」意图下才可能是 rejected / needs_more_info。
        #   之前不看意图，导致「这两天天气怎么样」这种咨询也被记成
        #   needs_more_info —— 用户明明拿到了完整回答，记录却是「信息不足」，
        #   后续做数据统计（比如「有多少比例没答上来」）会完全失真。
        # ★ 状态直接由 ctx.state 映射，而不是靠 confidence 反推。
        #
        #   原来是从 confidence 反推（rejected → ST_REJECTED，not passed →
        #   ST_NEEDS_MORE_INFO），有两个问题：
        #     1. 闸门 A 是在 RETRIEVE 节点里提前终止的，正常路径根本走不到
        #        PERSIST，于是这两个状态永远写不进库，成了死代码；
        #     2. 「未配置视觉模型」的降级分支压根没有 confidence（它发生在
        #        DIAGNOSE 节点，早于检索），反推逻辑对它完全无效，只能记成
        #        succeeded —— 明明是「需要用户补充信息」，却记成成功。
        #   改成读 ctx.state 之后，任何终止点只要在落库前把 state 设对，
        #   状态就一定正确，不需要每个分支各写一套判断。
        status = ST_SUCCEEDED
        if ctx.errors and not ctx.answer:
            status = ST_FAILED
        elif ctx.intent == "diagnosis":
            # 只有诊断意图才有「拒识 / 信息不足」的语义；
            # 咨询、天气这类意图即使置信度低，用户也确实拿到了回答。
            if ctx.state == AgentState.REJECTED:
                status = ST_REJECTED
            elif ctx.state == AgentState.NEEDS_MORE_INFO:
                status = ST_NEEDS_MORE_INFO

        elapsed = sum(s.elapsed_ms for s in ctx.trace)

        rec = DiagnosisRecord(
            user_id=ctx.user_id, field_id=ctx.field_id,
            crop_id=self._crop_id(ctx.crop_name),
            input_source="image" if ctx.has_images else "text",
            input_text=ctx.query or None,
            status=status,
            top1_disease_name=top1,
            confidence=getattr(ctx.confidence, "score", None),
            confidence_detail=getattr(ctx.confidence, "to_dict", lambda: None)(),
            result={
                "intent": ctx.intent,
                "candidates": ctx.candidates,
                "retrieval": ctx.retrieval_result,
                "rule": ctx.rule_result.to_dict() if ctx.rule_result else None,
                "risk": ctx.risk,
                "guard": ctx.guard_info,
                "trace": ctx.trace_dicts(),
                "tool_calls": ctx.tool_calls,
                "errors": ctx.errors,
            },
            answer_text=ctx.answer,
            degraded=ctx.degraded,
            llm_model=ctx.llm_model,
            elapsed_ms=elapsed or None,
        )
        self.db.add(rec)
        self.db.flush()
        ctx.diagnosis_id = rec.id

        if ctx.has_images:
            await self._store_images(ctx, rec)

        self._persist_chat(ctx, rec)
        self.db.commit()
        return AgentState.REMIND

    def _persist_chat(self, ctx: AgentContext, rec: DiagnosisRecord) -> None:
        """写对话记录。

        ★ 之前只写 diagnosis_records，不写 chat_messages，结果「对话历史」
          这张表永远是空的 —— 前端要展示历史会话、要支持「接着上次继续问」
          都无从下手。这里把用户提问与助手回答成对写入，并把执行轨迹、
          工具调用、引用来源一并存下来，便于事后复盘「为什么给出这个回答」。
        """
        sid = ctx.session_id or "default"
        if ctx.query:
            self.db.add(ChatMessage(
                user_id=ctx.user_id, session_id=sid, role="user",
                content=ctx.query, intent=ctx.intent,
                diagnosis_id=rec.id))
        self.db.add(ChatMessage(
            user_id=ctx.user_id, session_id=sid, role="assistant",
            content=ctx.answer or "",
            trace=ctx.trace_dicts(),
            tool_calls=ctx.tool_calls,
            references=ctx.citations,
            intent=ctx.intent,
            token_usage=ctx.token_usage or None,
            degraded=ctx.degraded,
            diagnosis_id=rec.id))

    async def _store_images(self, ctx: AgentContext, rec: DiagnosisRecord) -> None:
        storage = get_storage()
        for i, raw in enumerate(ctx.images):
            try:
                proc = imaging.process_upload(raw, strip_exif=settings.strip_exif)
            except imaging.ImageProcessError as exc:
                logger.warning("第 %d 张图片处理失败: %s", i + 1, exc)
                ctx.errors.append(f"image[{i}]: 图片处理失败")
                continue
            key = storage.build_key("uploads", f"d{rec.id}", ".jpg")
            thumb_key = storage.build_key("thumbnails", f"d{rec.id}", ".jpg")
            try:
                url = storage.put(key, proc.main_bytes, "image/jpeg")
                storage.put(thumb_key, proc.thumb_bytes, "image/jpeg")
            except Exception as exc:  # noqa: BLE001
                logger.warning("图片上传存储失败: %s", exc)
                url = None
            self.db.add(DiagnosisImage(
                diagnosis_id=rec.id, object_key=key, url=url, thumb_key=thumb_key,
                width=proc.width, height=proc.height,
                bytes_size=len(proc.main_bytes), sha256=proc.sha256,
                shot_type=(ctx.image_shot_types[i]
                           if i < len(ctx.image_shot_types) else None),
                exif_stripped=settings.strip_exif))
        self.db.flush()

    def _crop_id(self, name: str | None) -> int | None:
        if not name:
            return None
        return self.db.scalar(select(Crop.id).where(Crop.name == name))

    # ================================================================== #
    # 节点 8：生成农事提醒
    # ================================================================== #
    async def _node_remind(self, ctx: AgentContext) -> AgentState:
        if ctx.intent not in ("diagnosis", "farm_analysis", "task"):
            return AgentState.DONE
        from app.agent.tools import registry
        try:
            ids = await registry.invoke(AgentState.REMIND, "create_farm_task", ctx,
                                        db=self.db)
            ctx.tasks_created = ids or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("创建农事任务失败: %s", exc)
            ctx.errors.append("remind: 创建农事任务失败")
        return AgentState.DONE
