"""模型公共基类与混入。

统一约定（文档里没写，但缺了后面会很难受）：
- 所有业务表都有 created_at / updated_at；
- 所有会被人修改的表都有软删除 deleted_at，避免「用户点错一次数据就没了」；
- 知识类表额外有 version / status，用于文档难点 12 的「知识过期」治理。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PKMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None)


class VersionedMixin:
    """知识类条目的版本与状态（文档难点 12）。"""
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # draft / reviewed / active / deprecated
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft",
                                        index=True)


__all__ = ["Base", "PKMixin", "TimestampMixin", "SoftDeleteMixin", "VersionedMixin"]
