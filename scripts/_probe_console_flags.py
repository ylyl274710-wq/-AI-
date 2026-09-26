"""受控实验 v3：两种启动标志下，postgres 到底弹不弹窗口？

为什么要有 v3
-------------
v1 数 conhost 增量 → 得出「PG 创建 4 个控制台」，但没验证可见性。
v2 想验证可见性，判定逻辑却只统计了 **conhost 进程拥有的**窗口，
  结果漏掉了真正的窗口宿主（Windows Terminal 的
  CASCADIA_HOSTING_WINDOW_CLASS），于是错误地得出「不是弹窗来源」。
  ★ 这是**测试缺陷**：产品行为没变，是我量错了。修尺子，不是修产品。

v3 的正确判据
-------------
统计**所有可见的「控制台类」顶层窗口**（不限定宿主进程是谁）：
    ConsoleWindowClass / CASCADIA_HOSTING_WINDOW_CLASS / PseudoConsoleWindow
在「启动前 / 运行中 / 停止后」三个时刻各数一次。

背景（Windows 11）
------------------
系统默认终端是 Windows Terminal。任何进程调 AllocConsole() 建控制台，
都会**新开一个 Windows Terminal 窗口**。
所以「进程有没有自己的控制台」直接决定了会不会弹窗：
    DETACHED_PROCESS  → 系统不给控制台 → 进程自己 AllocConsole → 弹窗
    CREATE_NO_WINDOW  → 系统给一个隐藏控制台 → 进程无需自建 → 不弹窗
本实验就是验证这个推断。

用法
----
    python scripts/_probe_console_flags.py
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
DETACHED = 0x00000008
NEWGRP = 0x00000200

CONSOLE_CLASSES = {
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "PseudoConsoleWindow",
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def console_windows() -> list[dict]:
    """当前会话所有**可见的**控制台类顶层窗口。不限定宿主进程。"""
    out: list[dict] = []

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value not in CONSOLE_CLASSES:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        out.append({"pid": pid.value, "cls": cls.value, "title": title.value})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def show(tag: str) -> int:
    ws = console_windows()
    print(f"  [{tag}] 可见控制台类窗口 {len(ws)} 个")
    for w in ws:
        t = w["title"] or "(无标题)"
        print(f"      pid={w['pid']:<7} {w['cls']:<32} {t}")
    return len(ws)


def wait_pg(timeout: float = 150.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if infra.port_open(infra.PG_PORT):
            return True
        time.sleep(0.5)
    return False


def stop_pg() -> None:
    if infra.port_open(infra.PG_PORT):
        infra.pg_stop()
    else:
        pid = infra.pid_on_port(infra.PG_PORT)
        if pid:
            infra.kill_pid(pid)
    for _ in range(40):
        if not infra.port_open(infra.PG_PORT):
            return
        time.sleep(0.5)


def trial(name: str, flags: int) -> tuple[int, int]:
    """跑一轮：返回 (启动前窗口数, 运行中窗口数)"""
    print(f"\n{'=' * 66}\n【{name}】flags=0x{flags:08X}\n{'=' * 66}")
    stop_pg()
    time.sleep(1.5)
    before = show("启动前")

    f = open(infra.LOGS / "postgres.log", "ab")
    infra.log(f"$ postgres ... (probe flags=0x{flags:08X})")
    proc = subprocess.Popen(
        [infra.pg_bin("postgres"), "-D", infra.win(infra.PGDATA),
         "-p", str(infra.PG_PORT)],
        cwd=str(infra.PGSQL / "bin"),
        stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        creationflags=flags)

    ok = wait_pg()
    time.sleep(3.0)
    during = show(f"运行中{'（PG 未起来）' if not ok else ''}")

    print(f"  → 本轮新增可见控制台窗口：{during - before}")
    stop_pg()
    time.sleep(2.0)
    if proc.poll() is None:
        infra.kill_pid(proc.pid)
    after = show("停止后")
    print(f"  → 收尾（应回到 {before} 附近）：{after}")
    return before, during


def main() -> int:
    print("=" * 66)
    print("受控实验 v3：DETACHED_PROCESS vs CREATE_NO_WINDOW")
    print("=" * 66)
    print("判据：所有可见的「控制台类」顶层窗口数量变化（不限定宿主进程）")

    if infra.port_open(infra.PG_PORT):
        print(f"\n!! PG 已在 {infra.PG_PORT} 运行，请先停掉再跑")
        return 1

    r1 = trial("A. DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP（现状）",
               DETACHED | NEWGRP)
    r2 = trial("B. CREATE_NO_WINDOW（候选修复）", CNW)

    print("\n" + "=" * 66)
    print("结论")
    print("=" * 66)
    a_new = r1[1] - r1[0]
    b_new = r2[1] - r2[0]
    print(f"  A 现状 DETACHED_PROCESS  ：新增 {a_new} 个可见控制台窗口")
    print(f"  B 候选 CREATE_NO_WINDOW  ：新增 {b_new} 个可见控制台窗口")
    print()
    if a_new > 0 and b_new == 0:
        print("  ★ 确认：现状会弹窗，改用 CREATE_NO_WINDOW 可以消除。")
        print("  ⇒ 修复方向：pg_start() 用 CREATE_NO_WINDOW 而非 detached()。")
    elif a_new == 0 and b_new == 0:
        print("  两者都不弹窗 —— 那弹窗另有来源，需要继续查。")
    elif b_new > 0:
        print("  CREATE_NO_WINDOW 也弹窗，换方案（如注册 Windows 服务）。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
