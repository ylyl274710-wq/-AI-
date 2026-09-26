"""对话历史（文档「对话式交互」的落地）。

前端需要三件事：
  1. 列出我有过哪些会话（侧边栏）
  2. 打开某个会话看完整来回（含当时的检索引用与执行轨迹）
  3. 删掉不想留的会话

★ 关于删除：chat_messages 表只有 PKMixin + TimestampMixin，**没有**
  SoftDeleteMixin，所以这里只能真删。这是模型层的选择，不是 API 偷懒 ——
  对话内容不属于「有审计价值的历史数据」（诊断结论在 diagnosis_records 里
  另有留存），真删反而更符合隐私预期。如果以后要保留，得先给模型加
  deleted_at 列并生成迁移。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.misc import ChatMessage
from app.models.user import User
from app.schemas import ChatMessageOut, SessionOut

router = APIRouter(prefix="/chat", tags=["对话"])


@router.get("/sessions", response_model=list[SessionOut],
            summary="我的会话列表")
def list_sessions(limit: int = Query(30, ge=1, le=200),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> list[SessionOut]:
    rows = db.execute(
        select(ChatMessage.session_id,
               func.count(ChatMessage.id).label("n"),
               func.max(ChatMessage.created_at).label("last_at"))
        .where(ChatMessage.user_id == user.id)
        .group_by(ChatMessage.session_id)
        .order_by(func.max(ChatMessage.created_at).desc())
        .limit(limit)).all()

    out: list[SessionOut] = []
    for sid, n, last_at in rows:
        last_q = db.scalar(
            select(ChatMessage.content)
            .where(ChatMessage.session_id == sid,
                   ChatMessage.user_id == user.id,
                   ChatMessage.role == "user")
            .order_by(ChatMessage.id.desc()).limit(1))
        out.append(SessionOut(session_id=sid, last_at=last_at,
                              message_count=int(n), last_question=last_q))
    return out


@router.get("/sessions/{session_id}", response_model=list[ChatMessageOut],
            summary="单个会话的完整消息")
def session_detail(session_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> list[ChatMessage]:
    msgs = list(db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id,
               ChatMessage.user_id == user.id)
        .order_by(ChatMessage.id)).all())
    if not msgs:
        # ★ 返回 404 而不是空数组：会话不存在与「会话存在但还没消息」
        #   在前端是两种完全不同的状态，混在一起会让 UI 一直显示空白页。
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return msgs


@router.delete("/sessions/{session_id}", summary="删除会话")
def delete_session(session_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    n = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.user_id == user.id).delete(synchronize_session=False)
    db.commit()
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    return {"ok": True, "deleted": int(n)}
