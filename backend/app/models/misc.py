"""天气记录、农事任务、对话记录（文档第五章 weather_records / farm_tasks /
chat_messages）。"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Integer,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import PKMixin, SoftDeleteMixin, TimestampMixin

# 农事任务类型
TASK_TYPES = ("patrol", "fertilize", "irrigate", "spray", "recheck", "other")
TASK_STATUS = ("pending", "done", "cancelled")


class WeatherRecord(Base, PKMixin, TimestampMixin):
    """天气数据。字段刻意按「能参与风险计算」设计（文档难点 7）：
    温度、湿度、降雨小时数、风速都是结构化数值，而不是一句天气描述。
    """
    __tablename__ = "weather_records"

    field_id: Mapped[int | None] = mapped_column(
        ForeignKey("fields.id", ondelete="SET NULL"), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    record_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    temp_max: Mapped[float | None] = mapped_column(Float)
    temp_min: Mapped[float | None] = mapped_column(Float)
    temp_avg: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)          # 相对湿度 %
    precipitation_mm: Mapped[float | None] = mapped_column(Float)  # 降雨量
    rain_hours: Mapped[float | None] = mapped_column(Float)         # 降雨持续小时
    wind_speed: Mapped[float | None] = mapped_column(Float)         # 风速 m/s
    weather_text: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str | None] = mapped_column(String(32))
    is_mock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)


class FarmTask(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    """农事任务 / 农事日历。

    ★ deleted_at 不是可有可无的：任务表里带着 diagnosis_id，是「某次诊断之后
      建议做的事」。硬删任务等于抹掉那次诊断的后续动作，之后复盘
      「建议了巡田，农户到底去没去」就没有依据了（文档难点 13）。
      另外 /farm/overview 与 /farm/calendar 都是按 deleted_at 过滤的，
      缺这一列会让这两个接口直接报错。
    """
    __tablename__ = "farm_tasks"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    field_id: Mapped[int | None] = mapped_column(
        ForeignKey("fields.id", ondelete="SET NULL"), index=True)
    crop_id: Mapped[int | None] = mapped_column(
        ForeignKey("crops.id", ondelete="SET NULL"))
    diagnosis_id: Mapped[int | None] = mapped_column(
        ForeignKey("diagnosis_records.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    task_type: Mapped[str] = mapped_column(String(16), nullable=False,
                                           default="other", index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending",
                                        index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback: Mapped[str | None] = mapped_column(Text)
    # 由哪个状态节点生成，便于追溯
    created_by_node: Mapped[str | None] = mapped_column(String(24))


class ChatMessage(Base, PKMixin, TimestampMixin):
    """Agent 对话记录。references 存 RAG 引用来源，保证回答可追溯。"""
    __tablename__ = "chat_messages"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user/assistant/tool
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Agent 执行轨迹：走了哪些状态节点、调了哪些工具
    trace: Mapped[list | None] = mapped_column(JSONB, default=list)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, default=list)
    references: Mapped[list | None] = mapped_column(JSONB, default=list)
    intent: Mapped[str | None] = mapped_column(String(32), index=True)
    token_usage: Mapped[dict | None] = mapped_column(JSONB)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    diagnosis_id: Mapped[int | None] = mapped_column(
        ForeignKey("diagnosis_records.id", ondelete="SET NULL"))
