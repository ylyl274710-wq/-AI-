"""农田档案：农场 / 地块 / 作物 / 生长记录，以及首页总览。

这是「个性化农田记忆」的读写入口（文档第十二章创新点 5）。
所有查询都按 owner_id 过滤 —— 农户之间绝不能互相看到地块数据（难点 14）。
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.diagnosis import DiagnosisRecord
from app.models.farm import Crop, CropGrowthRecord, Farm, Field
from app.models.misc import FarmTask
from app.models.user import User
from app.schemas import (CropOut, FarmIn, FarmOut, FarmOverviewOut, FieldOut,
                         GrowthRecordIn, GrowthRecordOut, RiskOut, TaskOut,
                         WeatherOut)
from app.services.weather import WeatherService, risks_for_crop, summarize

router = APIRouter(prefix="/farm", tags=["农田档案"])


def _own_farm(db: Session, user: User, farm_id: int) -> Farm:
    farm = db.scalar(select(Farm).where(Farm.id == farm_id,
                                        Farm.owner_id == user.id,
                                        Farm.deleted_at.is_(None)))
    if farm is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "农场不存在")
    return farm


# --------------------------------------------------------------------------- #
# 作物字典
# --------------------------------------------------------------------------- #


@router.get("/crops", response_model=list[CropOut])
def list_crops(db: Session = Depends(get_db),
               _: User = Depends(get_current_user)) -> list[Crop]:
    return list(db.scalars(
        select(Crop).where(Crop.deleted_at.is_(None)).order_by(Crop.name)).all())


# --------------------------------------------------------------------------- #
# 农场与地块
# --------------------------------------------------------------------------- #


@router.get("/farms", response_model=list[FarmOut])
def list_farms(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> list[Farm]:
    return list(db.scalars(
        select(Farm)
        .options(selectinload(Farm.fields))
        .where(Farm.owner_id == user.id, Farm.deleted_at.is_(None))
        .order_by(Farm.id)).all())


@router.post("/farms", response_model=FarmOut,
             status_code=status.HTTP_201_CREATED)
def create_farm(payload: FarmIn, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> Farm:
    farm = Farm(owner_id=user.id, **payload.model_dump())
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm


@router.patch("/farms/{farm_id}", response_model=FarmOut)
def update_farm(farm_id: int, payload: FarmIn,
                db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> Farm:
    farm = _own_farm(db, user, farm_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(farm, k, v)
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm


@router.delete("/farms/{farm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_farm(farm_id: int, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)) -> None:
    """软删除。历史诊断记录还要引用这块地，硬删会让历史数据断链。"""
    farm = _own_farm(db, user, farm_id)
    farm.deleted_at = func.now()
    db.add(farm)
    db.commit()


@router.post("/farms/{farm_id}/fields", response_model=FieldOut,
             status_code=status.HTTP_201_CREATED)
def create_field(farm_id: int, payload: dict,
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> Field:
    _own_farm(db, user, farm_id)
    allowed = {"name", "area_mu", "soil_type", "irrigation_type", "lat", "lng"}
    data = {k: v for k, v in payload.items() if k in allowed}
    if not data.get("name"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "地块名称必填")
    field = Field(farm_id=farm_id, **data)
    db.add(field)
    db.commit()
    db.refresh(field)
    return field


# --------------------------------------------------------------------------- #
# 生长记录
# --------------------------------------------------------------------------- #


@router.get("/growth-records", response_model=list[GrowthRecordOut])
def list_growth_records(field_id: int | None = None,
                        db: Session = Depends(get_db),
                        user: User = Depends(get_current_user),
                        ) -> list[CropGrowthRecord]:
    stmt = (select(CropGrowthRecord)
            .join(Field, CropGrowthRecord.field_id == Field.id)
            .join(Farm, Field.farm_id == Farm.id)
            .where(Farm.owner_id == user.id,
                   CropGrowthRecord.deleted_at.is_(None)))
    if field_id is not None:
        stmt = stmt.where(CropGrowthRecord.field_id == field_id)
    return list(db.scalars(stmt.order_by(CropGrowthRecord.id.desc())).all())


@router.post("/growth-records", response_model=GrowthRecordOut,
             status_code=status.HTTP_201_CREATED)
def create_growth_record(payload: GrowthRecordIn,
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user),
                         ) -> CropGrowthRecord:
    # 校验地块归属，避免把记录挂到别人的地上
    field = db.scalar(select(Field).join(Farm, Field.farm_id == Farm.id)
                      .where(Field.id == payload.field_id,
                             Farm.owner_id == user.id))
    if field is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "地块不存在")
    if payload.crop_id is None or db.scalar(
            select(Crop).where(Crop.id == payload.crop_id)) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "作物不存在")

    # 同一地块只保留一条「当前」记录，否则 Agent 读上下文时不知道信哪个
    db.query(CropGrowthRecord).filter(
        CropGrowthRecord.field_id == payload.field_id,
        CropGrowthRecord.is_current.is_(True),
    ).update({"is_current": False})

    rec = CropGrowthRecord(**payload.model_dump(), is_current=True)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


# --------------------------------------------------------------------------- #
# 首页总览
# --------------------------------------------------------------------------- #


@router.get("/overview", response_model=FarmOverviewOut)
def overview(db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> FarmOverviewOut:
    farms = list(db.scalars(
        select(Farm).options(selectinload(Farm.fields))
        .where(Farm.owner_id == user.id, Farm.deleted_at.is_(None))
        .order_by(Farm.id)).all())

    current: list[dict] = []
    for rec in db.scalars(
            select(CropGrowthRecord)
            .join(Field, CropGrowthRecord.field_id == Field.id)
            .join(Farm, Field.farm_id == Farm.id)
            .where(Farm.owner_id == user.id,
                   CropGrowthRecord.is_current.is_(True),
                   CropGrowthRecord.deleted_at.is_(None))).all():
        crop = db.get(Crop, rec.crop_id)
        field = db.get(Field, rec.field_id)
        days = None
        if rec.expected_harvest_date:
            days = (rec.expected_harvest_date - date.today()).days
        current.append({
            "field_id": rec.field_id,
            "field_name": field.name if field else None,
            "crop_name": crop.name if crop else None,
            "variety": rec.variety,
            "stage": rec.stage,
            "days_to_harvest": days,
        })

    tasks = list(db.scalars(
        select(FarmTask).where(FarmTask.user_id == user.id,
                               FarmTask.status == "pending",
                               FarmTask.deleted_at.is_(None))
        .order_by(FarmTask.due_date.asc().nullslast(), FarmTask.priority)
        .limit(10)).all())

    recent = []
    for rec in db.scalars(
            select(DiagnosisRecord)
            .where(DiagnosisRecord.user_id == user.id,
                   DiagnosisRecord.deleted_at.is_(None))
            .order_by(DiagnosisRecord.id.desc()).limit(5)).all():
        recent.append({
            "id": rec.id,
            "status": rec.status,
            "top1": rec.top1_disease_name,
            "confidence": rec.confidence,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "input_text": rec.input_text,
        })

    # ---- 天气与风险：取第一块有作物的地块做风险计算 ----
    #
    # ★ 这一段的接口名踩过坑，记下来：WeatherService 的真实方法只有
    #   get_recent(field_id, lat, lng, days)，**没有** get() / to_dict() /
    #   candidate_diseases()。to_dict() 是 RiskAssessment 上的方法，不是
    #   服务上的。风险候选要从 disease_records 里按 crop_names 查，
    #   不能让天气服务「猜」有哪些病。
    weather_out: WeatherOut | None = None
    risks: list[RiskOut] = []
    if current:
        first_field = db.get(Field, current[0]["field_id"])
        svc = WeatherService(db)
        snaps = svc.get_recent(
            field_id=current[0]["field_id"],
            lat=first_field.lat if first_field else None,
            lng=first_field.lng if first_field else None,
            days=3)
        summary = summarize(snaps)
        if summary:
            weather_out = WeatherOut(**summary)
        crop_name, stage = current[0]["crop_name"], current[0]["stage"]
        if crop_name:
            risks = [RiskOut(**r.to_dict())
                     for r in risks_for_crop(db, crop_name, stage, snaps)]

    return FarmOverviewOut(
        user=user, farms=farms, current_crops=current,
        pending_tasks=[TaskOut.model_validate(t) for t in tasks],
        recent_diagnoses=recent, weather=weather_out, risks=risks)


@router.get("/calendar", response_model=list[TaskOut])
def calendar(days: int = 14, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> list[FarmTask]:
    """未来 N 天的农事日历。"""
    end = date.today() + timedelta(days=max(1, min(days, 90)))
    return list(db.scalars(
        select(FarmTask).where(FarmTask.user_id == user.id,
                               FarmTask.status == "pending",
                               FarmTask.deleted_at.is_(None),
                               FarmTask.due_date.is_not(None),
                               FarmTask.due_date <= end)
        .order_by(FarmTask.due_date)).all())
