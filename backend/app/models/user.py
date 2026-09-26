"""用户与权限（文档难点 14）。"""
from __future__ import annotations

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import PKMixin, SoftDeleteMixin, TimestampMixin

# RBAC 角色：farmer 普通种植户 / agronomist 农技员 / admin 管理员
ROLE_FARMER = "farmer"
ROLE_AGRONOMIST = "agronomist"
ROLE_ADMIN = "admin"
ROLES = (ROLE_FARMER, ROLE_AGRONOMIST, ROLE_ADMIN)


class User(Base, PKMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True,
                                          nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(128), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_FARMER,
                                      index=True)
    # 老人模式偏好（文档难点 9）：默认开启大字大按钮
    elder_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    city: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    farms: Mapped[list["Farm"]] = relationship(  # noqa: F821
        back_populates="owner", cascade="all, delete-orphan", lazy="selectin")
