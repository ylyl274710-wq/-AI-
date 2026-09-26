"""临时探针：验证 backend/.env 里的**天气**配置能否真的调通，以及该用哪个接口。

★ 为什么必须实测，不能只看配置项：
  `WEATHER_PROVIDER` / `WEATHER_API_KEY` / `WEATHER_API_BASE` 在代码里
  **零引用**（见 docs/实施问题清单.md D27）—— 也就是说不管填什么，
  跑起来都用 mock。所以「填好了」这个状态本身没法自证，
  必须真的发一次请求才知道 host 和 key 对不对。

★ 为什么要把候选接口都试一遍：
  和风天气现在有两套并行的 API，**认证方式和路径都不一样**：

    新版：专属 Host（形如 xxx.re.qweatherapi.com）+ 请求头 X-QW-Api-Key
          路径 /weather/v1/current/{lat}/{lon}
    旧版：固定域名 devapi.qweather.com + 查询参数 key=
          路径 /v7/weather/now?location=lng,lat

  用户控制台里给的是「专属 Host」，说明是新版；但新版的路径有多种变体，
  与其猜，不如把候选都打一遍看谁返回 200。**猜错的代价是写一个
  永远 404 的 provider，而且看起来像「key 不对」。**

★ 这个探针只读，不改任何业务数据，也不打印完整 Key。
"""
from __future__ import annotations

import gzip
import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402

# 郑州中牟（demo 用户 1 号棚的坐标）
LAT, LNG = 34.721, 113.981

CANDIDATES = [
    ("新版·实时天气（路径参数）", "GET", f"/weather/v1/current/{LAT}/{LNG}", None),
    ("新版·逐日预报（路径参数）", "GET", f"/weather/v1/daily/{LAT}/{LNG}?days=3", None),
    ("新版·逐日预报 forecast", "GET", f"/weather/v1/forecast/daily/{LAT}/{LNG}?days=3", None),
    ("新版·逐小时预报", "GET", f"/weather/v1/hourly/{LAT}/{LNG}?hours=24", None),
    ("新版·实时天气（查询参数）", "GET", f"/weather/v1/weather/now?location={LNG},{LAT}", None),
    ("新版·实时天气 now", "GET", f"/weather/v1/now?location={LNG},{LAT}", None),
    ("新版·城市查询", "GET", "/geo/v2/city/lookup?location=%E9%83%91%E5%B7%9E", None),
    ("旧版·实时天气", "GET", f"/v7/weather/now?location={LNG},{LAT}", None),
    ("旧版·3 天预报", "GET", f"/v7/weather/3d?location={LNG},{LAT}", None),
]

AUTH_STYLES = [
    ("X-QW-Api-Key 请求头", "header"),
    ("Authorization: Bearer", "bearer"),
    ("key= 查询参数", "query"),
]


def fetch(url: str, auth: str, key: str, timeout: int) -> tuple[int, str]:
    headers = {"Accept-Encoding": "gzip", "User-Agent": "agri-pest-agent/0.1"}
    if auth == "header":
        headers["X-QW-Api-Key"] = key
    elif auth == "bearer":
        headers["Authorization"] = "Bearer " + key
    if auth == "query":
        url += ("&" if "?" in url else "?") + "key=" + key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        if e.headers.get("Content-Encoding") == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:  # noqa: BLE001
                pass
        return e.code, raw.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def main() -> int:
    host = (settings.weather_api_base or "").strip()
    key = (settings.weather_api_key or "").strip()

    print("=" * 72)
    print("天气配置")
    print("=" * 72)
    print(f"  provider = {settings.weather_provider}")
    print(f"  api_base = {host or '(空)'}")
    print(f"  api_key  = {key[:6]}…{key[-4:]}（长度 {len(key)}）")
    if not host or not key:
        print("\n  ✗ host 或 key 为空，先把 .env 填好")
        return 2

    for scheme in ("https", "http"):
        try:
            ip = socket.gethostbyname(host)
            print(f"  DNS      = {ip}")
            break
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ DNS 解析失败（{scheme}）: {exc}")
            return 2

    print()
    print("=" * 72)
    print("逐个试接口 × 认证方式")
    print("=" * 72)

    winners: list[tuple[str, str, int, str]] = []
    seen_labels: set[str] = set()
    for label, method, path, _ in CANDIDATES:
        url = f"https://{host}{path}"
        for auth_label, auth in AUTH_STYLES:
            # 每个接口只要第一种能用的认证方式就够了，不必三连打
            if label in seen_labels:
                continue
            status, body = fetch(url, auth, key, settings.weather_timeout_seconds + 4)
            print(f"  {label:26s} {auth_label:22s} status={status}")
            if status == 200:
                winners.append((label, auth_label, status, body))
                seen_labels.add(label)
            elif status not in (401, 403, 404):
                print(f"       body: {body[:180]}")
        print()

    print("=" * 72)
    print("结论：能用的接口 + **完整响应**（字段名和单位是写 provider 的依据）")
    print("=" * 72)
    if not winners:
        print("  ✗ 没有任何组合返回 200。先确认 key 和 host 是不是同一个项目里的，")
        print("    以及控制台里这个 key 有没有开通「实时天气」这类接口权限。")
        return 1

    for label, auth_label, _, body in winners:
        print(f"  ✓ {label} / {auth_label}")
        try:
            data = json.loads(body)
            print(json.dumps(data, ensure_ascii=False, indent=2)[:2200])
        except Exception:  # noqa: BLE001
            print(f"    body = {body[:800]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
