"""一次性：把和风新版的「逐日预报」响应摊平，确认每个字段的单位和取值域。

★ 为什么单独写：写 provider 之前必须知道**单位**。
  新版接口的 humidity 是 0~1 的小数（不是 68 这样的百分数），
  wind.speed 已经带 `unit: "m/s"`，precipitation 是对象不是标量 ——
  这些如果靠猜，写出来的 provider 会「有数据但数值全错」，
  而且不报错。测试数据必须是真实响应，不能手写。
"""
from __future__ import annotations

import gzip
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402

LAT, LNG = 34.721, 113.981


def get(path: str) -> dict:
    url = f"https://{settings.weather_api_base}{path}"
    req = urllib.request.Request(url, headers={
        "X-QW-Api-Key": settings.weather_api_key,
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def main() -> int:
    d = get(f"/weather/v1/daily/{LAT}/{LNG}?days=3")
    print("=" * 72)
    print("逐日预报 —— 顶层 keys:", sorted(d.keys()))
    print("=" * 72)
    days = d.get("days") or []
    print(f"天数: {len(days)}\n")

    for i, day in enumerate(days):
        print(f"--- day[{i}] forecastStartTime = {day.get('forecastStartTime')!r}")
        for k in ("temperatureMax", "temperatureMin", "temperatureAvg",
                  "humidity", "precipitation", "wind", "condition",
                  "uvIndexMax"):
            if k in day:
                print(f"    {k:18s} = {json.dumps(day[k], ensure_ascii=False)}")
        for part in ("daytime", "nighttime"):
            blk = day.get(part) or {}
            print(f"    {part}.humidity    = {blk.get('humidity')!r}")
            print(f"    {part}.wind        = {json.dumps(blk.get('wind'), ensure_ascii=False)}")
            print(f"    {part}.precip      = {json.dumps(blk.get('precipitation'), ensure_ascii=False)}")
        print(f"    单日全部 keys      = {sorted(day.keys())}")
        print()

    print("=" * 72)
    print("逐小时预报（用来推 rain_hours —— 项目需要「降雨小时数」）")
    print("=" * 72)
    try:
        h = get(f"/weather/v1/hourly/{LAT}/{LNG}?hours=24")
        hours = h.get("hours") or []
        print(f"小时数: {len(hours)}；顶层 keys: {sorted(h.keys())}")
        if hours:
            print("hour[0] keys:", sorted(hours[0].keys()))
            print("hour[0] =", json.dumps(hours[0], ensure_ascii=False)[:500])
            # 统计有多少小时 precipitation.amount.value > 0
            rainy = 0
            for x in hours:
                amt = (x.get("precipitation") or {}).get("amount") or {}
                if (amt.get("value") or 0) > 0:
                    rainy += 1
            print(f"\n★ 这 24 小时里 precipitation.amount.value > 0 的小时数 = {rainy}")
    except Exception as exc:  # noqa: BLE001
        print(f"逐小时接口失败: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
