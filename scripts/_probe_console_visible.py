"""受控实验 v2：postgres 自建的那个控制台，窗口**可见吗**？

为什么需要这一步
----------------
v1 证明了 postgres 启动会新建 4 个 conhost。但**不能**据此说「就是弹窗」——
因为 CREATE_NO_WINDOW 同样会新建 conhost，而它的窗口是不可见的。
「有控制台」≠「有可见窗口」。把这两者混为一谈就是误报。

本脚本用 Win32 枚举当前会话的**顶层可见窗口**，按进程归类，
直接回答：新出现的 conhost 有没有可见窗口。

关键区分（v1 已给出线索）
------------------------
- CREATE_NO_WINDOW：系统建一个**隐藏**控制台 → conhost 有，窗口无。
- DETACHED_PROCESS：系统**不给**控制台。若此时进程仍拿到控制台，
  只能是它自己调了 AllocConsole() —— 而 AllocConsole 建的控制台
  **默认是可见的**。
所以 v1 里 DETACHED 下的 cmd.exe 也 +1 conhost 是预期的（cmd 会自己建）。

用法
----
    python scripts/_probe_console_visible.py
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
user32 = ctypes.WinDLL("user32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def visible_windows() -> list[dict]:
    """当前会话所有**可见**顶层窗口：pid / class / title / hwnd"""
    out: list[dict] = []

    def cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        out.append({"pid": pid.value, "cls": cls.value,
                    "title": title.value, "hwnd": int(hwnd)})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def pids_of(image: str) -> set[int]:
    r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
                       capture_output=True, text=True, errors="replace",
                       creationflags=CNW)
    s: set[int] = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower() == image.lower():
            try:
                s.add(int(parts[1]))
            except ValueError:
                pass
    return s


def report(tag: str) -> tuple[set[int], list[dict]]:
    ch = pids_of("conhost.exe")
    wins = visible_windows()
    ch_win = [w for w in wins if w["pid"] in ch]
    print(f"\n[{tag}] conhost 进程 {len(ch)} 个；其中带可见窗口的：{len(ch_win)} 个")
    for w in ch_win:
        print(f"    conhost pid={w['pid']}  class={w['cls']}  title=[{w['title']}]")
    # 顺带列出所有可见的「命令行类」窗口，便于人工确认
    cmdlike = [w for w in wins if w["cls"] in
               ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow")]
    if cmdlike:
        print(f"    可见控制台类窗口共 {len(cmdlike)} 个：")
        for w in cmdlike:
            print(f"      pid={w['pid']} class={w['cls']} title=[{w['title']}]")
    else:
        print("    （当前没有任何可见的控制台类窗口）")
    return ch, ch_win


def main() -> int:
    print("=" * 66)
    print("受控实验 v2：postgres 自建的控制台，窗口是否可见？")
    print("=" * 66)

    if infra.port_open(infra.PG_PORT):
        print(f"!! PG 已在 {infra.PG_PORT} 运行，请先停掉再跑本实验")
        return 1

    _, base_win = report("基线（PG 未运行）")
    base_n = len(base_win)

    print("\n启动 postgres（与 pg_start() 完全相同的标志）...")
    proc = infra.detached(
        [infra.pg_bin("postgres"), "-D", infra.win(infra.PGDATA),
         "-p", str(infra.PG_PORT)],
        cwd=infra.PGSQL / "bin", outfile=infra.LOGS / "postgres.log")
    ok = False
    for _ in range(300):
        if infra.port_open(infra.PG_PORT):
            ok = True
            break
        time.sleep(0.5)
    time.sleep(2.5)          # 等 PG 把后台进程都拉起来

    _, after_win = report("PG 运行中")

    print("\n" + "=" * 66)
    print("结论")
    print("=" * 66)
    if not ok:
        print("  PG 没起来，实验无效")
    elif len(after_win) > base_n:
        print(f"  ★ PG 启动后，**可见**的控制台窗口增加了 "
              f"{len(after_win) - base_n} 个 —— 确认就是弹窗来源。")
    else:
        print("  PG 启动后**没有新增可见窗口**。")
        print("  ⇒ PG 会创建控制台，但窗口是隐藏的，**不是**弹窗来源。")
        print("     v1 的 4 个 conhost 只是不可见的控制台宿主。")

    print("\n停止 PG ...")
    if ok:
        infra.pg_stop()
    else:
        pid = infra.pid_on_port(infra.PG_PORT)
        if pid:
            infra.kill_pid(pid)
        elif proc.poll() is None:
            infra.kill_pid(proc.pid)
    time.sleep(1.5)
    _, final_win = report("PG 已停止")
    print(f"\n  收尾检查：可见控制台窗口 {len(final_win)} 个"
          f"（基线是 {base_n} 个）")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
