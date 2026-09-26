"""意图识别专项检查（走真实 LLM）。

★ 为什么单开一个脚本，不塞进 smoke_http.py：
  smoke 是「快、每次改动都跑」的回归套件，而这里每跑一次要发 9 次模型请求
  （十几秒 + 消耗 token），塞进去会让 smoke 变慢，最后没人愿意跑。
  这里定位是「改了 INTENT_SYSTEM 或换了模型之后，手动跑一遍」的专项检查。

★ 为什么必须单独测这一条链路：
  意图识别是**整条链路的分岔口** —— 判成 diagnosis 会走「要求补拍照片」，
  判成 consult 会走「直接解释原因」。判错的代价不是分类不准，
  而是答非所问：用户问「叶子为什么发黄」，系统回「请再拍一张照片」。

★ 这里比 smoke 多测的是「**不要矫枉过正**」：
  修「为什么被误判成诊断」时，很容易把提示词写成「看到『为什么』就 consult」，
  结果「辣椒叶子卷起来了，是不是病毒病」也被判成 consult ——
  那又错了。所以正反两个方向都要有样例。

用法（后端需已在本机运行）：
    PYTHONUTF8=1 ../.venv/Scripts/python.exe scripts/check_intent_llm.py
    PYTHONUTF8=1 ../.venv/Scripts/python.exe scripts/check_intent_llm.py --base http://127.0.0.1:8001/api/v1
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8001/api/v1"

# (用户原话, 期望意图, 这条在测什么)
CASES: list[tuple[str, str, str]] = [
    # —— 反方向：「求解释」不能判成诊断（曾经的真实缺陷）——
    ("黄瓜叶子为什么发黄？", "consult", "★ 问原因，不该被要求补拍照片"),
    ("辣椒叶子为什么卷起来了", "consult", "★ 同上，换个作物/症状"),
    ("番茄叶子发黄是什么原因", "consult", "★ 「什么原因」也属求解释"),
    ("叶子发黄怎么回事", "consult", "★ 「怎么回事」同上"),

    # —— 正方向：别矫枉过正，真要诊断的还得判成诊断 ——
    ("黄瓜叶子发黄了，帮我看看是什么病", "diagnosis", "★ 明确要求判断病情"),
    ("辣椒叶子卷起来了，是不是病毒病", "diagnosis", "★ 句中有「是不是」但在求判断"),
    ("番茄叶子背面有一层白毛", "diagnosis", "纯症状描述"),

    # —— 其余分类的回归，防止改提示词时把它们带偏 ——
    ("番茄什么时候浇水比较合适", "consult", "普通农事咨询"),
    ("这个病用什么药，兑多少水", "pesticide", "用药类必须走规则引擎"),
    ("明天天气怎么样，会不会发病", "farm_analysis", "天气风险"),
    ("帮我设个提醒，三天后去看看", "task", "待办"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--user", default="demo")
    ap.add_argument("--password", default="demo1234")
    args = ap.parse_args()

    tok = _login(args.base, args.user, args.password)
    if not tok:
        print("登录失败，检查后端是否在运行 / 账号密码是否正确")
        return 1

    passed = 0
    fails: list[str] = []
    for query, want, why in CASES:
        got = _intent(args.base, tok, query)
        ok = got == want
        passed += ok
        print(f"  [{'OK  ' if ok else 'FAIL'}] {query:26s} "
              f"期望={want:14s} 实得={got:14s} {why}")
        if not ok:
            fails.append(f"{query} → {got}（期望 {want}）")

    print()
    print("=" * 62)
    print(f"意图识别 {passed}/{len(CASES)} 正确")
    if fails:
        print("失败明细：")
        for f in fails:
            print("  ·", f)
        print()
        print("提示：若只有「为什么」类失败 → INTENT_SYSTEM 缺少「求解释≠求诊断」规则；")
        print("      若「是不是什么病」也失败 → 提示词矫枉过正了，正反样例都要保住。")
    return 0 if not fails else 1


def _login(base: str, user: str, password: str) -> str:
    data = json.dumps({"username": user, "password": password}).encode()
    req = urllib.request.Request(
        base + "/auth/login", data=data,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("access_token", "")
    except Exception as exc:  # noqa: BLE001
        print(f"登录出错: {type(exc).__name__}: {exc}")
        return ""


def _intent(base: str, tok: str, query: str) -> str:
    data = urllib.parse.urlencode({"query": query}).encode()
    req = urllib.request.Request(base + "/diagnosis", data=data,
                                 headers={"Authorization": "Bearer " + tok})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return str(json.load(r).get("intent"))
    except Exception as exc:  # noqa: BLE001
        return f"ERR:{type(exc).__name__}"


if __name__ == "__main__":
    sys.exit(main())
