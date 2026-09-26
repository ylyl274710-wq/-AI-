"""抓取后端真实响应，存成前端运行时检查用的 fixture。

★ 为什么不在测试里手写假响应：
    手写的响应形状一旦和后端不一致，前端 render 就会抛错 ——
    但那是「我的 fixture 写错了」，不是「前端有 bug」。
    这类假阳性比没有测试更糟，因为它会把人引去改好代码。
    （SOUL.md 第 1 条：区分产品缺陷和测试缺陷。）

用法：
    python frontend/tools/capture-fixtures.py [--base http://127.0.0.1:8001/api/v1]
                                            [--user demo] [--pass demo1234]
后端没起就跳过，已存在的 fixture 保持不动。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "fixtures"


def http(method: str, url: str, token: str | None = None,
         body: dict | None = None, timeout: int = 20):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except (urllib.error.URLError, TimeoutError) as e:
        return 0, str(e)


def save(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / (name + ".json")
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    n = len(json.dumps(payload, ensure_ascii=False))
    print(f"  [ok] {name:<34} {n:>6} B")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001/api/v1")
    ap.add_argument("--user", default="demo")
    ap.add_argument("--pass", dest="password", default="demo1234")
    a = ap.parse_args()
    base = a.base.rstrip("/")

    st, health = http("GET", base + "/health")
    if st != 200:
        print(f"后端不可达（{base}）：{health}")
        return 1
    save("health", health)

    st, tok = http("POST", base + "/auth/login",
                   body={"username": a.user, "password": a.password})
    if st != 200 or not isinstance(tok, dict) or "access_token" not in tok:
        print(f"登录失败（{st}）：{tok}")
        return 1
    token = tok["access_token"]
    save("auth_login", tok)

    st, me = http("GET", base + "/auth/me", token)
    save("auth_me", me)

    # 前端每个 GET 调用点都要有一条 fixture，否则运行时检查里
    # fetch 桩会报「未 mock 的路径」——这是故意的，见 check-frontend-runtime.cjs
    simple = [
        ("farm_overview", "/farm/overview"),
        ("farm_farms", "/farm/farms"),
        ("farm_crops", "/farm/crops"),
        ("farm_growth_records", "/farm/growth-records"),
        ("chat_sessions", "/chat/sessions"),
        ("knowledge_stats", "/knowledge/stats"),
        ("knowledge_diseases", "/knowledge/diseases?limit=100"),
        ("pesticide_usage", "/pesticide/usage"),
    ]
    got: dict[str, object] = {}
    for name, path in simple:
        st, body = http("GET", base + path, token)
        if st != 200:
            print(f"  [!!] {name:<34} HTTP {st} {body}")
            continue
        got[name] = body
        save(name, body)

    # 详情类接口：从前面的列表里挑第一条真数据去取
    # ★ 会话列表的键是 session_id，不是 id —— 我第一版写成 id，
    #   取到 None，请求 /chat/sessions/None 拿了个 404 还存成 fixture。
    #   教训：fixture 里的 id 字段必须从响应里读，不能凭直觉猜。
    sess = got.get("chat_sessions") or []
    if isinstance(sess, list) and sess:
        sid = sess[0].get("session_id") or sess[0].get("id")
        if sid is None:
            print("  [!!] chat_session_detail               会话列表里找不到 id 字段")
        else:
            st, body = http("GET", f"{base}/chat/sessions/{sid}", token)
            if st == 200:
                save("chat_session_detail", body)
            else:
                print(f"  [!!] chat_session_detail               HTTP {st}")

    dis = got.get("knowledge_diseases")
    if isinstance(dis, dict):
        dis = dis.get("items") or dis.get("data") or []
    if isinstance(dis, list) and dis:
        did = dis[0].get("id")
        st, body = http("GET", f"{base}/knowledge/diseases/{did}", token)
        if st == 200:
            save("knowledge_disease_detail", body)
        else:
            print(f"  [!!] knowledge_disease_detail          HTTP {st}")

    # ---- 覆盖率自检：前端出现的 GET 字面量路径，必须都有 fixture ----
    # 漏一条的后果是运行时检查里 fetch 桩报「未 mock」，那是好事（会暴露），
    # 但最好在这里就说清楚，免得跑到一半才发现。
    import re
    js_dir = HERE.parent / "js"
    called: set[str] = set()
    for f in list(js_dir.glob("*.js")) + list(js_dir.glob("pages/*.js")):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"api\.get\(\s*'([^']+)'", src):
            called.add(m.group(1).split("?")[0])
    known = {
        "/health", "/auth/me", "/farm/overview", "/farm/farms", "/farm/crops",
        "/farm/growth-records", "/chat/sessions", "/knowledge/stats",
        "/knowledge/diseases", "/pesticide/usage",
    }
    dynamic = {p for p in called
               if "{" in p or "+" in p or "$" in p or p.endswith("/")}
    missing = sorted(p for p in called - known - dynamic)
    if missing:
        print("\n  [警告] 前端调了但没抓 fixture 的 GET 路径：")
        for p in missing:
            print("         " + p)
        print("         → 补进上面的 simple 列表，否则运行时检查会报「未 mock」")

    print(f"\nfixture 目录：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
