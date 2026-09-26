"""天气与病害风险（文档难点 7）。

★ 一条硬规矩：天气只做「风险提示」，绝不出「诊断结论」。
  天气说「高温高湿、适合霜霉病发生」，不等于「你的番茄得了霜霉病」。
  前者是概率，后者是断言 —— 混在一起会让农户照着天气预报去打药。
  所以 /weather/risk 返回的每一条都带 advice，且 advice 里
  只会出现「建议巡田 / 多留意」这类措辞，不会出现「立即用药」。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.farm import Field, Farm
from app.models.user import User
from app.schemas import RiskOut, WeatherOut
from app.services.weather import WeatherService, risks_for_crop, summarize

router = APIRouter(prefix="/weather", tags=["天气"])


def _field_coords(db: Session, user: User,
                  field_id: int | None) -> tuple[int | None, float | None,
                                                 float | None]:
    """取地块坐标。顺带做归属校验 —— 不加这一步，传别人的 field_id
    就能读到别人的地块位置（难点 14）。

    field_id 未指定时回退到用户第一个有坐标的地块：真实天气源需要经纬度，
    首页的「当前天气」没带 field_id，不回退的话真实模式下永远只能给
    演示数据。回退的是用户自己的地块，语义是「你家农场的天气」，
    不存在越权。
    """
    if field_id is None:
        row = db.execute(
            select(Field.id, Field.lat, Field.lng)
            .join(Farm, Field.farm_id == Farm.id)
            .where(Farm.owner_id == user.id, Field.lat.is_not(None),
                   Field.lng.is_not(None))
            .order_by(Field.id)).first()
        if row is None:
            return None, None, None
        return row[0], row[1], row[2]
    row = db.execute(
        select(Field.lat, Field.lng)
        .join(Farm, Field.farm_id == Farm.id)
        .where(Field.id == field_id, Farm.owner_id == user.id)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "地块不存在")
    return field_id, row[0], row[1]


@router.get("/current", response_model=WeatherOut, summary="当前天气")
def current(field_id: int | None = None, days: int = Query(3, ge=1, le=7),
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> WeatherOut:
    fid, lat, lng = _field_coords(db, user, field_id)
    snaps = WeatherService(db).get_recent(fid, lat, lng, days=days)
    summary = summarize(snaps) or {}
    return WeatherOut(**summary)


@router.get("/risk", response_model=list[RiskOut], summary="天气相关的病害风险")
def risk(crop_name: str, growth_stage: str | None = None,
         field_id: int | None = None,
         days: int = Query(3, ge=1, le=7),
         limit: int = Query(5, ge=1, le=20),
         db: Session = Depends(get_db),
         user: User = Depends(get_current_user)) -> list[RiskOut]:
    """给指定作物列出「当前天气适合发生」的病害，按风险分排序。

    ★ 这里不返回任何置信度、不返回 top1 —— 它不是一个诊断接口。
      前端展示时应与「诊断结果」视觉上明显区分开，避免农户误读。
    """
    fid, lat, lng = _field_coords(db, user, field_id)
    snaps = WeatherService(db).get_recent(fid, lat, lng, days=days)
    return [RiskOut(**r.to_dict())
            for r in risks_for_crop(db, crop_name, growth_stage, snaps,
                                    limit=limit)]
