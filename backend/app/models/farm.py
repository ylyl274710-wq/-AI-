"""农田数字档案：farms / fields / crops / crop_growth_records。

这是「个性化农田记忆」的载体（文档第十二章创新点 5），也是 Agent 的
Context 节点主要读取的数据源。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import (Boolean, Date, Float, ForeignKey, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import PKMixin, SoftDeleteMixin, TimestampMixin


class Farm(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """农田（一个农户可以有多块农田）。"""
    __tablename__ = "farms"

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    province: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(32))
    district: Mapped[str | None] = mapped_column(String(32))
    address: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    total_area_mu: Mapped[float | None] = mapped_column(Float)
    remark: Mapped[str | None] = mapped_column(Text)

    owner: Mapped["User"] = relationship(back_populates="farms")  # noqa: F821
    fields: Mapped[list["Field"]] = relationship(
        back_populates="farm", cascade="all, delete-orphan", lazy="selectin")


class Field(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """具体地块。诊断、农事任务、用药记录都挂在这一层。"""
    __tablename__ = "fields"

    farm_id: Mapped[int] = mapped_column(
        ForeignKey("farms.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    area_mu: Mapped[float | None] = mapped_column(Float)
    soil_type: Mapped[str | None] = mapped_column(String(32))
    irrigation_type: Mapped[str | None] = mapped_column(String(32))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    remark: Mapped[str | None] = mapped_column(Text)

    farm: Mapped["Farm"] = relationship(back_populates="fields")
    growth_records: Mapped[list["CropGrowthRecord"]] = relationship(
        back_populates="field", cascade="all, delete-orphan")


class Crop(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """作物标准名与品种。

    对应文档难点 4 的「统一数据字典」：name 是标准名，aliases 存俗称，
    检索与规则校验一律走标准名，避免同一作物多种叫法导致的匹配失败。
    """
    __tablename__ = "crops"

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False,
                                      index=True)
    aliases: Mapped[list | None] = mapped_column(JSONB, default=list)
    category: Mapped[str | None] = mapped_column(String(32))  # 茄果类/叶菜类/禾本科…
    # 生长阶段定义：[{"name":"苗期","days":20,"order":1}, ...]
    growth_stages: Mapped[list | None] = mapped_column(JSONB, default=list)
    description: Mapped[str | None] = mapped_column(Text)
    # 常见病虫害标准名列表，用于缩小检索范围
    common_disease_names: Mapped[list | None] = mapped_column(JSONB, default=list)
    is_high_frequency: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                    default=False)

    __table_args__ = (UniqueConstraint("name", name="uq_crops_name"),)


class CropGrowthRecord(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """地块的作物生长记录。

    ★ 加 deleted_at 的理由：这份记录是 Agent 判断「当前是什么作物、什么阶段」
      的唯一依据。农户改种之后旧记录不能硬删 —— 历史诊断记录里的
      crop_id 还指着它，硬删会让「当时是番茄得病」变成查不出来。
      而且 /farm/growth-records 与 /farm/overview 都是按 deleted_at 过滤的。
    """
    """某地块某作物的生长阶段记录。天气风险计算要用到 growth_stage。"""
    __tablename__ = "crop_growth_records"

    field_id: Mapped[int] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), nullable=False, index=True)
    crop_id: Mapped[int] = mapped_column(
        ForeignKey("crops.id", ondelete="RESTRICT"), nullable=False, index=True)
    variety: Mapped[str | None] = mapped_column(String(64))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    sowing_date: Mapped[date | None] = mapped_column(Date)
    stage_start_date: Mapped[date | None] = mapped_column(Date)
    expected_harvest_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True,
                                             index=True)
    note: Mapped[str | None] = mapped_column(Text)

    field: Mapped["Field"] = relationship(back_populates="growth_records")
    crop: Mapped["Crop"] = relationship(lazy="joined")
