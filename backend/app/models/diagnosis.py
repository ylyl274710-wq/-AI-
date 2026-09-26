"""诊断记录（文档第五章 diagnosis_records）。

★ 两处对原文档的修正：
  1. 原文档把「图片」写在 diagnosis_records 里，但一次诊断可以传多张照片
     （文档难点 1 明确要求用户补充叶片正反面 / 整株 / 病斑近照），
     是一对多关系，所以拆出 diagnosis_images 表。
  2. 增加 confidence_detail 字段保存置信度的**计算过程**，而不是只存一个数字。
     因为多模态大模型自报的置信度不可校准，必须让每个分量可追溯、可复算。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String,
                        Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import PKMixin, SoftDeleteMixin, TimestampMixin

# 诊断状态机（文档第七章的兜底分支都要有对应的状态）
ST_PENDING = "pending"                  # 已受理，推理中
ST_SUCCEEDED = "succeeded"              # 置信度达标，给出结论
ST_NEEDS_MORE_INFO = "needs_more_info"  # 闸门 A：置信度不足，要求补充拍照
ST_REJECTED = "rejected"                # 低于拒识阈值，判定无法识别
ST_FAILED = "failed"                    # 模型/服务异常


class DiagnosisRecord(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """一次诊断的完整记录。

    ★ 必须有 deleted_at：诊断记录是「用药历史 / 抗性判断 / 天气风险校准」
      的原始依据，农户在界面上点「删除」时不能真的抹掉 —— 那会让
      同一块地的前后两次诊断失去关联。而且 /diagnosis/history 与
      /farm/overview 都是按 deleted_at 过滤的，缺这一列这两个接口直接报错。
    """
    __tablename__ = "diagnosis_records"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    field_id: Mapped[int | None] = mapped_column(
        ForeignKey("fields.id", ondelete="SET NULL"), index=True)
    crop_id: Mapped[int | None] = mapped_column(
        ForeignKey("crops.id", ondelete="SET NULL"), index=True)

    input_source: Mapped[str] = mapped_column(String(16), nullable=False,
                                              default="image")  # image/text/voice
    input_text: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(24), nullable=False,
                                        default=ST_PENDING, index=True)
    # 最终采用的结论
    top1_disease_name: Mapped[str | None] = mapped_column(String(128), index=True)
    top1_disease_id: Mapped[int | None] = mapped_column(
        ForeignKey("disease_records.id", ondelete="SET NULL"))
    # ★ 可计算置信度（不是模型自报的措辞）
    confidence: Mapped[float | None] = mapped_column(Float, index=True)
    confidence_detail: Mapped[dict | None] = mapped_column(JSONB)

    # 完整结构化结果：候选列表、证据链、检索来源、规则校验结果
    result: Mapped[dict | None] = mapped_column(JSONB)
    # 面向用户的最终回答（三段式）
    answer_text: Mapped[str | None] = mapped_column(Text)

    # 是否处于无 Key 降级模式（便于区分「真实模型结论」和「规则兜底结论」）
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    llm_model: Mapped[str | None] = mapped_column(String(64))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    # ---- 效果反馈（文档难点 13）----
    feedback_resolved: Mapped[bool | None] = mapped_column(Boolean)
    feedback_note: Mapped[str | None] = mapped_column(Text)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    images: Mapped[list["DiagnosisImage"]] = relationship(
        back_populates="diagnosis", cascade="all, delete-orphan", lazy="selectin")


class DiagnosisImage(Base, PKMixin, TimestampMixin):
    __tablename__ = "diagnosis_images"

    diagnosis_id: Mapped[int] = mapped_column(
        ForeignKey("diagnosis_records.id", ondelete="CASCADE"), nullable=False,
        index=True)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(String(512))
    thumb_key: Mapped[str | None] = mapped_column(String(255))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bytes_size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    # 拍摄角度：叶片正面/背面/整株/病斑近景 —— 支撑闸门 A 的「补充拍照」提示
    shot_type: Mapped[str | None] = mapped_column(String(24))
    # EXIF 是否已剥离（GPS 会泄露农田位置，文档难点 14）
    exif_stripped: Mapped[bool] = mapped_column(Boolean, nullable=False,
                                                default=True)
    # 该图的单独诊断结论，用于计算多图一致性
    single_result: Mapped[dict | None] = mapped_column(JSONB)

    diagnosis: Mapped["DiagnosisRecord"] = relationship(back_populates="images")
