"""真实浏览器端到端验证（playwright + 系统 Chrome）。

★ 为什么还需要这一层。项目里已经有两层前端检查：
    - 静态检查（接口契约 / DOM id / 注释配平 / 块注释配平）
    - DOM 桩 + vm 执行（check-frontend-runtime.cjs，78 项断言）
  但桩**测不到三件事**，而这正是「前端到底能不能用」的核心：

    1. **CSS 是否真的生效** —— 老人模式字号有没有变大、窄屏有没有溢出、
       卡片有没有重叠。桩没有样式引擎，这类问题它一个都看不见。
    2. **元素是否真的可点** —— 被遮挡、被 z-index 压住、
       `pointer-events: none`。桩的事件派发是直接调用监听器，
       绕过了命中测试，所以「点不到」在桩里永远测不出来。
    3. **真实浏览器的 console 报错与资源 404** —— 桩只执行 JS，
       不加载 CSS/图片/font。

  所以这一层不是「再测一遍」，是测**另外三件事**。

★ 用系统 Chrome（channel="chrome"），不用 playwright 自带的 chromium：
  本机 `%LOCALAPPDATA%/ms-playwright` 里只有 chromium-1134，
  而 playwright 1.63 要的是 chromium_headless_shell-1243 →
  报 `Executable doesn't exist`。系统 Chrome 已经装了，借来用即可，
  省一次 ~120MB 下载。

用法（需要后端已经在跑）：
    .venv/Scripts/python.exe frontend/tools/check-browser-e2e.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

BASE = "http://127.0.0.1:8001/app/"
SHOTS = Path(__file__).resolve().parent / "screenshots"

PASS = 0
FAIL = 0
DETAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK  ] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}")
        if detail:
            print(f"         {detail}")
        DETAILS.append(name + (f" —— {detail}" if detail else ""))


def shot(page: Page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)


def body_font_px(page: Page) -> float:
    return page.evaluate(
        "parseFloat(getComputedStyle(document.body).fontSize)")


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 860})
        page = ctx.new_page()

        console_errs: list[str] = []
        page_errs: list[str] = []
        failed_reqs: list[str] = []
        page.on("console",
                lambda m: console_errs.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page_errs.append(str(e)))
        page.on("requestfailed",
                lambda r: failed_reqs.append(f"{r.url} ({r.failure})"))

        print("\n真实浏览器端到端验证（Chrome headless 1280×860）\n")

        # ---------------------------------------------------------------- #
        print("1. 首屏加载（匿名态）")
        # ---------------------------------------------------------------- #
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#gate", timeout=15000)

        check("页面标题正确", "病虫害" in page.title(),
              f"实际 title = {page.title()!r}")
        check("登录门可见", page.locator("#gate").is_visible())
        check("主界面隐藏", not page.locator("#app").is_visible())
        check("登录门错误区默认隐藏",
              not page.locator("#gateErr").is_visible())
        font0 = body_font_px(page)
        check("样式表已生效（能取到 body 计算字号）", font0 > 0,
              f"body font-size = {font0}px")
        shot(page, "01-gate")

        # ---------------------------------------------------------------- #
        print("\n2. 登录（真实填表 + 真实点击）")
        # ---------------------------------------------------------------- #
        page.fill("#gateUser", "demo")
        page.fill("#gatePass", "demo1234")
        page.click("#gateSubmit")
        page.wait_for_selector("#app", state="visible", timeout=15000)
        page.wait_for_timeout(1200)   # 等 banner / 首页数据

        check("登录后主界面可见", page.locator("#app").is_visible())
        check("登录后登录门隐藏", not page.locator("#gate").is_visible())
        check("顶栏显示用户名",
              page.locator("#whoName").inner_text().strip() == "演示农户",
              f"实际 {page.locator('#whoName').inner_text()!r}")

        # ★★ D25 的真实浏览器回归：降级提示条与 /health 必须**一致**。
        #
        #    ★ 这里**不能**硬编码「提示条里一定有『未配置大模型』」——
        #      那是把断言绑在「当前没配 Key」这个**环境状态**上。
        #      配好 Key 之后这条必然假红，而且会让人误以为提示条坏了
        #      （实际提示条完全正确：它只是不再报一条已经不存在的问题）。
        #      这是同一类错误在本项目里的**第 3 次**
        #      （前两次：老人模式初始状态、存储降级 fixture）。
        #
        #    正确做法：把 /health 当成**真值来源**，由它推出「提示条应该说什么」，
        #    再断言两者一致。这样环境怎么变断言都成立 ——
        #    而且比原来更强：原来只验证了「有 Key 时会报」，现在
        #    「有 Key 时不该报」也一并验证了。
        health = page.evaluate("() => fetch('/api/v1/health').then(r => r.json())")
        banner_txt = page.locator("#banner").inner_text()

        problems: list[str] = []
        if not health.get("llm_enabled"):
            problems.append("未配置大模型")
        if not health.get("vision_enabled"):
            problems.append("暂时无法识别照片")
        if health.get("redis") != "up":
            problems.append("缓存服务不可用")
        # storage 是「实际后端」，minio 只是「端口通不通」——见 D25
        if health.get("storage") == "local":
            problems.append("对象存储")
        elif health.get("minio") != "up":
            problems.append("图片存储")

        check("提示条可见性与 /health 一致",
              page.locator("#banner").is_visible() == bool(problems),
              f"health 推出 {problems}，banner 可见={page.locator('#banner').is_visible()}")
        for p in problems:
            check(f"提示条报出「{p}」", p in banner_txt, f"实际 {banner_txt!r}")
        if not problems:
            check("没有问题时提示条为空白（不硬塞一条无意义提示）",
                  banner_txt.strip() in ("", "⚠️"), f"实际 {banner_txt!r}")

        # 指引要**按问题类型**给，不能一律「去 .env 配 Key」。
        # 判据同样是从实际内容反推，不预设环境。
        has_cfg = any(s in banner_txt for s in ("未配置大模型", "暂时无法识别照片"))
        has_infra = any(s in banner_txt for s in ("缓存服务不可用", "对象存储", "图片存储"))
        check("指引按问题类型给出（配置类→.env / 服务类→infra.py）",
              has_cfg == ("API Key" in banner_txt)
              and has_infra == ("infra.py status" in banner_txt),
              f"cfg={has_cfg} infra={has_infra} banner={banner_txt!r}")
        shot(page, "02-home")

        # ---------------------------------------------------------------- #
        print("\n3. 六个页面逐个真实点击")
        # ---------------------------------------------------------------- #
        pages = page.evaluate("Object.keys(window.Agri.pages)")
        check("注册了 6 个页面", len(pages) == 6, f"实际 {pages}")
        for i, key in enumerate(pages, start=1):
            nav = page.locator(f'#nav [data-route="{key}"]')
            if nav.count() == 0 or not nav.first.is_visible():
                check(f"导航项 {key} 可点", False, "在 #nav 里找不到可见的该项")
                continue
            nav.first.click()
            # ★ 不能固定 sleep 900ms —— 那是把断言押在「这台机器此刻多快」上。
            #   实测踩过：这次浏览器检查与 smoke_e2e 并发跑（都在压同一个库），
            #   farm 页在 900ms 时还停在「加载中…」，报了一个**假红**；
            #   单独跑时同一个页面 1 秒内就渲染出 198 个字，完全正常。
            #   正确做法：等「加载中」消失（最多 15 秒），断言落在
            #   「页面最终会渲染出来」这个不变量上。超时也不在这里判失败，
            #   让下面的断言带着当时的内容去报错。
            try:
                page.wait_for_function(
                    "() => !document.getElementById('view')"
                    ".innerText.includes('加载中')",
                    timeout=15000)
            except Exception:  # noqa: BLE001
                pass
            has_content = page.evaluate(
                "document.getElementById('view').children.length > 0")
            view_txt = page.evaluate(
                "document.getElementById('view').innerText.trim()")
            check(f"页面 {key} 渲染出内容（子节点>0 且文字>20 字）",
                  bool(has_content) and len(view_txt) > 20,
                  f"children={has_content}, textLen={len(view_txt)}, "
                  f"开头={view_txt[:60]!r}")
            shot(page, f"03-{i}-{key}")

        # ---------------------------------------------------------------- #
        print("\n4. 老人模式（★ 桩测不到：字号是否真的变了）")
        # ---------------------------------------------------------------- #
        page.locator('#nav [data-route="home"]').first.click()
        page.wait_for_timeout(500)
        elder_before = page.evaluate(
            "document.body.classList.contains('elder')")
        font_before = body_font_px(page)

        page.click("#elderBtn")
        page.wait_for_timeout(700)
        elder_after = page.evaluate(
            "document.body.classList.contains('elder')")
        font_after = body_font_px(page)

        check("点击后 body.elder 状态翻转", elder_after != elder_before,
              f"{elder_before} → {elder_after}")

        # ★ 不能假设「初始是普通模式」。demo 用户的 elder_mode 默认是**开启**的
        #   （core.js: `store.setElder(me.elder_mode !== false)`），
        #   所以第一次点击其实是「关掉」。
        #   断言必须与初始状态无关，落在不变量上：**开着的字号 > 关着的字号**。
        #   （第一版按「点一下就是开启」写，红了两次 —— 那是我的测试错了。）
        font_elder = font_after if elder_after else font_before
        font_normal = font_before if elder_after else font_after
        check("★ 老人模式字号确实更大（CSS 生效，不只是加了个 class）",
              font_elder > font_normal,
              f"elder={font_elder}px / normal={font_normal}px")
        check("老人模式按钮图标与状态一致",
              page.locator("#elderIcon").inner_text().strip()
              == ("Aa+" if elder_after else "Aa"),
              f"elder={elder_after}，图标={page.locator('#elderIcon').inner_text()!r}")
        shot(page, "04-elder-toggle")

        page.click("#elderBtn")
        page.wait_for_timeout(500)
        check("再次点击能切回原状态",
              page.evaluate("document.body.classList.contains('elder')")
              == elder_before)

        # ---------------------------------------------------------------- #
        print("\n5. 账号弹窗 + 退出登录（真实点击，含命中测试）")
        # ---------------------------------------------------------------- #
        page.click("#who")
        page.wait_for_timeout(600)
        modal_visible = page.evaluate(
            "!!document.querySelector('.modal, .dialog, [class*=modal]')")
        check("点顶栏账号区弹出弹窗", bool(modal_visible))
        shot(page, "05-account-modal")

        # 点弹窗里的「退出登录」
        ok_btn = page.get_by_text("退出登录", exact=True)
        if ok_btn.count():
            ok_btn.first.click()
            page.wait_for_timeout(900)
            check("退出后回到登录门", page.locator("#gate").is_visible())
            check("退出后 token 已清空",
                  not page.evaluate("!!localStorage.getItem('agri.token')"))
        else:
            check("弹窗里有「退出登录」按钮", False, "找不到该按钮")

        ctx.close()

        # ---------------------------------------------------------------- #
        print("\n6. 窄屏（375×667，手机尺寸）")
        # ---------------------------------------------------------------- #
        m_ctx = browser.new_context(
            viewport={"width": 375, "height": 667}, device_scale_factor=2,
            is_mobile=True, has_touch=True)
        m = m_ctx.new_page()
        m.goto(BASE, wait_until="domcontentloaded")
        m.wait_for_selector("#gate", timeout=15000)
        m.fill("#gateUser", "demo")
        m.fill("#gatePass", "demo1234")
        m.click("#gateSubmit")
        m.wait_for_selector("#app", state="visible", timeout=15000)
        m.wait_for_timeout(1500)

        # ★ 桩测不到：窄屏是否横向溢出（元素被挤出屏幕 → 用户要左右拖）
        overflow = m.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("★ 窄屏无横向溢出（scrollWidth ≤ clientWidth）", overflow <= 1,
              f"溢出 {overflow}px")
        check("窄屏底部 tabbar 可见（nav 收起）",
              m.locator("#tabbar").is_visible())
        shot(m, "06-mobile-home")

        m.locator('#tabbar [data-route="diagnose"]').first.click()
        m.wait_for_timeout(900)
        shot(m, "07-mobile-diagnose")
        m_ctx.close()

        # ---------------------------------------------------------------- #
        print("\n7. 全局：真实浏览器的报错与失败请求")
        # ---------------------------------------------------------------- #
        real_console = [e for e in console_errs
                        if "favicon" not in e.lower()]
        check("无 JS 运行时异常（pageerror）", not page_errs,
              " | ".join(page_errs[:3]))
        check("无 console error（忽略 favicon）", not real_console,
              " | ".join(real_console[:3]))
        real_failed = [r for r in failed_reqs if "favicon" not in r.lower()]
        check("无资源加载失败", not real_failed, " | ".join(real_failed[:3]))

        browser.close()

    print("\n" + "─" * 64)
    if FAIL:
        print(f"失败 {FAIL} 项，通过 {PASS}/{PASS + FAIL}")
        print("\n失败明细：")
        for d in DETAILS:
            print("  · " + d)
    else:
        print(f"全部通过：{PASS}/{PASS}")
    print(f"截图目录：{SHOTS}")
    print("─" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
