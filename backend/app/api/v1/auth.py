"""认证：注册 / 登录 / 当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.security import (create_access_token, get_current_user,
                               hash_password, verify_password)
from app.models.user import ROLE_FARMER, User
from app.schemas import LoginIn, RegisterIn, TokenOut, UserOut, UserUpdateIn

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=UserOut,
             status_code=status.HTTP_201_CREATED)
def register(payload: RegisterIn, db: Session = Depends(get_db)) -> User:
    exists = db.scalar(select(User).where(User.username == payload.username))
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用")
    user = User(
        username=payload.username,
        display_name=payload.display_name or payload.username,
        phone=payload.phone or None,
        city=payload.city or None,
        hashed_password=hash_password(payload.password),
        role=ROLE_FARMER,
        # 文档难点 9：默认开启老人模式。面向中老年农户的产品，
        # 默认值应该是「字大、话少」，而不是让用户自己去设置里找开关。
        elder_mode=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, db: Session = Depends(get_db)) -> TokenOut:
    user = db.scalar(select(User).where(User.username == payload.username))
    # ★ 用户不存在与密码错误返回同一句话，避免被用来枚举账号
    if user is None or not verify_password(payload.password,
                                          user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不正确")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用")
    return TokenOut(access_token=create_access_token(user),
                    expires_in=settings.jwt_expire_minutes * 60)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(payload: UserUpdateIn, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> User:
    if payload.display_name is not None:
        user.display_name = payload.display_name
    if payload.city is not None:
        user.city = payload.city
    if payload.elder_mode is not None:
        user.elder_mode = payload.elder_mode
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
