"""天气服务与农业风险计算（文档难点 7）。

原文档说「将天气作为辅助特征，而不是诊断依据。先获取温度、湿度、降雨和风速，
再根据知识库中的发生条件计算风险等级」，但没定义「发生条件」的数据形态。

本项目的做法：把发生条件结构化成 disease_records.conditions：
    {"temp_min":20,"temp_max":30,"humidity_min":85,"rain_hours_min":4,
     "duration_days":2,"wind_max":6}
然后逐项打分，得到 0~1 的风险系数，再映射成「风险升高 / 建议巡田」这类话术 ——
绝不说「必须打药」。
"""
from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.farm import Field
from app.models.misc import WeatherRecord

logger = logging.getLogger(__name__)

# 真实源失败后的进程内退避：60 秒内不再发起请求。
# 没有它的话，接口每被打一次就去撞一次注定失败的 API，等于自己把自己限流。
_FETCH_BACKOFF_SECONDS = 60.0
_last_fetch_failure = 0.0
# 防并发重复拉取：两个请求同时发现「今天没有真实数据」时串行进入，
# 第二个拿到锁时会看到第一个刚写入的结果，直接跳过，省一次配额。
_FETCH_LOCK = threading.Lock()

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

RISK_TEXT = {
    RISK_LOW: "当前天气条件下，这个问题发生的可能性不高",
    RISK_MEDIUM: "当前天气条件有点适合这个问题发生，建议多留意",
    RISK_HIGH: "当前天气条件比较适合这个问题发生，建议尽快下地看看",
}


@dataclass
class WeatherSnapshot:
    record_date: date
    temp_max: float | None = None
    temp_min: float | None = None
    humidity: float | None = None
    precipitation_mm: float | None = None
    rain_hours: float | None = None
    wind_speed: float | None = None
    weather_text: str | None = None
    source: str = "unknown"
    is_mock: bool = False
    raw: dict | None = None  # 真实源的原始响应，落库备查（文档难点 12：可追溯）

    @property
    def temp_avg(self) -> float | None:
        if self.temp_max is None or self.temp_min is None:
            return None
        return (self.temp_max + self.temp_min) / 2


@dataclass
class RiskAssessment:
    disease_name: str
    risk_level: str
    score: float
    reasons: list[str] = field(default_factory=list)
    advice: str = ""

    def to_dict(self) -> dict:
        return {
            "disease_name": self.disease_name,
            "risk_level": self.risk_level,
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "advice": self.advice,
        }


def _to_float(v) -> float | None:
    """和风返回的数字几乎全是字符串（"27"）；None / 空串 / 解析失败都按缺测处理。"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _note_fetch_failure() -> None:
    global _last_fetch_failure
    _last_fetch_failure = time.monotonic()


class WeatherService:
    """天气获取。provider=mock 时用确定性的模拟数据，
    接口失败也只关闭天气增强，不影响基础诊断（文档第七章）。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_recent(self, field_id: int | None, lat: float | None = None,
                   lng: float | None = None, days: int = 3
                   ) -> list[WeatherSnapshot]:
        since = date.today() - timedelta(days=days - 1)
        stmt = select(WeatherRecord).where(WeatherRecord.record_date >= since)

        # ★★ D28（已修）：必须把「哪个地块」当成**约束**，不能当成可选过滤。
        #   旧写法是 `if field_id: stmt = stmt.where(...)` ——
        #   field_id 为空时**完全不过滤**，于是把**所有地块**同一天的记录
        #   一起捞回来，summarize() 再一 sum，就成了
        #   「3 天累计降雨 111.2 小时」（物理上不可能，3 天最多 72 小时）。
        #
        #   为什么这个数字不能忍：12/30 条病害的发生条件里带 rain_hours_min
        #   （阈值 2~6 小时），被污染后这一项**恒为满足**，风险分被系统性抬高，
        #   理由栏还会堂堂正正写「累计降雨 111.2 小时（需 ≥4 小时）」。
        #   农户看到这种数字，整个风险提示的可信度就没了 ——
        #   而 /weather/risk 在 smoke 里恰恰是不带 field_id 调的，必踩。
        #
        #   语义：field_id=None 表示「不针对具体地块」，对应 _upsert_snaps 写下的
        #   field_id IS NULL 那批记录。两者必须严格对齐，否则又会互相污染。
        stmt = stmt.where(WeatherRecord.field_id.is_(None) if field_id is None
                          else WeatherRecord.field_id == field_id)
        rows = list(self.db.scalars(stmt.order_by(WeatherRecord.record_date)).all())

        # ---- 真实天气源（D27 兑现）：provider=qweather 且配了 Key 才走 ----
        real_mode = self._real_mode()
        if real_mode:
            lat, lng = self._resolve_coords(field_id, lat, lng)
            self._refresh_today(rows, field_id, lat, lng)
            # _refresh_today 可能新写入/更新了「今天」这行，重查一次再组装
            rows = list(self.db.scalars(stmt.order_by(WeatherRecord.record_date)).all())

        # 真实模式下，旧的 mock 行不再参与窗口 —— 否则会变成「真实的今天 +
        # 演示用的前两天」混在一个窗口里，rain_hours 累加又会被污染（D28 的教训：
        # 假数据混进真数据比没有数据更糟，而且表面上看不出来）。
        snaps = [self._to_snapshot(r) for r in rows
                 if not (real_mode and r.is_mock)]

        # ★★ D29（已修）：窗口内缺的日期要补，不能「窗口里有任意一行就整段返回」。
        #   旧写法 `if rows: return rows` 的后果是**永久过期**：
        #   第 1 天写入 09-23~09-25 三天；第 2 天窗口变成 09-24~09-26，
        #   09-24/25 还在 → rows 非空 → 永远不生成 09-26。
        #   再往后窗口滑动，返回的永远是最早那批，record_date 一直停在过去。
        #   （TTL 过期重拉由 _refresh_today 负责，见 weather_cache_ttl_seconds。）
        have = {s.record_date for s in snaps}
        want = [date.today() - timedelta(days=days - 1 - i) for i in range(days)]
        missing = [d for d in want if d not in have]
        if missing and (not real_mode or date.today() not in have):
            # mock 模式：照旧补齐。
            # 真实模式下走到这里 = 今天没拿到真实数据（取数失败 / 没有坐标）→
            # 明确降级到 mock：is_mock=True 会让 summarize 带上演示数据声明，
            # 前端可见（D25 的原则：降级可以，但必须说话）。
            extra = self._mock_snapshot_for(missing)
            self._upsert_snaps(extra, field_id, lat, lng)
            snaps = sorted(snaps + extra, key=lambda s: s.record_date)
        # else：真实模式下「过去几天」的缺口不补 —— 和风免费档没有历史天气接口，
        #   拿 mock 凑数等于用假数据算风险。窗口会短，但每一条都是真的，
        #   之后每天自动积累一天。
        return snaps

    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_snapshot(r: WeatherRecord) -> WeatherSnapshot:
        return WeatherSnapshot(
            record_date=r.record_date, temp_max=r.temp_max, temp_min=r.temp_min,
            humidity=r.humidity, precipitation_mm=r.precipitation_mm,
            rain_hours=r.rain_hours, wind_speed=r.wind_speed,
            weather_text=r.weather_text, source=r.source or "db",
            is_mock=bool(r.is_mock))

    def _mock_snapshot_for(self, dates: list[date]) -> list[WeatherSnapshot]:
        """按**指定日期**生成模拟数据（不是「最近 N 天」）。

        刻意让「高温高湿」这一常见发病条件成立，这样演示时风险提示不会
        永远是空的。改成收日期列表而不是 days 计数，是为了让「补缺失日期」
        这件事变得自然 —— 收计数的话调用方还得自己算偏移，容易差一天。
        """
        out: list[WeatherSnapshot] = []
        for d in dates:
            # 用日期做种子，保证同一天多次调用结果一致
            seed = d.toordinal() % 7
            out.append(WeatherSnapshot(
                record_date=d,
                temp_max=26 + seed, temp_min=18 + seed % 3,
                humidity=78 + seed * 2, precipitation_mm=round(2 + seed * 1.5, 1),
                rain_hours=round(1 + seed * 0.8, 1),
                wind_speed=round(1.5 + seed * 0.4, 1),
                weather_text="多云转阴，局部有阵雨",
                source="mock", is_mock=True))
        return out

    # ------------------------- 真实天气源（和风天气） ------------------------- #
    # ★ 接口形状以 2026-09-26 实测为准（教训见实施问题清单 D27/D30：
    #   配置项存在 ≠ 实现存在；凭文档记忆写接口形状必踩坑）：
    #   - v1  /weather/v1/current/{lat}/{lon}      → 可用。humidity 是 0~1 小数，
    #     wind.speed 直接是 m/s
    #   - v1  /weather/v1/daily/*                  → 本 Host 404（订阅不含），不能用
    #   - v7  /v7/weather/3d?location={lng},{lat}  → 可用，同 Host 同样认
    #     X-QW-Api-Key 头。humidity 是百分数，数字全是字符串，precip 是 mm
    #   - v7 的 location 参数是「经度,纬度」，与 v1 路径 {lat}/{lon} 顺序相反
    #   - 响应可能是 gzip 且不带 Content-Encoding 头，要自己认魔数解压

    @staticmethod
    def _real_mode() -> bool:
        return (settings.weather_provider.strip().lower() == "qweather"
                and bool(settings.weather_api_key.strip()))

    def _resolve_coords(self, field_id: int | None, lat: float | None,
                        lng: float | None) -> tuple[float | None, float | None]:
        """fsm 里的调用只传 field_id 不带坐标 —— 坐标自己查。
        调用方（路由 / load_farm_context）已做过地块归属校验，这里不再重复。"""
        if lat is not None and lng is not None:
            return lat, lng
        if field_id is None:
            return lat, lng
        row = self.db.execute(
            select(Field.lat, Field.lng).where(Field.id == field_id)).first()
        if row and row[0] is not None and row[1] is not None:
            return row[0], row[1]
        return lat, lng

    def _refresh_today(self, rows: list[WeatherRecord], field_id: int | None,
                       lat: float | None, lng: float | None) -> None:
        """真实模式：保证「今天」这行是真实且未过期的。

        三个触发条件：还没有今天的行 / 今天的行是 mock 兜底 / 是真实数据但
        已超过 weather_cache_ttl_seconds —— 这个配置项从项目创建起就一直
        空转，在这里接上（实施问题清单待办 6）。
        """
        if lat is None or lng is None:
            return  # 没有坐标拉不了，由 get_recent 的 missing 分支降级 mock
        if time.monotonic() - _last_fetch_failure < _FETCH_BACKOFF_SECONDS:
            return  # 刚失败过，别每次请求都去撞一次注定失败的 API
        today = date.today()
        today_row = next((r for r in rows if r.record_date == today), None)
        if today_row is not None and not today_row.is_mock \
                and not self._is_stale(today_row):
            return
        with _FETCH_LOCK:
            # 拿到锁后重查：可能另一个请求刚好已经把今天写进去了
            row = self.db.scalar(select(WeatherRecord).where(
                WeatherRecord.record_date == today,
                WeatherRecord.field_id.is_(None) if field_id is None
                else WeatherRecord.field_id == field_id))
            if row is not None and not row.is_mock and not self._is_stale(row):
                return
            snap = self._qweather_fetch(lat, lng)
            if snap is None:
                _note_fetch_failure()
                return  # 降级由 get_recent 的 missing 分支负责（对用户可见）
            self._upsert_snaps([snap], field_id, lat, lng, commit=True)

    @staticmethod
    def _is_stale(row: WeatherRecord) -> bool:
        updated = row.updated_at
        if updated is None:
            return True
        if updated.tzinfo is None:  # 驱动万一给的是 naive，按 UTC 处理
            updated = updated.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - updated).total_seconds() \
            > settings.weather_cache_ttl_seconds

    def _qweather_fetch(self, lat: float, lng: float) -> WeatherSnapshot | None:
        base = settings.weather_api_base.strip()
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        base = base.rstrip("/")
        if base.endswith("/v7"):  # 兼容旧式配置（默认值自带版本路径）
            base = base[:-3]
        try:
            with httpx.Client(timeout=settings.weather_timeout_seconds,
                              headers={"X-QW-Api-Key": settings.weather_api_key}) as c:
                daily = self._get_json(
                    c, f"{base}/v7/weather/3d",
                    params={"location": f"{lng:.4f},{lat:.4f}"})   # ★ 经度在前
                cur = self._get_json(
                    c, f"{base}/weather/v1/current/{lat:.4f}/{lng:.4f}")
        except Exception:
            logger.warning("和风天气获取失败，本次请求降级为演示数据", exc_info=True)
            return None

        d0 = (daily.get("daily") or [{}])[0]
        cond = cur.get("condition") or {}
        wind_speed = ((cur.get("wind") or {}).get("speed") or {}).get("value")
        temp_now = _to_float((cur.get("temperature") or {}).get("value"))

        temp_max = _to_float(d0.get("tempMax")) or temp_now
        temp_min = _to_float(d0.get("tempMin")) or temp_now
        humidity = _to_float(d0.get("humidity"))
        if humidity is None:  # daily 拿不到时用实时的，v1 是 0~1 小数要换算
            frac = _to_float(cur.get("humidity"))
            humidity = round(frac * 100, 1) if frac is not None else None
        precip = _to_float(d0.get("precip")) or 0.0

        # ★ rain_hours 是估算值，必须在代码里说清楚：和风不提供降雨小时数
        #   （历史逐时数据是商业接口）。按「≈2.5mm/h（小雨偏中雨的强度）」折算，
        #   宁可把风险略报高也不静默置零 —— 置零会让 12/30 条病害的
        #   rain_hours_min 条件在真实模式下永远不满足，风险提示系统性失明。
        #   原始响应已存 raw 列，事后可复核。
        rain_hours = 0.0 if precip < 0.5 else min(24.0, round(precip / 2.5, 1))

        return WeatherSnapshot(
            record_date=date.today(), temp_max=temp_max, temp_min=temp_min,
            humidity=humidity, precipitation_mm=precip, rain_hours=rain_hours,
            wind_speed=_to_float(wind_speed),
            weather_text=str(d0.get("textDay") or cond.get("text") or "") or None,
            source="qweather", is_mock=False,
            raw={"qweather_daily": d0, "qweather_current": cur})

    @staticmethod
    def _get_json(client: httpx.Client, url: str,
                  params: dict | None = None) -> dict:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.content
        if data[:2] == b"\x1f\x8b":  # 和风有时不带 Content-Encoding 头就回 gzip
            data = gzip.decompress(data)
        payload = json.loads(data)
        code = payload.get("code")  # v7 的部分错误是 HTTP 200 + code 字段
        if code is not None and str(code) != "200":
            raise RuntimeError(f"qweather code={code}")
        return payload

    def _upsert_snaps(self, snaps: list[WeatherSnapshot], field_id: int | None,
                      lat: float | None, lng: float | None,
                      *, commit: bool = False) -> None:
        """按（field_id 语义, record_date）有则更新、无则插入。

        field_id 的空值语义必须与 get_recent 的过滤严格对齐（D28）：
        None ↔ IS NULL，否则读写两批记录互相看不见。
        commit=True 只给真实数据用：真实数据是一次外部调用换来的，而
        get_db 收尾并不会 commit —— flush 完等回滚，配额就白花了。
        mock 数据是确定性可再生的，保持 flush 即可。
        """
        if not snaps:
            return
        dates = [s.record_date for s in snaps]
        stmt = select(WeatherRecord).where(WeatherRecord.record_date.in_(dates))
        stmt = stmt.where(WeatherRecord.field_id.is_(None) if field_id is None
                          else WeatherRecord.field_id == field_id)
        by_date = {r.record_date: r for r in self.db.scalars(stmt).all()}
        for s in snaps:
            row = by_date.get(s.record_date)
            if row is None:
                row = WeatherRecord(field_id=field_id, record_date=s.record_date,
                                    lat=lat, lng=lng)
                self.db.add(row)
            row.temp_max = s.temp_max
            row.temp_min = s.temp_min
            row.temp_avg = s.temp_avg
            row.humidity = s.humidity
            row.precipitation_mm = s.precipitation_mm
            row.rain_hours = s.rain_hours
            row.wind_speed = s.wind_speed
            row.weather_text = s.weather_text
            row.source = s.source
            row.is_mock = s.is_mock
            if s.raw is not None:
                row.raw = s.raw
        self.db.flush()
        if commit:
            self.db.commit()


# ---------------------------------------------------------------------- #
def assess_risk(conditions: dict | None, snaps: list[WeatherSnapshot],
                growth_stage: str | None = None,
                stage_susceptibility: dict | None = None) -> RiskAssessment | None:
    """按知识库里结构化的发生条件打分。

    每满足一项条件得一分，最后归一化。条件是「必要条件集合」，
    不满足不等于不会发病，只代表风险低 —— 所以结论只输出风险等级。
    """
    if not conditions or not snaps:
        return None

    name = str(conditions.get("_disease_name") or "")
    checks: list[tuple[bool, str]] = []
    avg_temp = sum(s.temp_avg or 0 for s in snaps) / max(len(snaps), 1)
    max_humidity = max((s.humidity or 0) for s in snaps)
    total_rain_hours = sum((s.rain_hours or 0) for s in snaps)
    max_wind = max((s.wind_speed or 0) for s in snaps)

    tmin, tmax = conditions.get("temp_min"), conditions.get("temp_max")
    if tmin is not None or tmax is not None:
        lo, hi = tmin if tmin is not None else -99, tmax if tmax is not None else 99
        ok = lo <= avg_temp <= hi
        checks.append((ok, f"平均气温 {avg_temp:.1f}℃（适宜区间 {lo}~{hi}℃）"))

    hmin = conditions.get("humidity_min")
    if hmin is not None:
        ok = max_humidity >= hmin
        checks.append((ok, f"最高湿度 {max_humidity:.0f}%（发病需 ≥{hmin}%）"))

    rmin = conditions.get("rain_hours_min")
    if rmin is not None:
        ok = total_rain_hours >= rmin
        checks.append((ok, f"累计降雨 {total_rain_hours:.1f} 小时（需 ≥{rmin} 小时）"))

    wmax = conditions.get("wind_max")
    if wmax is not None:
        ok = max_wind <= wmax
        checks.append((ok, f"最大风速 {max_wind:.1f} m/s（需 ≤{wmax}）"))

    if not checks:
        return None

    score = sum(1 for ok, _ in checks if ok) / len(checks)

    # 生长阶段易感度加权（文档难点 7 提到要结合生长阶段）
    if growth_stage and stage_susceptibility:
        level = str(stage_susceptibility.get(growth_stage, "")).strip()
        if level in ("高", "high", "高感"):
            score = min(1.0, score * 1.15)
            checks.append((True, f"当前处于{growth_stage}，属于易感阶段"))
        elif level in ("低", "low"):
            score *= 0.85

    if score >= 0.75:
        level = RISK_HIGH
    elif score >= 0.45:
        level = RISK_MEDIUM
    else:
        level = RISK_LOW

    reasons = [f"{'满足' if ok else '不满足'}：{desc}" for ok, desc in checks]
    return RiskAssessment(disease_name=name, risk_level=level, score=score,
                          reasons=reasons, advice=RISK_TEXT[level])


# ---------------------------------------------------------------------- #
# 下面两个是「给接口用的」聚合函数。
#
# ★ 为什么放在 service 而不是写在路由里：farm.py 的 /farm/overview 与
#   weather.py 的 /weather/current 都要「逐日快照 → 一条汇总」，
#   写在路由里就会出现两个接口各维护一份、改一处漏一处的局面。
MOCK_CAVEAT = "当前天气为演示用模拟数据，接入真实气象接口后自动替换"


def summarize(snaps: list[WeatherSnapshot]) -> dict | None:
    """把逐日快照压成一条汇总。字段与 schemas.WeatherOut 对齐。"""
    if not snaps:
        return None
    temps = [s.temp_avg for s in snaps if s.temp_avg is not None]
    hums = [s.humidity for s in snaps if s.humidity is not None]
    winds = [s.wind_speed for s in snaps if s.wind_speed is not None]
    last = snaps[-1]
    is_mock = any(s.is_mock for s in snaps)
    return {
        "record_date": last.record_date,
        "temp_avg": round(sum(temps) / len(temps), 1) if temps else None,
        "temp_max": max((s.temp_max for s in snaps if s.temp_max is not None),
                        default=None),
        "temp_min": min((s.temp_min for s in snaps if s.temp_min is not None),
                        default=None),
        "humidity_avg": round(sum(hums) / len(hums), 1) if hums else None,
        "humidity_max": max(hums) if hums else None,
        "rainfall_hours": round(sum(s.rain_hours or 0 for s in snaps), 1),
        "wind_level": round(max(winds), 1) if winds else None,
        "provider": last.source,
        "is_mock": is_mock,
        # ★ 模拟数据必须自带声明。显示「高温高湿、风险升高」却不说明数据是
        #   假的，等于用假数据吓唬农户。接真实接口后这行自动消失。
        "caveat": MOCK_CAVEAT if is_mock else None,
    }


def risks_for_crop(db: Session, crop_name: str, stage: str | None,
                   snaps: list[WeatherSnapshot],
                   limit: int = 5) -> list[RiskAssessment]:
    """给当前作物算各病害的天气风险分。

    ★ 为什么是打分而不是让模型读天气描述自己判断：
      模型判断不可复现 —— 同一份天气数据两次调用可能给出不同风险等级，
      也没法向农户解释「凭什么是高风险」。打分是纯函数，同样的输入永远
      同样的输出，能写进测试，也能把「平均气温 24.3℃ 落在 20~30℃ 适宜
      区间」这种理由直接摆给农户看（文档难点 13：结果可验证）。
    """
    if not snaps:
        return []
    # 延迟导入，避免 service 之间在模块加载期互相牵扯
    from app.models.knowledge import DiseaseRecord

    rows = db.scalars(
        select(DiseaseRecord)
        .where(DiseaseRecord.deleted_at.is_(None),
               DiseaseRecord.crop_names.contains([crop_name]))
        .limit(40)).all()

    out: list[RiskAssessment] = []
    for d in rows:
        if not d.conditions:
            continue
        cond = dict(d.conditions)
        # assess_risk 用 conditions["_disease_name"] 给结果命名；
        # 库里不存这个键，它是运行期注入的，避免与真实字段混淆。
        cond["_disease_name"] = d.standard_name
        r = assess_risk(cond, snaps, growth_stage=stage,
                        stage_susceptibility=d.stage_susceptibility)
        if r is not None:
            out.append(r)
    out.sort(key=lambda x: x.score, reverse=True)
    return out[:limit]
