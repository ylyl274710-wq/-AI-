"""农药查询与安全校验（文档难点 3 / 6 / 8 的落地）。

这里最重要的不是「查农药」，而是**让规则引擎可以被单独调用和观察**。
  · GET  /pesticide/products    —— 登记产品清单（带数据来源与核验时间）
  · POST /pesticide/rule-check  —— 只跑规则引擎，不经过模型
  · GET  /pesticide/usage       —— 用药记录（抗性判断的依据）

★ 为什么要把 rule-check 单独暴露成接口：
  Agent 内部也调它，但内部调用被埋在整条链路里，出问题时分不清是
  「检索没召回对」还是「规则拦错了」。单独一个端点可以喂固定输入、
  看固定输出，能直接写进回归测试。这也是文档难点 3（农药安全）与
  难点 13（结果无法验证）唯一能真正验证的地方 —— 安全这类事，
  不能靠「看着像是对的」。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.pesticide import (PesticideActiveIngredient, PesticideProduct,
                                  PesticideUsage)
from app.models.user import User
from app.schemas import (IngredientOut, PesticideOut, RuleCheckIn,
                         RuleCheckItem, RuleCheckOut, UsageIn, UsageOut)
from app.services.rules.pesticide_rules import PesticideRuleEngine, RuleCheck

router = APIRouter(prefix="/pesticide", tags=["农药"])

# ★ 数据免责声明必须跟着产品数据一起返回。这些登记信息是人工整理的，
#   且「使用阶段」这类字段农药登记本身并不包含（是人工标注），
#   不把这一点说清楚，前端就会把整理结果当成官方结论展示给农户。
DATA_CAVEAT = ("登记信息由人工整理自公开农药登记资料，可能存在滞后；"
               "使用阶段为人工标注，仅供参考。实际用药请以产品标签为准。")


def _to_out(p: PesticideProduct) -> PesticideOut:
    out = PesticideOut.model_validate(p)
    out.data_caveat = DATA_CAVEAT
    return out


def _to_item(c: RuleCheck) -> RuleCheckItem:
    return RuleCheckItem(
        product_id=c.product_id, product_name=c.product_name, passed=c.passed,
        blocked_reasons=list(c.blocked_reasons or []),
        warnings=list(c.warnings or []),
        label_dosage_text=c.label_dosage_text,
        safety_interval_days=c.safety_interval_days,
        active_ingredients=list(c.active_ingredients or []),
        resistance_groups=list(c.resistance_groups or []),
        source_url=c.source_url)


# --------------------------------------------------------------------------- #
# 产品与有效成分
# --------------------------------------------------------------------------- #


@router.get("/products", response_model=list[PesticideOut],
            summary="登记农药产品")
def list_products(
    crop_name: str | None = Query(None),
    pest_name: str | None = Query(None),
    keyword: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PesticideOut]:
    stmt = select(PesticideProduct).where(PesticideProduct.deleted_at.is_(None))
    if crop_name:
        stmt = stmt.where(PesticideProduct.registered_crops.contains([crop_name]))
    if pest_name:
        stmt = stmt.where(PesticideProduct.target_pests.contains([pest_name]))
    if keyword:
        stmt = stmt.where(PesticideProduct.product_name.ilike(f"%{keyword}%"))
    rows = db.scalars(stmt.order_by(PesticideProduct.product_name)
                      .limit(limit)).all()
    return [_to_out(p) for p in rows]


@router.get("/products/{product_id}", response_model=PesticideOut,
            summary="农药产品详情")
def product_detail(product_id: int, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)) -> PesticideOut:
    p = db.get(PesticideProduct, product_id)
    if p is None or p.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "产品不存在")
    return _to_out(p)


@router.get("/ingredients", response_model=list[IngredientOut],
            summary="有效成分清单")
def list_ingredients(
    category: str | None = Query(None),
    resistance_group: str | None = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[PesticideActiveIngredient]:
    stmt = select(PesticideActiveIngredient)
    if category:
        stmt = stmt.where(PesticideActiveIngredient.category == category)
    if resistance_group:
        stmt = stmt.where(
            PesticideActiveIngredient.resistance_group == resistance_group)
    return list(db.scalars(
        stmt.order_by(PesticideActiveIngredient.standard_name)).all())


# --------------------------------------------------------------------------- #
# 规则引擎（闸门 B）
# --------------------------------------------------------------------------- #


@router.post("/rule-check", response_model=RuleCheckOut,
             summary="农药合规校验（闸门 B，不经过模型）")
def rule_check(payload: RuleCheckIn, days_to_harvest: int | None = None,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> RuleCheckOut:
    """逐产品校验：作物是否在登记范围、防治对象是否匹配、生长阶段是否适用、
    安全间隔期是否够、是否与近期用药重复（抗性风险）。

    ★ 返回体里 approved 与 blocked 都带上**逐条原因**，而不是只给一个
      「通过/不通过」。农户要的是「为什么不能用这个」，只给结论等于逼他
      去猜。原因码到人话的映射在 rules/pesticide_rules.py 里统一维护。
    """
    engine = PesticideRuleEngine(db)
    return _run_rule_check(engine, payload, user.id, days_to_harvest)


def _run_rule_check(engine: PesticideRuleEngine, payload: RuleCheckIn,
                    user_id: int | None,
                    days_to_harvest: int | None) -> RuleCheckOut:
    result = engine.check(crop_name=payload.crop_name,
                          disease_name=payload.disease_name,
                          growth_stage=payload.growth_stage,
                          user_id=user_id,
                          days_to_harvest=days_to_harvest)
    return RuleCheckOut(
        crop_name=result.crop_name, disease_name=result.disease_name,
        growth_stage=result.growth_stage, checked=result.checked,
        hard_blocked=result.hard_blocked, message=result.message,
        approved=[_to_item(c) for c in result.approved],
        blocked=[_to_item(c) for c in result.blocked])


# --------------------------------------------------------------------------- #
# 用药记录（抗性判断的依据，文档难点 8）
# --------------------------------------------------------------------------- #


@router.get("/usage", response_model=list[UsageOut],
            summary="我的用药记录")
def list_usage(field_id: int | None = None,
               limit: int = Query(50, ge=1, le=200),
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> list[PesticideUsage]:
    stmt = select(PesticideUsage).where(PesticideUsage.user_id == user.id)
    if field_id is not None:
        stmt = stmt.where(PesticideUsage.field_id == field_id)
    return list(db.scalars(
        stmt.order_by(PesticideUsage.used_at.desc().nullslast(),
                      PesticideUsage.id.desc()).limit(limit)).all())


@router.post("/usage", response_model=UsageOut,
             status_code=status.HTTP_201_CREATED, summary="补记一次用药")
def create_usage(payload: UsageIn, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> PesticideUsage:
    """农户事后补记用药。

    ★ record_source 一律写成 "manual"。文档难点 8 说「用药记录容易遗漏」，
      而**遗漏的根因不是农户懒，是记起来麻烦**。所以系统里有两类记录：
        · agent   —— 诊断后点「我已按建议施药」，一键生成，字段自动带全
        · manual  —— 补记，字段可能不全
      分开标记，做抗性统计时才知道哪些数据可信、哪些只是参考。
      这条端点是 manual 通道；agent 通道在诊断完成后由前端调同一张表写入。
    """
    data = payload.model_dump()
    if data.get("product_id"):
        p = db.get(PesticideProduct, data["product_id"])
        if p is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "产品不存在")
        # 用产品信息补全冗余字段，避免只存一个 id 导致以后产品下架就查不出用了什么
        data.setdefault("product_name_raw", p.product_name)
        if not data.get("active_ingredient_name") and p.active_ingredient_names:
            data["active_ingredient_name"] = p.active_ingredient_names[0]
    if not data.get("product_name_raw") and not data.get("active_ingredient_name"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "请至少填写产品名或有效成分名")

    rec = PesticideUsage(user_id=user.id, record_source="manual", **data)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


@router.delete("/usage/{usage_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="删除用药记录")
def delete_usage(usage_id: int, db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> None:
    """这里是**真删**，不是软删 —— 与其他资源不同，这是刻意的。

    ★ 用药记录是抗性判断的输入（rules/pesticide_rules.py 的 _recent_usage
      按最近 30 天统计「同一作用机理用过几次」）。农户补记时写错一条，
      如果只做软删，而统计查询又没有过滤 deleted_at，那条错记录会一直
      参与抗性计算，导致系统反复提醒「该药已连用两次，请换药」——
      农户会觉得这功能在胡说。误记就该彻底消失。
      代价是删了不可恢复，所以前端必须二次确认。
    """
    rec = db.scalar(select(PesticideUsage).where(
        PesticideUsage.id == usage_id, PesticideUsage.user_id == user.id))
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "记录不存在")
    db.delete(rec)
    db.commit()
