"""认证与密码（文档难点 14）。

用 bcrypt 直接做哈希 + PyJWT 签发令牌，不引 passlib
（passlib 1.7.4 与 bcrypt 4.x 存在已知的版本兼容告警，直接用 bcrypt 更省事）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.user import ROLE_ADMIN, ROLE_AGRONOMIST, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/token",
                                    auto_error=False)

# bcrypt 最多处理 72 字节；拒绝超长密码，避免不同密码产生相同凭证。
_BCRYPT_MAX_BYTES = 72


def hash_password(plain: str) -> str:
    raw = plain.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        raise ValueError("密码 UTF-8 编码不能超过 72 字节")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        raw = plain.encode("utf-8")
        if len(raw) > _BCRYPT_MAX_BYTES:
            return False
        return bcrypt.checkpw(raw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret,
                          algorithms=[settings.jwt_algorithm],
                          options={"require": ["sub", "iat", "exp"]})
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录凭证无效")


def get_current_user(token: str | None = Depends(oauth2_scheme),
                     db: Session = Depends(get_db)) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    payload = decode_token(token)
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录凭证无效")
    user = db.scalar(select(User).where(User.id == user_id,
                                        User.deleted_at.is_(None)))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "账号不存在或已停用")
    return user


def get_optional_user(token: str | None = Depends(oauth2_scheme),
                      db: Session = Depends(get_db)) -> User | None:
    if not token:
        return None
    try:
        return get_current_user(token, db)
    except HTTPException:
        return None


def require_role(*roles: str):
    """RBAC 依赖：require_role(ROLE_ADMIN) 这样用。"""
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "没有访问该资源的权限")
        return user
    return _dep


require_admin = require_role(ROLE_ADMIN)
require_agronomist = require_role(ROLE_ADMIN, ROLE_AGRONOMIST)
