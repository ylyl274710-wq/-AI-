"""天气风险打分与汇总（assess_risk / summarize）的单元测试。

关键回归点：
  - 纯函数性质：同样的天气 + 条件，风险等级必须可复现（文档难点 13）；
  - D28 的物理不变量：窗口降雨小时数 ≤ 天数 × 24，跨地块数据混入必爆；
  - 降级可见：mock 数据必须自带 caveat（D25 原则）。
"""
from datetime import date

import pytest

from app.services.weather import (RISK_HIGH, RISK_LOW, RISK_MEDIUM,
                                  MOCK_CAVEAT, WeatherSnapshot, assess_risk,
                                  summarize)


def snap(day, *, temp_max=28, temp_min=20, humidity=85, rain_hours=3.0,
         wind=2.0, is_mock=False, source="qweather"):
    return WeatherSnapshot(
        record_date=date(2026, 9, day), temp_max=temp_max, temp_min=temp_min,
        humidity=humidity, precipitation_mm=rain_hours * 2.5,
        rain_hours=rain_hours, wind_speed=wind, weather_text="多云",
        source=source, is_mock=is_mock)


COND = {"_disease_name": "测试病害", "temp_min": 20, "temp_max": 30,
        "humidity_min": 80, "rain_hours_min": 4}


def test_all_conditions_met_is_high_and_reproducible():
    snaps = [snap(25, rain_hours=5.0), snap(26, rain_hours=5.0)]
    r1 = assess_risk(COND, snaps)
    r2 = assess_risk(COND, snaps)
    assert r1 is not None and r2 is not None
    assert r1.score == r2.score == 1.0          # 纯函数：两次调用同一结果
    assert r1.risk_level == RISK_HIGH
    assert r1.disease_name == "测试病害"
    assert len(r1.reasons) == 3


def test_partial_match_is_medium():
    # 温度达标、湿度达标、降雨不足 → 2/3 ≈ 0.67 → medium
    snaps = [snap(25, humidity=85, rain_hours=0.0)]
    r = assess_risk(COND, snaps)
    assert r is not None
    assert r.risk_level == RISK_MEDIUM
    assert r.score == pytest.approx(2 / 3)


def test_mostly_failed_is_low():
    snaps = [snap(25, temp_max=35, temp_min=33, humidity=40, rain_hours=0.0)]
    r = assess_risk(COND, snaps)
    assert r is not None
    assert r.risk_level == RISK_LOW
    assert any("不满足" in reason for reason in r.reasons)


def test_temperature_out_of_range_fails_with_reason():
    snaps = [snap(25, temp_max=38, temp_min=35)]
    r = assess_risk(COND, snaps)
    assert r is not None
    assert any("不满足" in x and "平均气温" in x for x in r.reasons)


def test_empty_conditions_or_snaps_return_none():
    assert assess_risk(None, [snap(25)]) is None
    assert assess_risk(COND, []) is None
    assert assess_risk({"_disease_name": "x"}, [snap(25)]) is None  # 无可计算项


def test_susceptible_stage_boosts_and_leaves_a_reason():
    snaps = [snap(25, rain_hours=5.0)]
    base = assess_risk(COND, snaps, growth_stage=None)
    boosted = assess_risk(COND, snaps, growth_stage="结果期",
                          stage_susceptibility={"结果期": "高"})
    assert boosted is not None and base is not None
    assert boosted.score >= base.score
    assert any("易感阶段" in x for x in boosted.reasons)


def test_resistant_stage_dampens():
    snaps = [snap(25, humidity=85, rain_hours=0.0)]  # 2/3
    r = assess_risk(COND, snaps, growth_stage="苗期",
                    stage_susceptibility={"苗期": "低"})
    assert r is not None
    assert r.score < 2 / 3


def test_summarize_rainfall_invariant_d28():
    # 物理不变量：3 天累计降雨 ≤ 72 小时。跨地块数据混入必爆（D28 的教训）
    snaps = [snap(25, rain_hours=6.0), snap(26, rain_hours=6.6),
             snap(27, rain_hours=0.0)]
    s = summarize(snaps)
    assert s is not None
    assert s["rainfall_hours"] <= 3 * 24
    assert s["rainfall_hours"] == pytest.approx(12.6)


def test_summarize_mock_caveat_visible():
    s = summarize([snap(25, is_mock=True, source="mock")])
    assert s is not None
    assert s["is_mock"] is True
    assert s["caveat"] == MOCK_CAVEAT


def test_summarize_real_data_has_no_caveat():
    s = summarize([snap(25, rain_hours=2.0)])
    assert s is not None
    assert s["is_mock"] is False
    assert s["caveat"] is None
    assert s["provider"] == "qweather"


def test_summarize_empty_returns_none():
    assert summarize([]) is None


def test_snapshot_temp_avg():
    assert snap(25, temp_max=30, temp_min=20).temp_avg == 25
    assert WeatherSnapshot(record_date=date(2026, 9, 25)).temp_avg is None
