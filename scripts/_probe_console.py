"""受控实验：pg_start() 的启动方式会不会创建控制台？

判定依据
--------
`detached()` 用的是 DETACHED_PROCESS，这种进程**没有可继承的控制台**。
所以只要它（或它的子进程）自己调 AllocConsole，系统就必然新建一个
conhost.exe。⇒ 数 conhost 的 PID 增量，就能判定有没有新建控制台，
**不需要肉眼盯着屏幕看窗口**。

阳性对照
--------
只测 postgres 是不够的：万一我的探测方法本身失灵，「没有新增」这个
结论就毫无意义。所以先用一个**必然创建控制台**的东西（cmd.exe）跑一遍，
它必须让 conhost +1，否则说明探针坏了，postgres 的结果作废。
（SOUL.md：区分产品缺陷和测试缺陷 —— 先证明尺子是准的。）

用法
----
    python scripts/_probe_console.py
需要先停掉本项目服务（本脚本只做一次冷启动 + 立即停止）。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import infra  # noqa: E402

CNW = 0x08000000
DETACHED = 0x00000008
NEWGRP = 0x00000200


def conhost_pids() -> set[str]:
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq conhost.exe", "/NH"],
                       capture_output=True, text=True, errors="replace",
                       creationflags=CNW)
    out: set[str] = set()
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].lower().startswith("conhost"):
            out.add(parts[1])
    return out


def settle(sec: float = 1.5) -> None:
    time.sleep(sec)


def main() -> int:
    print("=" * 62)
    print("受控实验：DETACHED_PROCESS 启动的服务会不会创建控制台")
    print("=" * 62)

    if infra.port_open(infra.PG_PORT):
        print(f"!! PG 已在 {infra.PG_PORT} 运行，请先停掉再跑本实验")
        return 1

    base = conhost_pids()
    print(f"\n基线 conhost 数：{len(base)}")

    # ---------------- 阳性对照 ----------------
    print("\n[对照] cmd.exe（必然创建控制台）")
    before = conhost_pids()
    p = subprocess.Popen(["cmd", "/c", "echo", "probe"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL,
                         creationflags=DETACHED | NEWGRP)
    settle(1.5)
    after = conhost_pids()
    cmd_new = after - before
    print(f"  DETACHED 方式新增 conhost：{len(cmd_new)} 个  {sorted(cmd_new)}")
    p.wait(timeout=10)
    settle(1.0)

    before2 = conhost_pids()
    p2 = subprocess.Popen(["cmd", "/c", "echo", "probe"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL,
                          creationflags=CNW)
    settle(1.5)
    after2 = conhost_pids()
    print(f"  CREATE_NO_WINDOW 方式新增 conhost：{len(after2 - before2)} 个")
    p2.wait(timeout=10)
    settle(1.0)

    probe_ok = len(cmd_new) >= 1
    print(f"\n  探针有效性：{'通过（尺子是准的）' if probe_ok else '★ 失效！下面结论作废'}")

    # ---------------- 待测对象 ----------------
    print("\n[待测] postgres.exe（与 pg_start() 完全相同的启动方式）")
    before3 = conhost_pids()
    print(f"  启动前 conhost 数：{len(before3)}")
    proc = infra.detached(
        [infra.pg_bin("postgres"), "-D", infra.win(infra.PGDATA),
         "-p", str(infra.PG_PORT)],
        cwd=infra.PGSQL / "bin", outfile=infra.LOGS / "postgres.log")

    ok = False
    for _ in range(300):          # 最多 150s，与 pg_start 一致
        if infra.port_open(infra.PG_PORT):
            ok = True
            break
        time.sleep(0.5)
    settle(2.0)
    after3 = conhost_pids()
    pg_new = after3 - before3
    print(f"  启动后 conhost 数：{len(after3)}")
    print(f"  ★ postgres 新增 conhost：{len(pg_new)} 个  {sorted(pg_new)}")

    # 顺带看 PG 的子进程挂在哪个 session
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq postgres.exe", "/NH"],
                       capture_output=True, text=True, errors="replace",
                       creationflags=CNW)
    print("\n  postgres.exe 进程列表（第 3 列是 Session）：")
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print("    " + line.rstrip())

    # ---------------- 收尾 ----------------
    print("\n停止 PG ...")
    if ok:
        infra.pg_stop()
    else:
        print(f"  !! PG 未在 150s 内监听 {infra.PG_PORT}，强制清理")
        pid = infra.pid_on_port(infra.PG_PORT)
        if pid:
            infra.kill_pid(pid)
        elif proc.poll() is None:
            infra.kill_pid(proc.pid)

    print("\n" + "=" * 62)
    print("结论")
    print("=" * 62)
    if not probe_ok:
        print("  探针失效，无法判定 —— 需要换一种检测手段。")
    elif len(pg_new) == 0:
        print("  postgres 启动**没有**新建控制台。")
        print("  ⇒ PG 不是弹窗来源。窗口只可能来自别处。")
    else:
        print(f"  postgres 启动**新建了 {len(pg_new)} 个控制台** —— 这就是弹窗来源。")
        print("  ⇒ 需要给 postgres 换一种启动方式（如注册为 Windows 服务）。")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
