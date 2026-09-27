"""Agent 工具的默认实现与注册。

所有工具签名统一为 `async def handler(*, ctx, db, **kwargs)`，
由 ToolRegistry 按节点白名单调用（见 app/agent/tools.py 的 TOOL_POLICY）。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.prompts import VISION_SCHEMA, VISION_SYSTEM
from app.agent.state import AgentContext
from app.agent.tools import ToolSpec, registry
from app.core.config import settings
from app.models.farm import Crop, CropGrowthRecord, Farm, Field
from app.models.misc import FarmTask
from app.models.pesticide import (PesticideProduct, PesticideUsage)
from app.services import imaging
from app.services.llm import LLMUnavailable, llm
from app.services.rag.retriever import HybridRetriever
from app.services.rag.rewrite import rewrite_query
from app.services.rules.pesticide_rules import PesticideRuleEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------- #
# CONTEXT 节点工具
# ---------------------------------------------------------------------- #
async def load_farm_context(*, ctx: AgentContext, db: Session, **_) -> dict:
    """取用户的农田、当前作物、生长阶段、距采收天数。"""
    data: dict = {}
    if not ctx.user_id:
        return data

    field = db.get(Field, ctx.field_id) if ctx.field_id else None
    if field is None:
        field = db.scalar(
            select(Field).join(Farm, Field.farm_id == Farm.id)
            .where(Farm.owner_id == ctx.user_id, Field.deleted_at.is_(None))
            .order_by(Field.id))
    if field:
        data["field_id"] = field.id
        data["field_name"] = field.name
        data["farm_id"] = field.farm_id
        grow = db.scalar(select(CropGrowthRecord).where(
            CropGrowthRecord.field_id == field.id,
            CropGrowthRecord.is_current.is_(True)))
        if grow:
            data["growth_stage"] = grow.stage
            data["crop_name"] = grow.crop.name if grow.crop else None
            data["variety"] = grow.variety
            if grow.expected_harvest_date:
                data["days_to_harvest"] = max(
                    (grow.expected_harvest_date - date.today()).days, 0)
    return data


async def load_usage_history(*, ctx: AgentContext, db: Session,
                             days: int = 30, **_) -> list[dict]:
    """近期用药记录。只返回用户确认过的（文档难点 8）。"""
    if not ctx.user_id:
        return []
    since = date.today() - timedelta(days=days)
    rows = db.scalars(select(PesticideUsage).where(
        PesticideUsage.user_id == ctx.user_id,
        PesticideUsage.confirmed.is_(True),
        PesticideUsage.used_at.is_not(None),
        PesticideUsage.used_at >= since)
        .order_by(PesticideUsage.used_at.desc()).limit(10)).all()
    return [{"product": r.product_name_raw,
             "ingredient": r.active_ingredient_name,
             "resistance_group": r.resistance_group,
             "used_at": r.used_at.isoformat() if r.used_at else None}
            for r in rows]


# ---------------------------------------------------------------------- #
# DIAGNOSE 节点工具
# ---------------------------------------------------------------------- #
async def vision_diagnose(*, ctx: AgentContext, db: Session, **_) -> dict:
    """多模态诊断。图片先压缩再送模型（文档难点 11 的成本控制）。"""
    del db
    data_urls: list[str] = []
    raw_bytes: list[bytes] = []
    shots: list[str] = []
    # ★ 拍摄部位必须和图片一起收集，不能事后再按下标去配 ctx.image_shot_types：
    #   预处理失败的图会被 continue 跳过，导致 data_urls 比原列表短，
    #   后面的图就会整体错位一位（图 2 被标成图 3 的部位）—— 而且不报错。
    for idx, raw in enumerate(ctx.images[:4]):      # 最多 4 张，再多收益低、成本高
        try:
            proc = imaging.process_upload(raw, strip_exif=settings.strip_exif)
        except imaging.ImageProcessError as exc:
            logger.warning("图片预处理失败，跳过: %s", exc)
            continue
        data_urls.append(imaging.to_data_url(proc.main_bytes))
        raw_bytes.append(proc.main_bytes)
        shots.append(ctx.image_shot_types[idx] if idx < len(ctx.image_shot_types) else "")
    if not data_urls:
        return {"candidates": [], "per_image_top1": [], "image_quality": "poor",
                "notes": "没有可用的图片"}

    # ★ 把「这张图拍的是哪个部位」告诉模型。
    #   VISION_SCHEMA 要求它输出 missing_shots（还缺哪个角度），
    #   如果模型不知道已经拍了什么，这个字段只能瞎猜。
    shot_labels = {"leaf_front": "叶片正面", "leaf_back": "叶片背面",
                   "whole_plant": "整株全景", "lesion_closeup": "病斑特写"}
    shot_lines = "\n".join(
        f"  图{i + 1}：{shot_labels.get(s, s) if s else '未标注拍摄部位'}"
        for i, s in enumerate(shots))

    # ★ 这里必须把 VISION_SYSTEM 和 VISION_SCHEMA 都带上。
    #   之前只拼了下面这几行，两段提示词虽然 import 了却从没传进模型 ——
    #   结果是：模型不知道自己的角色、不知道安全规则、更不知道要按什么
    #   JSON 结构输出。而本函数下面的代码正是在按 VISION_SCHEMA 定义的
    #   candidates / per_image / image_quality / missing_shots 解析，
    #   所以即便配上 Key，也大概率解析出空结果（而且不会报错，只是没数据）。
    prompt = (f"作物：{ctx.crop_name or '未提供'}\n"
              f"生长阶段：{ctx.growth_stage or '未提供'}\n"
              f"用户补充描述：{ctx.query or '（无）'}\n\n"
              f"本次共 {len(data_urls)} 张照片，拍摄部位依次为：\n{shot_lines}\n\n"
              "请判断这些照片里的作物出了什么问题。\n"
              "严格按下面的 JSON 结构输出：\n"
              + VISION_SCHEMA)
    try:
        result = await llm.vision(prompt=prompt, image_data_urls=data_urls,
                                  system=VISION_SYSTEM,
                                  json_mode=True, max_tokens=1200)
    except LLMUnavailable as exc:
        logger.warning("视觉诊断不可用: %s", exc)
        return {"candidates": [], "per_image_top1": [], "image_quality": "unknown",
                "notes": "视觉模型不可用", "degraded": True}

    from app.services.llm import extract_json
    data = extract_json(result.text) or {}
    if not isinstance(data, dict):
        data = {}
    candidates = [c for c in (data.get("candidates") or [])
                  if isinstance(c, dict) and c.get("name")]
    per_image = []
    for item in (data.get("per_image") or []):
        if isinstance(item, dict) and item.get("top1"):
            per_image.append(str(item["top1"]))

    # ---- 本地分类器增强（可选）：把可复现的 top-k 概率并进候选 ----
    # 分类器只对「知识库里有条目」的类别出结果（pv_labels.json 映射），
    # 未映射类只留痕不冒充诊断。classifier.predict 内部吞掉一切异常，
    # 这里再兜一层 —— VLM 主链路绝不能被增强功能拖垮。
    classifier_info: list[dict] = []
    try:
        from app.services import classifier as cls_svc
        if cls_svc.classifier.available and data_urls:
            # ★ 必须传原始字节而不是 data URL——predict 期望 bytes，
            #   传字符串会 TypeError 且被兜底吞掉（实测：候选静默消失）
            cls_top = cls_svc.classifier.predict(raw_bytes[0])
            classifier_info = cls_top
            vlm_names = {str(c.get("name")) for c in candidates}
            strong: list[dict] = []   # 分类器强信号候选：分数可复现，排到最前
            for item in cls_top:
                if item["prob"] < settings.classifier_min_prob:
                    continue
                if item["standard_name"] in vlm_names:
                    # 两条通道结论一致：给候选补上可复现概率作为 agreement 证据
                    for c in candidates:
                        if str(c.get("name")) == item["standard_name"] \
                                and not c.get("classifier_prob"):
                            c["classifier_prob"] = item["prob"]
                    continue
                cand = {"name": item["standard_name"],
                        "source": "classifier",
                        "confidence": item["prob"]}
                # ★ 强候选置顶：_resolve_top1 取列表第一项，分类器 0.9+ 的
                #   概率必须压过 VLM 的低置信猜测，否则「识别」白做了
                (strong if item["prob"] >= settings.classifier_strong_prob
                 else candidates).append(cand)
            strong.sort(key=lambda c: c["confidence"], reverse=True)
            candidates[:0] = strong
    except Exception:  # noqa: BLE001
        logger.warning("分类器候选合并失败，忽略", exc_info=True)

    logger.info(
        "分类增强: available=%s cls_top=%s merged_candidates=%s",
        getattr(cls_svc.classifier, "available", None) if 'cls_svc' in dir() else 'n/a',
        len(classifier_info), len(candidates))
    return {
        "candidates": candidates,
        "per_image_top1": per_image,
        "image_quality": data.get("image_quality"),
        "missing_shots": data.get("missing_shots") or [],
        "notes": data.get("notes"),
        "model": result.model,
        "classifier_top": classifier_info,
    }


# ---------------------------------------------------------------------- #
# RETRIEVE 节点工具
# ---------------------------------------------------------------------- #
async def rewrite_query_tool(*, ctx: AgentContext, db: Session, **_) -> dict:
    crops = list(db.scalars(select(Crop.name).where(Crop.deleted_at.is_(None))).all())
    rq = await rewrite_query(ctx.query or "", known_crops=crops)
    return {"keywords": rq.keywords, "expanded": rq.expanded_terms,
            "crop": rq.crop, "by_llm": rq.by_llm}


async def retrieve_knowledge(*, ctx: AgentContext, db: Session, top_k: int | None = None,
                             **_) -> dict:
    rq = await rewrite_query(ctx.query or "作物异常", known_crops=None)
    if ctx.crop_name:
        rq.crop = ctx.crop_name
    if ctx.growth_stage:
        rq.growth_stage = ctx.growth_stage
    res = HybridRetriever(db).retrieve(rq, crop=ctx.crop_name,
                                       growth_stage=ctx.growth_stage, top_k=top_k)
    return {"count": len(res.chunks), "top_score": res.top_score,
            "chunks": [c.to_dict() for c in res.chunks]}


# ---------------------------------------------------------------------- #
# RULE_CHECK 节点工具（高风险，只在该节点可用）
# ---------------------------------------------------------------------- #
async def check_pesticide_rules(*, ctx: AgentContext, db: Session,
                                disease_name: str | None = None, **_) -> dict:
    disease = disease_name
    if not disease and ctx.candidates:
        disease = ctx.candidates[0].get("name")
    engine = PesticideRuleEngine(db)
    res = engine.check(crop_name=ctx.crop_name, disease_name=disease,
                       growth_stage=ctx.growth_stage, user_id=ctx.user_id,
                       days_to_harvest=ctx.days_to_harvest)
    return res.to_dict()


async def search_pesticide_products(*, ctx: AgentContext, db: Session,
                                    keyword: str = "", limit: int = 20, **_) -> dict:
    stmt = select(PesticideProduct).where(
        PesticideProduct.deleted_at.is_(None),
        PesticideProduct.review_status == "approved")
    if keyword:
        stmt = stmt.where(PesticideProduct.product_name.ilike(f"%{keyword}%"))
    rows = db.scalars(stmt.limit(limit)).all()
    return {"count": len(rows),
            "items": [{"id": r.id, "name": r.product_name,
                       "ingredients": r.active_ingredient_names,
                       "crops": r.registered_crops} for r in rows]}


# ---------------------------------------------------------------------- #
# PERSIST / REMIND 节点工具
# ---------------------------------------------------------------------- #
async def save_usage_record(*, ctx: AgentContext, db: Session,
                            product_name: str = "", used_at: date | None = None,
                            **_) -> dict:
    """记录一次用药。默认 confirmed=False，必须用户确认后才生效（难点 8）。"""
    rec = PesticideUsage(user_id=ctx.user_id, field_id=ctx.field_id,
                         product_name_raw=product_name, used_at=used_at,
                         record_source="manual", confirmed=False)
    db.add(rec)
    db.flush()
    return {"id": rec.id, "confirmed": False}


async def create_farm_task(*, ctx: AgentContext, db: Session, **_) -> list[int]:
    """按诊断结果自动排农事任务：3 天后复诊。"""
    if not ctx.user_id:
        return []
    due = date.today() + timedelta(days=3)
    title = "复查上次发现的问题"
    if ctx.crop_name:
        title = f"复查{ctx.crop_name}的疑似问题"
    detail_parts = []
    if ctx.candidates:
        detail_parts.append(f"上次疑似：{ctx.candidates[0].get('name')}")
    if ctx.rule_result and not ctx.rule_result.has_recommendation:
        detail_parts.append("上次未给出用药建议，注意观察是否有加重")
    task = FarmTask(user_id=ctx.user_id, field_id=ctx.field_id,
                    diagnosis_id=ctx.diagnosis_id, title=title,
                    task_type="recheck", due_date=due,
                    detail="；".join(detail_parts) or None, priority=2,
                    created_by_node="remind")
    db.add(task)

    ids = []
    if ctx.risk and ctx.risk[0].get("risk_level") in ("medium", "high"):
        patrol = FarmTask(user_id=ctx.user_id, field_id=ctx.field_id,
                          title="下地巡田，重点看叶片背面",
                          task_type="patrol", due_date=date.today() + timedelta(days=1),
                          detail=ctx.risk[0].get("advice"), priority=1,
                          created_by_node="remind")
        db.add(patrol)
        db.flush()
        ids.append(patrol.id)

    db.flush()
    ids.append(task.id)
    db.commit()
    return ids


async def list_farm_tasks(*, ctx: AgentContext, db: Session,
                          only_pending: bool = True, **_) -> list[dict]:
    stmt = select(FarmTask).where(FarmTask.user_id == ctx.user_id)
    if only_pending:
        stmt = stmt.where(FarmTask.status == "pending")
    rows = db.scalars(stmt.order_by(FarmTask.due_date).limit(50)).all()
    return [{"id": r.id, "title": r.title, "type": r.task_type,
             "due_date": r.due_date.isoformat() if r.due_date else None,
             "status": r.status} for r in rows]


async def save_diagnosis(*, ctx: AgentContext, db: Session, **_) -> dict:
    """落库动作由 FSM 的 PERSIST 节点统一处理，这里只返回当前 id。"""
    del db
    return {"diagnosis_id": ctx.diagnosis_id}


# ---------------------------------------------------------------------- #
_registered = False


def register_default_tools(force: bool = False) -> None:
    """注册全部内置工具。幂等。

    ★ 不要依赖本模块的「导入副作用」来触发注册。
      之前的写法是在本文件末尾直接调用 register_default_tools()，但
      fsm.py 是 `from app.agent.fsm import AgriAgent`，并不会导入本模块，
      结果运行时所有工具都是「未注册」，每个节点都抛
      「未注册的工具: xxx」—— 而且被节点级 try/except 吞掉，只表现为
      「上下文加载失败」，极难定位。
      现在由 AgriAgent.__init__ 显式调用本函数。
    """
    global _registered
    if _registered and not force:
        return
    specs = [
        ToolSpec("load_farm_context", "读取用户农田/作物/生长阶段/距采收天数",
                 load_farm_context, timeout_seconds=10, max_retries=1),
        ToolSpec("load_usage_history", "读取用户确认过的近期用药记录",
                 load_usage_history, timeout_seconds=10, max_retries=1),
        ToolSpec("vision_diagnose", "调用多模态模型对照片做初步诊断",
                 vision_diagnose, timeout_seconds=90, max_retries=1),
        ToolSpec("rewrite_query", "把口语症状改写成农业专业检索词",
                 rewrite_query_tool, timeout_seconds=25, max_retries=1),
        ToolSpec("retrieve_knowledge", "混合检索农业知识库（向量+关键词+元数据）",
                 retrieve_knowledge, timeout_seconds=30, max_retries=1),
        ToolSpec("check_pesticide_rules", "农药安全规则校验（高风险，仅规则节点可用）",
                 check_pesticide_rules, timeout_seconds=20, max_retries=1,
                 high_risk=True),
        ToolSpec("search_pesticide_products", "在登记白名单里搜索农药产品",
                 search_pesticide_products, timeout_seconds=15, max_retries=1),
        ToolSpec("save_diagnosis", "保存诊断记录", save_diagnosis,
                 timeout_seconds=15, max_retries=1),
        ToolSpec("save_usage_record", "保存用药记录（待用户确认）",
                 save_usage_record, timeout_seconds=15, max_retries=1),
        ToolSpec("create_farm_task", "创建农事任务与提醒", create_farm_task,
                 timeout_seconds=15, max_retries=1),
        ToolSpec("list_farm_tasks", "列出待办农事任务", list_farm_tasks,
                 timeout_seconds=10, max_retries=1),
    ]
    for s in specs:
        registry.register(s)
    _registered = True


# 模块被显式导入时顺手注册一次，方便脚本/测试单独用 registry。
# 真正的保障在 AgriAgent.__init__ 里的显式调用。
register_default_tools()
