#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性验证：诊断页连续追问共用同一会话 + 对话列表分组紧凑化。

验证点：
  1. 第一次提问后 start 事件带回 session_id 并存入 store
  2. 第二次提问带同一 session_id（后端归入同会话，消息数=4）
  3. 对话页列表按「今天/昨天/更早」分组渲染
  4. 「开新话题」按钮重置后，再次提问产生新会话
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "frontend" / "tools"))

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = "http://127.0.0.1:8001"  # API 根
APP = BASE + "/app/"
SHOT = ROOT / "frontend" / "tools" / "screenshots"


def main() -> int:
    ok = []

    def check(name, cond, extra=""):
        ok.append(bool(cond))
        print(f"  [{'OK  ' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(APP, wait_until="domcontentloaded")
        page.wait_for_selector("#gate", timeout=15000)
        page.fill("#gateUser", "demo")
        page.fill("#gatePass", "demo1234")
        page.click("#gateSubmit")
        page.wait_for_selector("#app", timeout=15000)

        # 第一次提问
        page.locator('#nav [data-route="diagnose"]').first.click()
        page.wait_for_selector("#query", timeout=15000)
        page.fill("#query", "番茄叶子背面有白粉怎么办")
        page.click("#submit")
        page.wait_for_selector("#result .card", timeout=90000)
        sid1 = page.evaluate("Agri.store.diagSessionId")
        check("第一次提问后 store 记下会话", bool(sid1), sid1[:12] if sid1 else "")

        # 继续追问（不换会话）
        page.fill("#query", "那茄子呢")
        page.click("#submit")
        page.wait_for_selector("#result .card", timeout=90000)
        sid2 = page.evaluate("Agri.store.diagSessionId")
        check("第二次提问仍为同一会话", sid2 == sid1, f"{sid1[:8]}… vs {sid2[:8]}…")

        # 后端确认：该会话应有 4 条消息
        import httpx
        token = page.evaluate("Agri.store.token")
        h = {"Authorization": f"Bearer {token}"}
        sess = httpx.get(BASE + "/api/v1/chat/sessions", headers=h, timeout=30).json()
        mine = [s for s in sess if s["session_id"] == sid1]
        check("后端同一会话消息数=4（两问两答）",
              mine and mine[0]["message_count"] == 4,
              f"实际 {mine[0]['message_count'] if mine else '无'}")

        # 开新话题后，再提问应为新会话
        page.click("#newTopic")
        page.fill("#query", "小麦什么时候浇水好")
        page.click("#submit")
        page.wait_for_selector("#result .card", timeout=90000)
        sid3 = page.evaluate("Agri.store.diagSessionId")
        check("开新话题后产生新会话", sid3 and sid3 != sid1)

        # 对话页：分组渲染
        page.locator('#nav [data-route="chat"]').first.click()
        page.wait_for_selector("#sessList", timeout=15000)
        body = page.inner_text("#sessList")
        check("列表按「今天/昨天/更早」分组", "今天" in body or "昨天" in body)
        page.screenshot(path=str(SHOT / "session-groups.png"), full_page=False)

        browser.close()

    print()
    if all(ok):
        print(f"全部 {len(ok)} 项通过")
        return 0
    print(f"{ok.count(False)} / {len(ok)} 项失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
