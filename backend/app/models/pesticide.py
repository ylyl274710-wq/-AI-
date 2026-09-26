"""农药结构化数据与规则引擎的数据基础（文档难点 3、难点 12）。

★ 重要提醒（实施时第一个真正的卡点）：
   文档说「建立 pesticide_products 表……先判断作物是否匹配、防治对象是否匹配、
   使用阶段是否匹配」，但没有说数据从哪来。中国农药登记信息没有开放 API，
   且登记数据里**不存在「使用阶段」这个字段**，需要人工标注。
   因此本项目采用「人工整理白名单 + 来源可追溯」的方式，
   数据规模控制在几十条，每条都带 source_url 与更新时间。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Index, Integer,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import PKMixin, SoftDeleteMixin, TimestampMixin, VersionedMixin

# 农药类别
PESTICIDE_CATEGORIES = ("insecticide", "fungicide", "herbicide", "acaricide",
                        "other")
# 毒性分级（登记标签用）
TOXICITY_LEVELS = ("low", "moderate", "high")


class PesticideActiveIngredient(Base, PKMixin, TimestampMixin):
    """有效成分标准名 —— 文档难点 4 提到的「有效成分标准名 / 作用机制分类」
    数据字典，但原文档第五章没有对应的表。这里补上。

    作用机制分类是抗性管理的基础（同一作用机制连续使用会导致抗药性）。
    """
    __tablename__ = "pesticide_active_ingredients"

    standard_name: Mapped[str] = mapped_column(String(128), unique=True,
                                               nullable=False, index=True)
    aliases: Mapped[list | None] = mapped_column(JSONB, default=list)
    english_name: Mapped[str | None] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 作用机制分类，例如 "烟碱型乙酰胆碱受体竞争性调节剂"
    mode_of_action: Mapped[str | None] = mapped_column(String(160))
    # 抗性管理分组（FRAC / IRAC 分组号），用于判断连续使用
    resistance_group: Mapped[str | None] = mapped_column(String(32), index=True)
    toxicity: Mapped[str | None] = mapped_column(String(16))
    remark: Mapped[str | None] = mapped_column(Text)


class PesticideProduct(Base, PKMixin, TimestampMixin, SoftDeleteMixin,
                       VersionedMixin):
    """农药产品（白名单条目）。

    只有 status='active' 且 review_status='approved' 的记录才允许被推荐。
    """
    __tablename__ = "pesticide_products"

    product_name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    registration_no: Mapped[str | None] = mapped_column(String(64), index=True)
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    formulation: Mapped[str | None] = mapped_column(String(32))  # 剂型：悬浮剂/乳油…

    # 有效成分：存标准名列表，方便与 pesticide_active_ingredients 对应
    active_ingredient_names: Mapped[list | None] = mapped_column(JSONB, default=list)
    active_ingredient_ids: Mapped[list | None] = mapped_column(JSONB, default=list)

    # ---- 规则引擎依赖的四个匹配维度 ----
    # ★ 同 knowledge.py 的 crop_names：数组列不能走 btree，这里用 GIN 索引
    #   （见 __table_args__），否则建表会报 "data type json has no default
    #   operator class for access method btree"。
    registered_crops: Mapped[list | None] = mapped_column(JSONB, default=list)
    target_pests: Mapped[list | None] = mapped_column(JSONB, default=list)
    # ★ 登记数据里没有这个字段，需要人工标注
    applicable_stages: Mapped[list | None] = mapped_column(JSONB, default=list)
    safety_interval_days: Mapped[int | None] = mapped_column(Integer)

    dosage_text: Mapped[str | None] = mapped_column(Text)   # 标签上的使用方法原文
    precautions: Mapped[str | None] = mapped_column(Text)
    label_url: Mapped[str | None] = mapped_column(String(512))

    # ---- 可信度与溯源（文档难点 12）----
    source_url: Mapped[str | None] = mapped_column(String(512))
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="SET NULL"))
    # 数据核对时间：展示层要显示「数据更新时间」
    data_verified_at: Mapped[date | None] = mapped_column(Date)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False,
                                               default="pending", index=True)

    source: Mapped["KnowledgeSource | None"] = relationship(lazy="joined")  # noqa: F821

    __table_args__ = (
        Index("ix_pesticide_products_registered_crops_gin", "registered_crops",
              postgresql_using="gin"),
        Index("ix_pesticide_products_target_pests_gin", "target_pests",
              postgresql_using="gin"),
    )


class PesticideUsage(Base, PKMixin, TimestampMixin):
    """用户历史用药记录（文档难点 8）。

    ★ 只有 confirmed=True 的记录才会进入规则引擎的判断，
      未经用户确认的 OCR/语音草稿不算正式历史（原文档明确要求）。
    """
    __tablename__ = "pesticide_usage"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    field_id: Mapped[int | None] = mapped_column(
        ForeignKey("fields.id", ondelete="SET NULL"), index=True)
    crop_id: Mapped[int | None] = mapped_column(
        ForeignKey("crops.id", ondelete="SET NULL"))
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("pesticide_products.id", ondelete="SET NULL"))
    # 用户实际说的/包装上印的原始名称，可能匹配不上白名单
    product_name_raw: Mapped[str | None] = mapped_column(String(160))
    active_ingredient_name: Mapped[str | None] = mapped_column(String(128))
    resistance_group: Mapped[str | None] = mapped_column(String(32), index=True)
    dose_text: Mapped[str | None] = mapped_column(String(160))
    area_mu: Mapped[float | None] = mapped_column(Float)
    used_at: Mapped[date | None] = mapped_column(Date, index=True)
    # 记录来源：manual 手动 / ocr 拍包装 / voice 语音
    record_source: Mapped[str] = mapped_column(String(16), nullable=False,
                                               default="manual")
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                            index=True)
    note: Mapped[str | None] = mapped_column(Text)

    product: Mapped["PesticideProduct | None"] = relationship(lazy="joined")


class PesticideOcrDraft(Base, PKMixin, TimestampMixin):
    """拍农药包装后的 OCR 草稿，等待用户确认（文档难点 8）。

    ★ 这一环在文档里被低估了：农药包装是弧形、竖排、低对比度小字，
      通用 OCR 准确率很低；且识别出来是**商品名**，还要再映射到有效成分。
      所以这里刻意设计成「草稿 → 用户确认 → 正式记录」两段式，
      识别不确定时直接让用户从白名单里搜选，而不是硬猜。
    """
    __tablename__ = "pesticide_ocr_drafts"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    image_key: Mapped[str | None] = mapped_column(String(255))
    raw_text: Mapped[str | None] = mapped_column(Text)
    matched_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("pesticide_products.id", ondelete="SET NULL"))
    match_score: Mapped[float | None] = mapped_column(Float)
    candidates: Mapped[list | None] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
