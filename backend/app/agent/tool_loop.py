"""咨询路径的工具循环：LLM 自主决定调用哪些工具来收集证据（2026-09-27）。

★ 设计立场：**循环只收集证据，不生成回答**。
  最终回答仍由 compose 节点统一生成（流式 + 输出护栏 + 三段式出口纪律），
  工具循环产出的证据以【工具收集到的信息】块进入 compose 提示词。
  这样「Agent 自主调工具」与「回答纪律」两个诉求互不妥协：
  前者要自由，后者要可控。

与 FSM 固定管线的关系：诊断路径仍是固定管线（确定性优先，见 state.py
INTENT_ROUTES 注释）；consult 路径改走 TOOL_LOOP —— 两种形态并存，
面试/论文里都是一句说得清的设计对照，而不是「全盘倒向某一种」。

留痕：每次工具调用写 ctx.tool_calls（与 FSM 节点的记录同一形态，
{node, tool, ok, elapsed_ms, error?}），chat_messages.tool_calls 一并落库 ——
「Agent 哪一步查了什么」可事后复盘（难点 6 / 13）。
"""
from __future__ import annotations

import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.prompts import CONSULT_TOOL_SYSTEM, history_block
from app.models.farm import Crop, Farm
from app.services.llm import llm

logger = logging.getLogger(__name__)

# 单次咨询的最多「LLM 决策轮数」。不是工具调用次数上限（一轮可并行多个调用），
# 而是防 LLM 死循环调工具的硬顶。够覆盖「farm_context → weather → knowledge」
# 的典型三连，再往上大概率是模型在空转。
DEFAULT_MAX_ROUNDS = 3
# 单个工具结果塞回消息的最大字符数：超出说明工具把整库都倒出来了，
# 截断到摘要粒度即可（检索结果本来就该按 chunk 给）。
_TOOL_RESULT_MAX_CHARS = 4000

# --------------------------------------------------------------------------- #
# 工具清单（OpenAI function calling 格式）。描述写给模型看 ——
# 「什么时候该调这个工具」全靠 description 说清楚。
TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "在农业知识库中检索专业资料：症状鉴别、防治方法、"
                       "用药原理、农事操作要点。涉及「为什么/怎么办/怎么区分」时调用。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "检索问题，用农业专业术语，不要口语"},
            "crop": {"type": "string",
                     "description": "作物标准名（如：番茄），不确定则省略"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_weather_risk",
        "description": "查询用户地块最近几天的天气实况，以及指定作物当前天气下"
                       "的病害风险等级。涉及「最近天气」「现在适不适合打药/浇水」"
                       "「会不会爆发什么病」时调用。",
        "parameters": {"type": "object", "properties": {
            "crop": {"type": "string", "description": "作物名（可选）"},
            "days": {"type": "integer", "enum": [1, 3, 7],
                     "description": "回看天数，默认 3"},
        }}}},
    {"type": "function", "function": {
        "name": "get_farm_context",
        "description": "查询用户名下的农场、地块与在种作物。不知道用户种什么、"
                       "问题里没提作物名时，先调这个再决定下一步。",
        "parameters": {"type": "object", "properties": {}}}},
]


def parse_tool_args(arguments: str) -> dict:
    """解析模型给的 arguments（JSON 字符串）。坏 JSON 返回 {} ——
    工具照常执行（参数全缺省），绝不因为模型输出格式问题抛异常。"""
    try:
        data = json.loads(arguments or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def build_evidence_block(evidence: list[str]) -> str:
    """把工具循环收集到的证据渲染进 compose 提示词。空列表给出明示占位 ——
    compose 必须知道「这次没有任何工具证据」，而不是拿到空字符串误判。"""
    if not evidence:
        return "（本轮没有调用任何工具，未收集到证据；请如实说明信息不足）"
    return "\n\n".join(evidence)


class ToolLoopContext:
    """分发器的依赖容器：db + ctx 引用，工具实现都挂成方法。"""

    def __init__(self, ctx, db: Session) -> None:  # noqa: ANN001
        self.ctx = ctx
        self.db = db

    # ------------------------------------------------------------------ #
    async def _user_crops(self) -> list[str]:
        if self.ctx.crop_name:
            return [self.ctx.crop_name]
        rows = self.db.scalars(select(Crop.name).where(
            Crop.deleted_at.is_(None))).all()
        return list(rows)

    async def tool_search_knowledge(self, args: dict) -> dict:
        from app.services.rag.retriever import HybridRetriever
        from app.services.rag.rewrite import local_rewrite

        query = str(args.get("query") or self.ctx.query or "作物异常")
        crop = str(args.get("crop") or "") or self.ctx.crop_name
        rq = local_rewrite(query, known_crops=await self._user_crops())
        if crop:
            rq.crop = crop
        res = HybridRetriever(self.db).retrieve(rq, crop=crop or None)
        chunks = res.chunks
        # 检索到的条目进 ctx.citations —— 与固定管线同一套引用来源，
        # compose 的 [1][2] 编号和前端「查看来源」都靠它
        start = len(self.ctx.citations)
        for i, ch in enumerate(chunks, start=1):
            self.ctx.citations.append({"index": start + i, "title": ch.title,
                                       "source": ch.source_name,
                                       "url": ch.source_url})
        return {"count": len(chunks),
                "chunks": [{"title": c.title, "content": c.content[:300],
                            "citation_index": start + i}
                           for i, c in enumerate(chunks, start=1)]}

    async def tool_get_weather_risk(self, args: dict) -> dict:
        import asyncio

        from app.models.farm import Field
        from app.services.weather import WeatherService, risks_for_crop, summarize

        days = int(args.get("days") or 3)
        days = min(max(days, 1), 7)
        # 坐标解析：优先显式 field_id，否则取用户第一个有坐标的地块
        field_id = self.ctx.field_id
        lat = lng = None
        if field_id is None:
            owner_filter = [Farm.owner_id == self.ctx.user_id] \
                if self.ctx.user_id else []
            row = self.db.execute(
                select(Field.id, Field.lat, Field.lng)
                .join(Farm, Field.farm_id == Farm.id)
                .where(Field.lat.is_not(None), Field.lng.is_not(None),
                       *owner_filter).order_by(Field.id)).first()
            if row:
                field_id, lat, lng = row
        # get_recent 内部可能发真实 HTTP（provider=qweather），必须进线程池
        snaps = await asyncio.to_thread(
            WeatherService(self.db).get_recent, field_id, lat, lng, days)
        summary = summarize(snaps) or {}
        out: dict = {"weather": summary}
        crop = str(args.get("crop") or "") or self.ctx.crop_name
        if crop:
            risks = risks_for_crop(self.db, crop, self.ctx.growth_stage, snaps)
            out["disease_risks"] = [r.to_dict() for r in risks]
        return out

    async def tool_get_farm_context(self, args: dict) -> dict:
        owner_filter = [Farm.owner_id == self.ctx.user_id] \
            if self.ctx.user_id else []
        farms = self.db.scalars(select(Farm).where(*owner_filter)).all()
        out = []
        for f in farms:
            fields = [{"id": fd.id, "name": fd.name, "crop": fd.crop_name,
                       "area_mu": fd.total_area_mu}
                      for fd in (f.fields or [])]
            out.append({"farm": f.name, "fields": fields})
        return {"farms": out,
                "current_crop": self.ctx.crop_name,
                "growth_stage": self.ctx.growth_stage}

    # ------------------------------------------------------------------ #
    async def dispatch(self, name: str, args: dict) -> dict:
        if name == "search_knowledge":
            return await self.tool_search_knowledge(args)
        if name == "get_weather_risk":
            return await self.tool_get_weather_risk(args)
        if name == "get_farm_context":
            return await self.tool_get_farm_context(args)
        raise KeyError(name)


async def run_tool_loop(ctx, db: Session,  # noqa: ANN001
                        max_rounds: int = DEFAULT_MAX_ROUNDS) -> dict:
    """执行工具循环，返回 {evidence, rounds}。证据同时写 ctx.tool_loop_evidence。

    抛 LLMUnavailable 的场合由调用方（fsm 的 TOOL_LOOP 节点）降级到固定管线。
    """
    loop_ctx = ToolLoopContext(ctx, db)
    messages: list[dict] = [{"role": "system", "content": CONSULT_TOOL_SYSTEM}]
    hist = history_block(ctx.history)
    if hist:
        messages.append({"role": "user", "content": hist})
        messages.append({"role": "assistant",
                         "content": "（已了解之前的对话，请说当前问题。）"})
    messages.append({"role": "user", "content":
                     f"当前问题：{ctx.query or '（无文字描述）'}\n"
                     f"已知信息：作物={ctx.crop_name or '未知'}，"
                     f"生长阶段={ctx.growth_stage or '未知'}"})

    evidence: list[str] = []
    rounds = 0
    for _ in range(max_rounds):
        rounds += 1
        result = await llm.chat_with_tools(messages, TOOL_SPECS)
        if not result["tool_calls"]:
            break  # 模型认为信息足够（按约定应输出 DONE）
        # ★ assistant 消息必须原样带 tool_calls（含 id），协议要求
        messages.append({"role": "assistant",
                         "content": result["content"] or None,
                         "tool_calls": [
                             {"id": tc["id"], "type": "function",
                              "function": {"name": tc["name"],
                                           "arguments": tc["arguments"]}}
                             for tc in result["tool_calls"]]})
        for tc in result["tool_calls"]:
            t0 = time.monotonic()
            args = parse_tool_args(tc["arguments"])
            try:
                data = await loop_ctx.dispatch(tc["name"], args)
                ok = True
                err = ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("工具 %s 执行失败: %s", tc["name"], exc)
                data = {"error": str(exc)[:200]}
                ok = False
                err = str(exc)[:200]
            elapsed = int((time.monotonic() - t0) * 1000)
            ctx.tool_calls.append({"node": "tool_loop", "tool": tc["name"],
                                   "ok": ok, "elapsed_ms": elapsed,
                                   **({"error": err} if err else {"args": args})})
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(data, ensure_ascii=False, default=str)
                             [:_TOOL_RESULT_MAX_CHARS]})
            if ok:
                evidence.append(f"【{tc['name']}】"
                                + json.dumps(data, ensure_ascii=False, default=str)
                                [:_TOOL_RESULT_MAX_CHARS // 2])

    # ★ 知识库证据兜底（安全约束的代码层保障）：consult 的安全约束是
    #   「回答必须基于知识库」，而模型可能只调了天气/地块工具就认为信息
    #   足够（实测发生过）——天气证据再全也替代不了知识库。只要本轮
    #   没查过知识库，就强制补一次检索，保证 compose 永远有可引用的
    #   知识库证据；宁多一次检索，不赌模型自觉。
    if not any("search_knowledge" in e for e in evidence):
        t0 = time.monotonic()
        try:
            data = await loop_ctx.tool_search_knowledge({"query": ctx.query})
            ok = True
            err = ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("兜底检索失败: %s", exc)
            data = {"error": str(exc)[:200]}
            ok = False
            err = str(exc)[:200]
        elapsed = int((time.monotonic() - t0) * 1000)
        ctx.tool_calls.append({"node": "tool_loop", "tool": "search_knowledge",
                               "ok": ok, "elapsed_ms": elapsed,
                               **({"error": err}
                                  if err else {"args": {"fallback": True}})})
        if ok:
            evidence.append("【search_knowledge（兜底检索）】"
                            + json.dumps(data, ensure_ascii=False, default=str)
                            [:_TOOL_RESULT_MAX_CHARS // 2])

    ctx.tool_loop_evidence = evidence
    return {"evidence": evidence, "rounds": rounds}
