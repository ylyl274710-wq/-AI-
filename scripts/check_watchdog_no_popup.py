"""回归测试：看门狗重启服务时，会不会弹控制台窗口？

为什么需要这个测试
------------------
「桌面一直弹命令行窗口，电脑没法运行了」的真实路径是：

    pg_start() 用 DETACHED_PROCESS
      → PG 自己 AllocConsole() 建可见控制台
      → Windows Terminal 弹窗（一次 4 个）
      → 看门狗每 15 秒重启一次
      → 窗口持续弹，用户无法使用电脑

单测 `_probe_console_flags.py` 只能证明「直接启动会弹窗」。
真正把故障放大的那一步是**看门狗的自动重启**，所以必须端到端测这条路径。

本测试做的事
------------
1. 阳性对照：确认探针能看见窗口（不然「0 个」毫无意义）
2. 杀掉正在运行的 PostgreSQL（按 PID，不碰别人的）
3. 高频采样可见控制台窗口，直到看门狗把它拉起来（或超时）
4. 断言：整个过程中可见控制台窗口数始终为 0，且 PG 确实恢复了

用法
----
    python scripts/check_watchdog_no_popup.py
前置条件：`infra.py serve` 正在运行（看门狗要在）。
退出码 0 = 通过。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import infra  # noqa: E402

CNW = 0x08000000
CL = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"}

u32 = ctypes.WinDLL("user32", use_last_error=True)


def console_windows() -> list[str]:
    """可见的控制台类顶层窗口。★ 不限定宿主进程 ——
    PG 自建的控制台挂在 Windows Terminal 上，只查 conhost 会漏掉。"""
    found: list[str] = []

    def cb(hwnd, _l):
        if not u32.IsWindowVisible(hwnd):
            return True
        c = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, c, 256)
        if c.value not in CL:
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        t = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, t, 512)
        found.append(f"pid={pid.value} {c.value} [{t.value}]")
        return True

    u32.EnumWindows(ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    return found


def positive_control() -> bool:
    """先证明尺子是准的：起一个 cmd 窗口，探针必须看得见它。

    ★ 少了这一步，「0 个窗口」既可能是真的没有，也可能是探针瞎了。
    """
    print("[对照] 起一个 cmd.exe 窗口，确认探针能看见它 ...")
    p = subprocess.Popen(["cmd", "/c", "timeout", "/t", "4", "/nobreak"],
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
    seen = False
    for _ in range(30):
        if console_windows():
            seen = True
            break
        time.sleep(0.3)
    try:
        p.kill()
    except OSError:
        pass
    # 等窗口消失，免得污染后面的基线
    for _ in range(30):
        if not console_windows():
            break
        time.sleep(0.3)
    print(f"  探针{'有效（看得见窗口）' if seen else '★ 失效！下面的结论作废'}")
    return seen


def main() -> int:
    print("=" * 66)
    print("回归测试：看门狗重启服务时不弹控制台窗口")
    print("=" * 66)

    if not infra.port_open(infra.PG_PORT):
        print(f"\n!! PostgreSQL 未在 {infra.PG_PORT} 运行，"
              f"请先启动 `infra.py serve`（看门狗必须在）")
        return 2

    lock = infra.SERVE_LOCK
    if not lock.exists():
        print(f"\n!! 找不到 {lock} —— serve 没在跑，看门狗不在，测不了")
        return 2
    serve_pid = lock.read_text(encoding="ascii").strip()
    if not infra.pid_alive(int(serve_pid)):
        print(f"\n!! serve（PID {serve_pid}）已死，看门狗不在，测不了")
        return 2
    print(f"\n看门狗宿主：serve PID {serve_pid}")

    if not positive_control():
        return 1

    base = console_windows()
    print(f"\n基线：可见控制台窗口 {len(base)} 个（应为 0）")
    if base:
        print("  基线不是 0，先清干净再测：")
        for d in base:
            print("    " + d)
        return 1

    # ---------------- 杀 PG，看看门狗怎么处理 ----------------
    pid = infra.pid_on_port(infra.PG_PORT)
    print(f"\n杀掉 PostgreSQL（PID {pid}，按 PID 精确杀）...")
    infra.kill_pid(pid)
    t_kill = time.monotonic()

    max_seen = 0
    worst: list[str] = []
    recovered_at = None
    deadline = t_kill + 200.0          # 冷启动恢复最长给 200s
    samples = 0
    while time.monotonic() < deadline:
        w = console_windows()
        samples += 1
        if len(w) > max_seen:
            max_seen, worst = len(w), w
        if recovered_at is None and infra.port_open(infra.PG_PORT):
            recovered_at = time.monotonic() - t_kill
            # 恢复后再多采 5 秒，捕捉「起来之后才弹窗」的情况
            extra = time.monotonic() + 5.0
            while time.monotonic() < extra:
                w2 = console_windows()
                samples += 1
                if len(w2) > max_seen:
                    max_seen, worst = len(w2), w2
                time.sleep(0.5)
            break
        time.sleep(0.5)

    print(f"\n采样 {samples} 次（每 0.5s 一次）")
    print(f"  PostgreSQL 恢复用时："
          f"{f'{recovered_at:.1f}s' if recovered_at else '★ 未恢复'}")

    print("\n" + "=" * 66)
    print("结论")
    print("=" * 66)
    ok = True
    if recovered_at is None:
        print("  ✗ 看门狗没能在 200s 内把 PG 拉起来 —— 需要人工看 serve 日志")
        ok = False
    else:
        print(f"  ✓ 看门狗自动恢复了 PostgreSQL（{recovered_at:.1f}s）")
    if max_seen == 0:
        print(f"  ✓ 全程可见控制台窗口始终为 0 个 —— 没有弹窗")
    else:
        print(f"  ✗ 过程中出现过 {max_seen} 个可见控制台窗口：")
        for d in worst:
            print("      " + d)
        ok = False

    print()
    print("通过" if ok else "失败")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
