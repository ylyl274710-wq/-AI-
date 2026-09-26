# -*- coding: utf-8 -*-
"""对照实验 v3：不删除任何文件（v2 的 os.remove 触发了 safe-delete 护栏）。

父进程等待 3 秒确保子进程完成初始化，然后父进程继续常驻，
用于验证「后台常驻任务能否让子进程跨命令存活」。
"""
import os
import subprocess
import sys
import time

LOG = r"E:/agri-infra/logs"
os.makedirs(LOG, exist_ok=True)

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

STAMP = str(int(time.time()))  # 用时间戳区分本轮，避免依赖删除


def code(tag: str) -> str:
    return (
        "import time,os\n"
        f"open(r'{LOG}/p_{tag}_{STAMP}.pid','w').write(str(os.getpid()))\n"
        "time.sleep(600)\n"
    )


variants = [
    ("a", DETACHED_PROCESS, "DETACHED_PROCESS"),
    ("b", DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, "DETACHED|NEW_GROUP"),
    ("c", 0, "默认（无标志）"),
]

print(f"STAMP={STAMP}")
for tag, flags, label in variants:
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", code(tag)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True,
        )
        print(f"p_{tag}  flags=0x{flags:08X}  ({label})  pid={proc.pid}")
    except OSError as e:
        print(f"p_{tag}  FAILED: {e}")

time.sleep(3)
for tag, _, _ in variants:
    p = os.path.join(LOG, f"p_{tag}_{STAMP}.pid")
    print(f"  p_{tag}_{STAMP}.pid = {open(p).read().strip() if os.path.exists(p) else '(无)'}")
print("READY")
