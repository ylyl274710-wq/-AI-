"""农事任务与日历（文档「农事提醒」落地）。

任务有两个来源：
  · Agent 自动生成 —— 诊断完之后建议「3 天后复查」，带 created_by_node
  · 农户手动添加 —— 自己记得要打药、要浇水

★ 为什么任务表要保留 created_by_node：出了问题要能分清「系统建议错了」
  还是「农户自己记错了」。两者混在一张表里看不出来源，复盘时全是废话。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.farm import Field, Farm
from app.models.misc import TASK_STATUS, TASK_TYPES, FarmTask
from app.models.user import User
from app.schemas import TaskIn, TaskOut

router = APIRouter(prefix="/tasks", tags=["农事任务"])


def _own_field(db: Session, user: User, field_id: int | None) -> None:
    """校验地块归属。不校验的话，任何人构造一个 field_id 就能把任务
    挂到别人的地上（难点 14 权限隐私）。"""
    if field_id is None:
        return
    ok = db.scalar(select(Field.id).join(Farm, Field.farm_id == Farm.id)
                   .where(Field.id == field_id, Farm.owner_id == user.id))
    if ok is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "地块不存在")


@router.get("", response_model=list[TaskOut], summary="任务列表")
def list_tasks(
    status_: str | None = Query(None, alias="status"),
    field_id: int | None = None,
    due_within: int | None = Query(None, ge=1, le=180,
                                   description="只看未来 N 天内到期的"),
    include_overdue: bool = Query(True, description="是否包含已过期未完成的"),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[FarmTask]:
    stmt = select(FarmTask).where(FarmTask.user_id == user.id,
                                  FarmTask.deleted_at.is_(None))
    if status_:
        if status_ not in TASK_STATUS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"status 只能是 {TASK_STATUS}")
        stmt = stmt.where(FarmTask.status == status_)
    if field_id is not None:
        stmt = stmt.where(FarmTask.field_id == field_id)

    today = date.today()
    if due_within is not None:
        end = today + timedelta(days=due_within)
        stmt = stmt.where(FarmTask.due_date.is_not(None),
                          FarmTask.due_date <= end)
        if not include_overdue:
            stmt = stmt.where(FarmTask.due_date >= today)

    # 到期的排前面；没有日期的沉底（.nullslast()，否则 PostgreSQL 默认
    # 把 NULL 排在最前，一堆没定日期的任务会盖住今天该做的事）
    return list(db.scalars(
        stmt.order_by(FarmTask.due_date.asc().nullslast(),
                      FarmTask.priority.asc(), FarmTask.id.desc())
        .limit(limit)).all())


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED,
             summary="新建任务")
def create_task(payload: TaskIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> FarmTask:
    if payload.task_type not in TASK_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"task_type 只能是 {TASK_TYPES}")
    _own_field(db, user, payload.field_id)
    task = FarmTask(user_id=user.id, status="pending",
                    created_by_node="manual", **payload.model_dump())
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.patch("/{task_id}", response_model=TaskOut, summary="修改任务")
def update_task(task_id: int, payload: TaskIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> FarmTask:
    task = _own_task(db, user, task_id)
    data = payload.model_dump(exclude_unset=True)
    if "field_id" in data:
        _own_field(db, user, data["field_id"])
    if "task_type" in data and data["task_type"] not in TASK_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"task_type 只能是 {TASK_TYPES}")
    for k, v in data.items():
        setattr(task, k, v)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.post("/{task_id}/done", response_model=TaskOut, summary="标记完成")
def complete_task(task_id: int, feedback: str = Query("", max_length=500),
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> FarmTask:
    """完成任务，可附一句反馈。

    ★ 为什么完成时要收 feedback：这是判断「Agent 的建议到底有没有用」
      的唯一信号源。系统说「3 天后复查」，农户复查完说「确实好转了」，
      这条建议才算被验证过；一直没人填，就说明建议本身可能没意义。
    """
    task = _own_task(db, user, task_id)
    task.status = "done"
    task.done_at = datetime.now(timezone.utc)
    task.feedback = feedback or None
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="删除任务（软删除）")
def delete_task(task_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> None:
    task = _own_task(db, user, task_id)
    task.deleted_at = datetime.now(timezone.utc)
    db.add(task)
    db.commit()


def _own_task(db: Session, user: User, task_id: int) -> FarmTask:
    task = db.scalar(select(FarmTask).where(FarmTask.id == task_id,
                                            FarmTask.user_id == user.id,
                                            FarmTask.deleted_at.is_(None)))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    return task
