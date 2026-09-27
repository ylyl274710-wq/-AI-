"""农药安全规则引擎（文档难点 3）。

原文档的规则描述是：
    「先判断作物是否匹配、防治对象是否匹配、使用阶段是否匹配，再读取产品数据；
      同时检查安全间隔期和历史用药。规则不通过时，Agent 不输出具体用药方案。」

这里把它实现成一个纯函数式的引擎（不依赖 LLM、不依赖网络），
五个维度逐条判定，每条都给出**可读的拦截原因**，而不是一个布尔值。
原因要能直接展示给农户，否则「不给推荐」会变成「系统坏了」。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.pesticide import (PesticideActiveIngredient, PesticideProduct,
                                  PesticideUsage)

logger = logging.getLogger(__name__)

# 拦截原因码 → 面向农户的话术（文档难点 9：不用术语）
BLOCK_MESSAGES = {
    "crop_mismatch": "这个药没有登记在你种的作物上，不建议使用",
    "target_mismatch": "这个药不是针对这个问题登记的，用了可能没效果",
    "stage_mismatch": "当前生长阶段不适合用这个药",
    "no_data": "这个药缺少可信的登记数据，不能推荐",
    "unverified": "这个药的数据还没有经过审核，不能推荐",
    "source_missing": "这个药查不到来源，不能推荐",
}
WARN_MESSAGES = {
    "safety_interval": "离采收期太近，安全间隔期可能不够",
    "resistance": "同一类作用机制的药最近用得太频繁了，容易产生抗药性",
    "toxicity_high": "这个药毒性较高，使用时务必做好防护",
}


@dataclass
class RuleCheck:
    product_id: int
    product_name: str
    passed: bool = False
    blocked_reasons: list[str] = field(default_factory=list)   # 原因码
    warnings: list[str] = field(default_factory=list)
    matched: dict[str, bool] = field(default_factory=dict)
    # 通过校验后，剂量只能来自标签原文，绝不由模型生成
    label_dosage_text: str | None = None
    safety_interval_days: int | None = None
    active_ingredients: list[str] = field(default_factory=list)
    resistance_groups: list[str] = field(default_factory=list)
    source_url: str | None = None
    data_verified_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "passed": self.passed,
            "blocked_reasons": self.blocked_reasons,
            "blocked_messages": [BLOCK_MESSAGES.get(r, r) for r in self.blocked_reasons],
            "warnings": self.warnings,
            "warning_messages": [WARN_MESSAGES.get(w, w) for w in self.warnings],
            "matched": self.matched,
            "label_dosage_text": self.label_dosage_text,
            "safety_interval_days": self.safety_interval_days,
            "active_ingredients": self.active_ingredients,
            "resistance_groups": self.resistance_groups,
            "source_url": self.source_url,
            "data_verified_at": self.data_verified_at,
        }


@dataclass
class RuleEngineResult:
    crop_name: str | None = None
    disease_name: str | None = None
    growth_stage: str | None = None
    approved: list[RuleCheck] = field(default_factory=list)
    blocked: list[RuleCheck] = field(default_factory=list)
    checked: int = 0
    message: str = ""
    hard_blocked: bool = False

    @property
    def has_recommendation(self) -> bool:
        return bool(self.approved)

    def to_dict(self) -> dict:
        return {
            "crop_name": self.crop_name,
            "disease_name": self.disease_name,
            "growth_stage": self.growth_stage,
            "checked": self.checked,
            "approved": [c.to_dict() for c in self.approved],
            "blocked": [c.to_dict() for c in self.blocked],
            "message": self.message,
            "hard_blocked": self.hard_blocked,
        }


def _norm_list(items) -> list[str]:
    return [str(i).strip() for i in (items or []) if str(i).strip()]


def _overlaps(a: list[str], b: list[str]) -> bool:
    return bool(set(a) & set(b))


class PesticideRuleEngine:
    """规则引擎。纯同步、纯数据库查询，方便单测。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    def _load_ingredients(self) -> dict[str, PesticideActiveIngredient]:
        rows = self.db.scalars(select(PesticideActiveIngredient)).all()
        return {r.standard_name: r for r in rows}

    def _recent_usage(self, user_id: int | None, days: int = 30
                      ) -> list[PesticideUsage]:
        if not user_id:
            return []
        since = date.today() - timedelta(days=days)
        return list(self.db.scalars(
            select(PesticideUsage).where(
                PesticideUsage.user_id == user_id,
                PesticideUsage.confirmed.is_(True),   # 只认用户确认过的记录
                PesticideUsage.used_at.is_not(None),
                PesticideUsage.used_at >= since,
            )).all())

    # ------------------------------------------------------------------ #
    def check(self, *, crop_name: str | None, disease_name: str | None,
              growth_stage: str | None = None, user_id: int | None = None,
              days_to_harvest: int | None = None,
              candidate_product_ids: list[int] | None = None) -> RuleEngineResult:
        result = RuleEngineResult(crop_name=crop_name, disease_name=disease_name,
                                  growth_stage=growth_stage)

        if not crop_name or not disease_name:
            result.message = "缺少作物或病虫害信息，无法做用药校验"
            result.hard_blocked = True
            return result

        stmt = select(PesticideProduct).where(PesticideProduct.deleted_at.is_(None))
        if candidate_product_ids:
            stmt = stmt.where(PesticideProduct.id.in_(candidate_product_ids))
        else:
            # 用作物做粗筛，减少无效遍历；真正的匹配在下面逐维判定
            stmt = stmt.where(PesticideProduct.registered_crops.is_not(None))
        products = list(self.db.scalars(stmt).all())

        ingredients = self._load_ingredients()
        usage = self._recent_usage(user_id)

        for p in products:
            check = self._check_one(p, ingredients, crop_name, disease_name,
                                    growth_stage, usage, days_to_harvest)
            result.checked += 1
            if check.passed:
                result.approved.append(check)
            else:
                result.blocked.append(check)

        result.approved.sort(key=lambda c: len(c.warnings))
        if result.approved:
            result.message = (f"共校验 {result.checked} 个登记产品，"
                              f"{len(result.approved)} 个通过规则校验")
        else:
            result.hard_blocked = settings.rule_hard_block
            # 区分「真的无登记」与「有登记但阶段不符」：后者的药是存在的，
            # 统一句「没有找到」会误导用户以为病害无药可治（2026-09-28 实测）
            stage_only = [c for c in result.blocked
                          if list(c.blocked_reasons) == ["stage_mismatch"]]
            if stage_only:
                gs = result.growth_stage or "当前阶段"
                result.message = (
                    f"有 {len(stage_only)} 个产品登记了「{disease_name}」，"
                    f"但都不适用于你选择的生长阶段（{gs}）。"
                    "请核对这些产品标注的适用阶段，或咨询当地植保部门。")
            else:
                result.message = ("没有找到符合条件的登记农药。"
                                  "请以当前产品标签为准，或咨询当地植保部门。")
        return result

    # ------------------------------------------------------------------ #
    def _check_one(self, p: PesticideProduct,
                   ingredients: dict[str, PesticideActiveIngredient],
                   crop_name: str, disease_name: str, growth_stage: str | None,
                   usage: list[PesticideUsage],
                   days_to_harvest: int | None) -> RuleCheck:
        chk = RuleCheck(product_id=p.id, product_name=p.product_name)

        # ---- 0. 数据可信度前置：状态、审核、来源（文档难点 12）----
        if p.status != "active":
            chk.blocked_reasons.append("no_data")
        if p.review_status != "approved":
            chk.blocked_reasons.append("unverified")
        if not p.source_url:
            chk.blocked_reasons.append("source_missing")

        registered_crops = _norm_list(p.registered_crops)
        target_pests = _norm_list(p.target_pests)
        stages = _norm_list(p.applicable_stages)

        # ---- 1. 作物匹配 ----
        chk.matched["crop"] = crop_name in registered_crops
        if not chk.matched["crop"]:
            chk.blocked_reasons.append("crop_mismatch")

        # ---- 2. 防治对象匹配 ----
        chk.matched["target"] = disease_name in target_pests
        if not chk.matched["target"]:
            chk.blocked_reasons.append("target_mismatch")

        # ---- 3. 使用阶段匹配（登记数据里没有这个字段，靠人工标注）----
        if stages:
            chk.matched["stage"] = (not growth_stage) or (growth_stage in stages)
        else:
            chk.matched["stage"] = True   # 未标注阶段限制，视为不限
        if not chk.matched["stage"]:
            chk.blocked_reasons.append("stage_mismatch")

        # ---- 4. 安全间隔期 ----
        chk.safety_interval_days = p.safety_interval_days
        if (days_to_harvest is not None and p.safety_interval_days is not None
                and days_to_harvest < p.safety_interval_days):
            chk.warnings.append("safety_interval")

        # ---- 5. 历史用药与抗性 ----
        names = _norm_list(p.active_ingredient_names)
        chk.active_ingredients = names
        groups: list[str] = []
        for n in names:
            ing = ingredients.get(n)
            if ing and ing.resistance_group:
                groups.append(ing.resistance_group)
            if ing and ing.toxicity == "high":
                chk.warnings.append("toxicity_high")
        chk.resistance_groups = list(dict.fromkeys(groups))
        if chk.resistance_groups:
            recent_groups = [u.resistance_group for u in usage if u.resistance_group]
            for g in chk.resistance_groups:
                if recent_groups.count(g) >= settings.resistance_repeat_limit:
                    chk.warnings.append("resistance")
                    break

        # ---- 输出：剂量只取标签原文 ----
        chk.label_dosage_text = p.dosage_text
        chk.source_url = p.source_url
        chk.data_verified_at = (p.data_verified_at.isoformat()
                                if p.data_verified_at else None)
        chk.blocked_reasons = list(dict.fromkeys(chk.blocked_reasons))
        chk.warnings = list(dict.fromkeys(chk.warnings))
        chk.passed = not chk.blocked_reasons
        return chk
