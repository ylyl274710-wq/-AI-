"""一次性诊断：为什么「我的地」页面停在「加载中…」。

★ 为什么单独写一个而不是改 check-browser-e2e.py：
  这个脚本是**排查用的**，要打印网络请求、控制台、异常堆栈这些
  平时不该进回归套件的东西。查清之后再决定是修产品还是修测试。
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8001/app/"


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1100, "height": 800})

        reqs: list[str] = []
        fails: list[str] = []
        console: list[str] = []
        errors: list[str] = []
        page.on("request", lambda r: reqs.append(f"{r.method} {r.url}"))
        page.on("requestfailed",
                lambda r: fails.append(f"{r.url} :: {r.failure}"))
        page.on("console", lambda m: console.append(f"[{m.type}] {m.text}"))
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(BASE, wait_until="networkidle")
        page.fill("#gateUser", "demo")
        page.fill("#gatePass", "demo1234")
        page.click("#gateSubmit")
        page.wait_for_selector("#app", state="visible", timeout=15000)
        page.wait_for_timeout(1000)

        reqs.clear()
        print("=" * 70)
        print("切到 farm 页，观察 5 秒")
        print("=" * 70)
        page.locator('#nav [data-route="farm"]').first.click()
        for i in range(5):
            page.wait_for_timeout(1000)
            txt = page.locator("#view").inner_text().strip()
            print(f"  +{i + 1}s  view 文字长度={len(txt):4d}  "
                  f"开头={txt[:60]!r}")

        print()
        print("=" * 70)
        print("网络请求")
        print("=" * 70)
        for r in reqs:
            print("  ", r)

        print()
        print("=" * 70)
        print("请求失败")
        print("=" * 70)
        for f in fails:
            print("  ", f)
        if not fails:
            print("  （无）")

        print()
        print("=" * 70)
        print("控制台")
        print("=" * 70)
        for c in console:
            print("  ", c)
        if not console:
            print("  （无）")

        print()
        print("=" * 70)
        print("未捕获异常")
        print("=" * 70)
        for e in errors:
            print("  ", e)
        if not errors:
            print("  （无）")

        # 直接手动调一遍，看是哪一步不返回
        print()
        print("=" * 70)
        print("手动逐个调这三个接口（在页面上下文里）")
        print("=" * 70)
        for path in ("/farm/farms", "/farm/crops", "/farm/growth-records"):
            out = page.evaluate(
                """async (p) => {
                    const t0 = performance.now();
                    try {
                        const r = await fetch(Agri.cfg.apiBase + p, {
                            headers: { Authorization: 'Bearer ' + Agri.store.token }
                        });
                        const j = await r.json();
                        return { ms: Math.round(performance.now() - t0),
                                 status: r.status,
                                 n: Array.isArray(j) ? j.length : typeof j };
                    } catch (e) {
                        return { err: String(e) };
                    }
                }""", path)
            print(f"  {path:26s} -> {out}")

        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
