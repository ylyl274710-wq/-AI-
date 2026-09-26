# -*- coding: utf-8 -*-
"""验证：能否用 CREATE_BREAKAWAY_FROM_JOB 让子进程逃出沙箱 job object。

背景：沙箱用 Windows Job Object 包住每条命令的进程树。命令结束时 job 关闭，
所有成员进程被连坐 kill（表现：进程无 clean shutdown 记录就消失）。
DETACHED_PROCESS 只脱离控制台，不脱离 job。要脱离 job 需要
CREATE_BREAKAWAY_FROM_JOB，且 job 必须允许 breakaway。
"""
import os
import subprocess
import sys

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008

flags = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB

pidfile = r"E:/agri-infra/logs/probe.pid"
if os.path.exists(pidfile):
    os.remove(pidfile)

code = (
    "import time,os\n"
    f"open(r'{pidfile}','w').write(str(os.getpid()))\n"
    "time.sleep(45)\n"
)
try:
    p = subprocess.Popen([sys.executable, "-c", code],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    print("Popen OK, probe pid =", p.pid)
except OSError as e:
    print("Popen FAILED:", e)
    print("  winerror =", getattr(e, "winerror", None))
    print("  winerror 5 (ACCESS_DENIED) => job 不允许 breakaway")
