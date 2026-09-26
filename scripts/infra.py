#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
agri-pest-agent 便携基础设施管理器（Windows / 无 Docker 场景）

统一管理 PostgreSQL 17 + pgvector、Redis、MinIO 的初始化与启停。
所有二进制与数据都放在项目内 _infra/ 下，不写系统目录、不注册系统服务。

用法:
    python scripts/infra.py init      # 首次初始化（initdb + 装 pgvector + 建库建角色）
    python scripts/infra.py up        # 启动全部
    python scripts/infra.py down      # 停止全部
    python scripts/infra.py status    # 查看状态
    python scripts/infra.py psql      # 打开 psql 交互
    python scripts/infra.py pg        # 只拉起 PostgreSQL（它最容易掉）
    python scripts/infra.py serve     # 启动全部并常驻 + 看门狗（推荐，见下）

★ 由 AI 助手代管本机环境时，必须用 serve 而不是 up：
  宿主给每条命令的进程树套了 Windows Job Object，命令一结束就连坐 kill
  全部成员进程。serve 把自己挂在后台常驻任务下，子进程才活得下来，
  并且每 15 秒巡检一次端口，掉线的服务自动重启。
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# ★★ 进程创建与停止的两条铁律（都是踩过坑才写下的）
#
# 1) 任何子进程都必须带 CREATE_NO_WINDOW。
#    否则当父进程自己没有控制台时（被沙箱以后台任务方式拉起的情况），
#    Windows 会给每个子进程**新开一个可见的控制台窗口**。
#    实测症状：桌面上不停弹命令行运行框。netstat / taskkill / pg_ctl / psql
#    全都是控制台程序，一个都不能漏。
#
# 2) 停止服务**只能用 PID 或端口精确定位，绝不能用 `taskkill /IM <镜像名>`**。
#    /IM 是按镜像名杀全局，会把**别人的**同名进程一起杀掉。
#    实测后果：三个 serve 进程并存时，任何一个退出都执行 cmd_down，
#    把另外两个正在托管的 PostgreSQL 一起杀掉 → 服务反复掉线 →
#    日志里出现莫名其妙的 0xC000013A → 排查方向被完全带偏。
#
# ★ 这两个常量必须定义在**最前面**：下面的 _resolve_infra_root() 在模块
#   导入期就会被调用，它内部要用 CREATE_NO_WINDOW。定义在文件中部会导致
#   NameError —— 而且只在「需要建 junction」的分支才触发，很容易漏掉。
# ---------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000

# ⛔ DETACHED_PROCESS —— **不要再用它启动本项目任何服务**。
#    它语义上「不给进程控制台」，但 PostgreSQL 在 Windows 上会因此自己
#    调 AllocConsole() 建一个可见控制台 → 每次启动弹 4 个终端窗口。
#    保留这个常量只为让下面的注释有据可查，代码里已无引用。
#    实测见 scripts/_probe_console_flags.py（A 组 4 个 / B 组 0 个）。
DETACHED_PROCESS = 0x00000008

# 本进程**实际启动过**哪些服务（服务名 -> PID）。
# cmd_down 默认只停这里面的 —— 不是我们拉起来的服务，不该由我们杀掉。
# 这条规则防的是同一个 bug 的另一种形态：用户自己开着的 PG，被我们的
# serve 退出时顺手杀了。
_STARTED: dict[str, int] = {}

# ---------------------------------------------------------------------------
# ★ 关键：PostgreSQL 在 Windows 上无法在含中文的路径下初始化。
#   initdb 的 post-bootstrap 阶段会把路径/环境以系统 ANSI 代码页（GBK）编码
#   传给后台进程，而后台按 UTF8 解析，直接 FATAL:
#       invalid byte sequence for encoding "UTF8": 0xcf 0xee
#   （0xCFEE 正是「项」的 GBK 编码）
#   解决办法：建一个纯 ASCII 的目录联接（junction）指向项目内的 _infra，
#   文件仍然全部落在项目目录里，只是 PostgreSQL 看到的是 ASCII 路径。
# ---------------------------------------------------------------------------
_ASCII_LINK = Path(os.environ.get("AGRI_INFRA_LINK", "E:/agri-infra"))


def _is_ascii(p: Path) -> bool:
    try:
        str(p).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _resolve_infra_root() -> Path:
    """返回一个 ASCII 安全的基础设施根目录。"""
    project_infra = ROOT / "_infra"
    if _is_ascii(project_infra):
        return project_infra
    if not _ASCII_LINK.exists():
        _ASCII_LINK.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(_ASCII_LINK), str(project_infra)],
            capture_output=True, text=True,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        if r.returncode != 0:
            raise SystemExit(
                "无法创建 ASCII 目录联接（PostgreSQL 不支持中文路径）。\n"
                f"请手动执行：cmd /c mklink /J {_ASCII_LINK} \"{project_infra}\"\n"
                f"{r.stdout}{r.stderr}")
    return _ASCII_LINK


INFRA = _resolve_infra_root()
PGSQL = INFRA / "pgsql"
REDIS = INFRA / "redis"
MINIO_DIR = INFRA / "minio"
DATA = INFRA / "data"
LOGS = INFRA / "logs"
PGDATA = DATA / "pgdata"

PG_PORT = int(os.environ.get("AGRI_PG_PORT", "5432"))
REDIS_PORT = int(os.environ.get("AGRI_REDIS_PORT", "6379"))
MINIO_PORT = int(os.environ.get("AGRI_MINIO_PORT", "9000"))
MINIO_CONSOLE_PORT = int(os.environ.get("AGRI_MINIO_CONSOLE_PORT", "9001"))
APP_PORT = int(os.environ.get("AGRI_APP_PORT", "8001"))

DB_NAME = os.environ.get("AGRI_DB_NAME", "agri_pest")
DB_USER = os.environ.get("AGRI_DB_USER", "agri")
DB_PASSWORD = os.environ.get("AGRI_DB_PASSWORD", "agri_dev_pwd")
SUPERUSER = "postgres"

MINIO_ROOT_USER = os.environ.get("AGRI_MINIO_USER", "agriadmin")
MINIO_ROOT_PASSWORD = os.environ.get("AGRI_MINIO_PASSWORD", "agri_dev_pwd")


def log(msg: str) -> None:
    print(f"[infra] {msg}", flush=True)


def win(path: Path) -> str:
    return str(path)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def _no_window() -> int:
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def pid_on_port(port: int) -> int | None:
    """返回正在 LISTENING 该端口的进程 PID。

    用 netstat 而不是 psutil —— 保持零第三方依赖。
    ★ 必须带 CREATE_NO_WINDOW，否则弹窗。
    """
    try:
        r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                           capture_output=True, text=True, errors="replace",
                           creationflags=_no_window())
    except OSError:
        return None
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" \
                and parts[3].upper() == "LISTENING":
            if parts[1].endswith(":" + str(port)):
                try:
                    return int(parts[4])
                except ValueError:
                    continue
    return None


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                       capture_output=True, text=True, errors="replace",
                       creationflags=_no_window())
    return str(pid) in (r.stdout or "")


def kill_pid(pid: int) -> None:
    """按 PID 杀进程（含子树）。这是唯一允许的杀法。"""
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=_no_window())


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    log("$ " + " ".join(cmd))
    kw.setdefault("creationflags", _no_window())
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def run_detached(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """执行并立即返回，不捕获子进程输出。

    ★ 为什么 pg_ctl 必须用这个而不是 run()：
      pg_ctl 启动 postmaster 后，postmaster 会继承父进程的 stdout/stderr 句柄。
      如果这里用 capture_output=True（管道），管道写端一直被 postmaster 持有，
      subprocess.run 会永远等不到 EOF —— 表现为命令「卡死」但服务其实已经起来了。
      所以统一把 stdio 接到 DEVNULL，启动结果用端口探测来判断。
    """
    log("$ " + " ".join(cmd))
    kw.setdefault("creationflags", _no_window())
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL, **kw)


def spawn_hidden(cmd: list[str], cwd: Path, outfile: Path,
                 env: dict | None = None) -> subprocess.Popen:
    """后台常驻启动服务，且**绝不产生可见的控制台窗口**。

    ★ 这里踩过一个很大的坑，记录清楚免得再犯。

      原来用的是 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`。
      `DETACHED_PROCESS` 的语义是「系统**不给**你这个进程控制台」。
      但 PostgreSQL 在 Windows 上发现自己没有控制台时，会自己调
      `AllocConsole()` 建一个 —— 而 Windows 11 的默认终端是
      Windows Terminal，于是**每建一个控制台就弹一个终端窗口**。
      postmaster 加后台进程一共弹 4 个。实测（scripts/_probe_console_flags.py，
      单变量对照）：
          A. DETACHED_PROCESS   → 新增 4 个可见控制台窗口
          B. CREATE_NO_WINDOW   → 新增 0 个

      改用 `CREATE_NO_WINDOW` 后，系统给的是「一个**隐藏**的控制台」，
      进程有控制台可用，不需要自建，窗口数归零。

    ★ 附带修掉的另一个问题：日志里反复出现的
         terminated by exception 0xC000013A   (STATUS_CONTROL_C_EXIT)
      正是来自这里 —— 那个**可见**的控制台窗口一旦被用户关掉、
      或随宿主终端销毁，控制台里所有进程都会收到 CTRL_CLOSE_EVENT 而退出。
      隐藏控制台没有这个入口，PG 就不会再莫名掉线。

    ★ 关于 stdout 必须接 DEVNULL / 文件而不是管道：
      postmaster 会继承父进程的 stdout 句柄。如果用 capture_output（管道），
      管道写端被 postmaster 一直持有，subprocess.run 永远等不到 EOF，
      表现为命令「卡死」但服务其实已经起来了。
    """
    outfile.parent.mkdir(parents=True, exist_ok=True)
    f = open(outfile, "ab")
    flags = 0
    if os.name == "nt":
        # ★ 只能是 CREATE_NO_WINDOW，不能带 DETACHED_PROCESS ——
        #   两者同时给时 DETACHED_PROCESS 优先，等于白改。
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    log("$ " + " ".join(cmd) + f"  (log -> {outfile.name})")
    return subprocess.Popen(cmd, cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, creationflags=flags)


# --------------------------------------------------------------------------- #
# PostgreSQL
# --------------------------------------------------------------------------- #
def pg_bin(name: str) -> str:
    return win(PGSQL / "bin" / f"{name}.exe")


def pg_env() -> dict:
    env = os.environ.copy()
    env["PGPASSWORD"] = DB_PASSWORD
    return env


def pg_initdb() -> None:
    if (PGDATA / "PG_VERSION").exists():
        log("pgdata 已存在，跳过 initdb")
        return
    PGDATA.mkdir(parents=True, exist_ok=True)
    pwfile = DATA / ".pgpw"
    pwfile.write_text(SUPERUSER + "\n", encoding="utf-8")
    logfile = LOGS / "initdb.log"
    LOGS.mkdir(parents=True, exist_ok=True)
    log("$ initdb -D <pgdata> ...")
    with open(logfile, "wb") as lf:
        r = subprocess.run(
            [pg_bin("initdb"), "-D", win(PGDATA), "-U", SUPERUSER,
             "-E", "UTF8", "--no-locale", "-A", "scram-sha-256",
             f"--pwfile={win(pwfile)}"],
            stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=_no_window())
    pwfile.unlink(missing_ok=True)
    if r.returncode != 0 or not (PGDATA / "PG_VERSION").exists():
        tail = ""
        if logfile.exists():
            tail = logfile.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise SystemExit("initdb 失败（详见 _infra/logs/initdb.log）:\n" + tail)
    log("initdb 完成")


def pg_install_pgvector() -> None:
    """把 pgvector 预编译产物拷进 PostgreSQL 安装目录。"""
    src = INFRA / "pgvector"
    if not src.exists():
        raise SystemExit(f"找不到 pgvector 解压目录: {src}")
    copied = []
    for rel in ("lib/vector.dll",
                "include/server/extension/vector"):
        s = src / rel
        d = PGSQL / rel
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
            copied.append(rel)
        elif s.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            copied.append(rel)
    ext_src = src / "share/extension"
    if ext_src.exists():
        shutil.copytree(ext_src, PGSQL / "share/extension", dirs_exist_ok=True)
        copied.append("share/extension/*")
    log("pgvector 文件已安装: " + ", ".join(copied))


def pg_start() -> None:
    if port_open(PG_PORT):
        log(f"PostgreSQL 已在 {PG_PORT} 运行")
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    # ★ 为什么直接拉 postgres.exe 而不用 `pg_ctl start`：
    #   pg_ctl 把 postmaster 作为「孙进程」拉起，孙进程拿不到分离标志，
    #   父命令一结束整棵树就被回收，表现为「启动成功但下一个命令连不上」。
    #
    # ★★ 但光有分离标志还不够：在受管沙箱里，命令的整个进程树被
    #    Windows Job Object 包住，且该 job 不允许 CREATE_BREAKAWAY_FROM_JOB
    #   （实测 Popen 带该标志返回 WinError 5 拒绝访问）。命令一结束 job 关闭，
    #   成员进程被连坐 kill —— 日志里表现为「ready to accept connections」
    #   之后没有任何 shutdown 记录，进程凭空消失。
    #   ⇒ 本机启动基础设施必须走 `infra.py serve`（后台常驻任务）。
    #
    # ★★★ 而且必须用 spawn_hidden()（CREATE_NO_WINDOW），
    #   绝不能用 DETACHED_PROCESS —— 那会让 PG 自己 AllocConsole 弹 4 个窗口。
    #   详见 spawn_hidden() 的 docstring。
    p = spawn_hidden([pg_bin("postgres"), "-D", win(PGDATA), "-p", str(PG_PORT)],
                     cwd=PGSQL / "bin", outfile=LOGS / "postgres.log")
    # 非正常关闭后 PG 要对整个数据目录做 fsync 恢复，实测每个路径约 10s，
    # 冷启动可能 40s 以上，所以超时给足 150s。
    wait_port(PG_PORT, "PostgreSQL", timeout=150)
    _STARTED["postgres"] = p.pid


def pg_stop() -> None:
    """停止本项目数据目录下的 PostgreSQL。

    ★ 这里曾经用 `taskkill /F /IM postgres.exe` 兜底，是个严重缺陷：
      /IM 按镜像名杀全局，会把**别人的** PostgreSQL 一起杀掉。实测三个
      serve 进程并存时互相杀对方的 PG，服务反复掉线，日志里出现
      0xC000013A（STATUS_CONTROL_C_EXIT），排查方向被完全带偏。
      pg_ctl 只认 -D 指定的 PGDATA 里的 postmaster.pid，天然是精确的；
      兜底也改成按端口查 PID，不再按镜像名。
    """
    if not (PGDATA / "PG_VERSION").exists():
        return
    if not port_open(PG_PORT):
        log("PostgreSQL 未在运行")
        return
    # 用 pg_ctl 发 fast stop 信号（走 postmaster.pid，只影响本数据目录）
    run_detached([pg_bin("pg_ctl"), "-D", win(PGDATA), "-m", "fast", "stop"])
    for _ in range(40):          # 最多等 20s 让它体面退出
        if not port_open(PG_PORT):
            log("PostgreSQL 已停止")
            return
        time.sleep(0.5)
    # 兜底：按端口找 PID 强杀（绝不用 /IM）
    pid = pid_on_port(PG_PORT)
    if pid:
        log(f"PostgreSQL 未响应停止信号，强制终止 PID {pid}")
        kill_pid(pid)
    log("PostgreSQL 已停止")


def psql(sql: str, dbname: str = "postgres") -> subprocess.CompletedProcess:
    return run([pg_bin("psql"), "-h", "127.0.0.1", "-p", str(PG_PORT), "-U", SUPERUSER,
                "-d", dbname, "-v", "ON_ERROR_STOP=1", "-c", sql], env=pg_env())


def pg_provision() -> None:
    """建角色、建库、装扩展。"""
    r = psql(f"SELECT 1 FROM pg_roles WHERE rolname='{DB_USER}'")
    if "1 row" in (r.stdout or "") or "(1 row)" in (r.stdout or ""):
        psql(f"ALTER ROLE {DB_USER} WITH LOGIN PASSWORD '{DB_PASSWORD}'")
    else:
        psql(f"CREATE ROLE {DB_USER} WITH LOGIN PASSWORD '{DB_PASSWORD}'")
    r = psql(f"SELECT 1 FROM pg_database WHERE datname='{DB_NAME}'")
    if "(1 row)" not in (r.stdout or ""):
        psql(f'CREATE DATABASE {DB_NAME} OWNER {DB_USER} ENCODING \'UTF8\'')
    for stmt in ("CREATE EXTENSION IF NOT EXISTS vector",
                 "CREATE EXTENSION IF NOT EXISTS pg_trgm"):
        r = psql(stmt, dbname=DB_NAME)
        if r.returncode != 0:
            raise SystemExit(f"扩展安装失败 ({stmt}):\n" + (r.stdout or "") + (r.stderr or ""))
    psql(f"GRANT ALL ON SCHEMA public TO {DB_USER}", dbname=DB_NAME)
    # 验证 pgvector 真的可用
    r = psql("SELECT extversion FROM pg_extension WHERE extname='vector'", dbname=DB_NAME)
    ver = [l.strip() for l in (r.stdout or "").splitlines() if l.strip() and l.strip()[0].isdigit()]
    log(f"数据库 {DB_NAME} 就绪，pgvector 版本 = {ver[0] if ver else '未知'}")


# --------------------------------------------------------------------------- #
# Redis
# --------------------------------------------------------------------------- #
def redis_start() -> None:
    if port_open(REDIS_PORT):
        log(f"Redis 已在 {REDIS_PORT} 运行")
        return
    exe = REDIS / "redis-server.exe"
    if not exe.exists():
        raise SystemExit(f"找不到 {exe}")
    conf = REDIS / "redis.conf"
    args = [win(exe), win(conf), "--port", str(REDIS_PORT),
            "--dir", win(DATA / "redis"), "--save", "", "--appendonly", "no"]
    (DATA / "redis").mkdir(parents=True, exist_ok=True)
    p = spawn_hidden(args, cwd=REDIS, outfile=LOGS / "redis.log")
    wait_port(REDIS_PORT, "Redis")
    _STARTED["redis"] = p.pid


def redis_stop() -> None:
    exe = REDIS / "redis-cli.exe"
    if exe.exists() and port_open(REDIS_PORT):
        run([win(exe), "-p", str(REDIS_PORT), "shutdown", "nosave"])
        time.sleep(0.5)
    log("Redis 已停止")


# --------------------------------------------------------------------------- #
# MinIO
# --------------------------------------------------------------------------- #
def minio_start() -> None:
    if port_open(MINIO_PORT):
        log(f"MinIO 已在 {MINIO_PORT} 运行")
        return
    exe = MINIO_DIR / "minio.exe"
    if not exe.exists():
        raise SystemExit(f"找不到 {exe}")
    env = os.environ.copy()
    env["MINIO_ROOT_USER"] = MINIO_ROOT_USER
    env["MINIO_ROOT_PASSWORD"] = MINIO_ROOT_PASSWORD
    data_dir = DATA / "minio"
    data_dir.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    # ★ 必须显式绑 127.0.0.1：只写 `:9000` 会绑到 0.0.0.0，
    #   等于把 MinIO 连同已知的默认口令暴露给整个局域网。
    # ★ 启动方式统一走 spawn_hidden()：原来这里手写
    #   CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS，和 pg_start 是同一个坑。
    p = spawn_hidden([win(exe), "server", win(data_dir),
                      "--address", f"127.0.0.1:{MINIO_PORT}",
                      "--console-address", f"127.0.0.1:{MINIO_CONSOLE_PORT}"],
                     cwd=MINIO_DIR, outfile=LOGS / "minio.log", env=env)
    wait_port(MINIO_PORT, "MinIO")
    _STARTED["minio"] = p.pid


def minio_stop() -> None:
    """按 PID 停止 MinIO。

    ★ 原来写的是 `taskkill /F /IM minio.exe` —— 同上，按镜像名杀全局。
      改成先看本进程记录的 PID，再用端口反查兜底。
    """
    pid = _STARTED.get("minio")
    if not pid or not pid_alive(pid):
        pid = pid_on_port(MINIO_PORT)
    if not pid:
        log("MinIO 未在运行")
        return
    kill_pid(pid)
    log("MinIO 已停止")


# --------------------------------------------------------------------------- #
def app_start() -> None:
    """启动后端 API（uvicorn）。

    ★ 为什么后端也交给这里管，而不是让用户自己开一个终端跑：
      「项目起来了没有、界面在哪」本身就是最容易卡住人的一步 ——
      前端界面**不是独立服务**，它是后端用 StaticFiles 托管的，
      所以只要后端活着，界面就在 http://127.0.0.1:8001/app/。
      把它和基础设施放进同一条命令，用户跑一次就能拿到地址。
    """
    if port_open(APP_PORT):
        log(f"后端已在运行（端口 {APP_PORT}）")
        return
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        raise SystemExit(
            f"找不到虚拟环境解释器：{py}\n"
            f"先建环境：python -m venv .venv\n"
            f"再装依赖：.venv/Scripts/pip install -r backend/requirements.txt")
    p = spawn_hidden(
        [str(py), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(APP_PORT)],
        ROOT / "backend", LOGS / "app.log")
    _STARTED["app"] = p.pid
    wait_port(APP_PORT, "后端 API")


def app_stop() -> None:
    pid = _STARTED.get("app") or pid_on_port(APP_PORT)
    if pid:
        kill_pid(pid)


def serve_running() -> bool:
    """`serve` 是否正在托管本项目的服务。（SERVE_LOCK 在文件后半段定义，
    函数体内的引用在调用时才解析，所以这里可以先用。）"""
    if not SERVE_LOCK.exists():
        return False
    try:
        return pid_alive(int(SERVE_LOCK.read_text(encoding="ascii").strip()))
    except (ValueError, OSError):
        return False


def app_restart() -> None:
    """重启后端，让 `backend/.env` 的改动生效。

    ★ 为什么要专门给一条命令：改完 `.env` 必须重启后端才生效，而这里
      最容易踩的坑是「以为改了、其实跑的还是旧配置」—— 页面看起来一切正常，
      只是新填的 Key 没起作用，然后你会去怀疑 Key 是不是填错了。
    """
    pid = _STARTED.get("app") or pid_on_port(APP_PORT)
    if pid:
        log(f"停止后端（PID {pid}）...")
        kill_pid(pid)
        for _ in range(20):
            if not port_open(APP_PORT):
                break
            time.sleep(0.3)
    # ★ serve 在托管时**不要**自己起。原因：本进程起的孩子会跟着本命令的
    #   job object 一起被回收，只有起在 serve 树下的才能长期存活。
    #   交给看门狗反而更快也更稳。
    if serve_running():
        log("serve 正在托管，看门狗会在 15 秒内用新配置自动重启后端")
        log("稍等片刻后执行 `python scripts/infra.py status` 确认")
        return
    app_start()


def wait_port(port: int, name: str, timeout: int = 40) -> None:
    for _ in range(timeout * 2):
        if port_open(port):
            log(f"{name} 已就绪，端口 {port}")
            return
        time.sleep(0.5)
    raise SystemExit(f"{name} 在 {timeout}s 内未监听 {port}，请查看 _infra/logs/")


def cmd_init() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    pg_initdb()
    pg_install_pgvector()
    pg_start()
    pg_provision()
    log("init 完成")


def visible_console_windows() -> list[str]:
    """当前会话里**可见的**控制台类顶层窗口（描述字符串）。

    ★ 给「启动后自检」用：本项目的服务必须一个窗口都不弹。
      返回非空就说明启动方式被改坏了 —— 最典型的是改回
      DETACHED_PROCESS，PG 会自己 AllocConsole 弹 4 个窗口。
      有了这个自检，那种回归会在日志里第一行就暴露，而不是等用户
      发现桌面上一直在弹窗。

    判据不限定宿主进程：Windows 11 默认终端是 Windows Terminal，
    PG 自建的控制台挂在 CASCADIA_HOSTING_WINDOW_CLASS 上，
    只查 conhost 会漏掉（我第一版探针就是这么漏的）。
    """
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",
                   "PseudoConsoleWindow"}
        found: list[str] = []

        def _cb(hwnd, _lp):  # noqa: ANN001
            if not u32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(hwnd, cls, 256)
            if cls.value not in classes:
                return True
            pid = wintypes.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(hwnd, title, 512)
            found.append(f"pid={pid.value} {cls.value} [{title.value}]")
            return True

        u32.EnumWindows(ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_cb), 0)
        return found
    except (OSError, AttributeError):
        return []


def cmd_up() -> None:
    for p in (DATA, LOGS):
        p.mkdir(parents=True, exist_ok=True)
    pg_start()
    redis_start()
    minio_start()
    app_start()
    cmd_status()
    # ★ 启动后自检：本项目的服务**不应该**产生任何可见控制台窗口。
    #   不这么做的话，「弹窗」这类回归没有任何自动化手段能发现 ——
    #   它不报错、不写日志、测试也测不到，只能等用户抱怨。
    ws = visible_console_windows()
    if ws:
        log("★★ 自检失败：检测到可见的控制台窗口，本项目不应弹窗 ——")
        for d in ws:
            log("     " + d)
        log("   大概率是某个 spawn 用回了 DETACHED_PROCESS。"
            "见 scripts/_probe_console_flags.py")
    else:
        log("自检通过：未产生任何可见控制台窗口")


# --------------------------------------------------------------------------- #
# 单实例锁
# --------------------------------------------------------------------------- #
SERVE_LOCK = LOGS / "serve.lock"


def acquire_serve_lock() -> None:
    """确保同一时间只有一个 serve 进程在托管本项目的服务。

    ★ 为什么必须有这把锁（这是本文件最严重缺陷的根源）：
      曾经同时跑着三个 serve 进程。每个 serve 退出时都会执行 cmd_down，
      而当时的 cmd_down 用 `taskkill /F /IM postgres.exe` 按镜像名杀全局 ——
      于是任何一个 serve 退出，都会把**另外两个正在托管**的 PG 一起杀掉。
      症状：服务反复掉线、日志里出现 0xC000013A、看门狗不停重启，
      排查方向被完全带偏，最后表现为桌面上不停弹命令行窗口。

      现在的 cmd_down 已经改成精确停止，但这把锁仍然要保留 ——
      多个 serve 并存本身就是错的，早失败比晚失败好。
    """
    if SERVE_LOCK.exists():
        try:
            old = int(SERVE_LOCK.read_text(encoding="ascii").strip())
        except (ValueError, OSError):
            old = 0
        if old and pid_alive(old):
            raise SystemExit(
                f"已有 serve 进程在运行（PID {old}），拒绝重复启动。\n"
                f"本项目的服务只能由一个进程托管。\n"
                f"若确认它已经死了，删除 {SERVE_LOCK} 后重试。")
        log(f"发现陈旧的锁文件（PID {old} 已不存在），覆盖之")
        SERVE_LOCK.unlink(missing_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    SERVE_LOCK.write_text(str(os.getpid()), encoding="ascii")


def release_serve_lock() -> None:
    try:
        SERVE_LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def cmd_serve() -> None:
    """启动全部服务并常驻不退出。

    ★ 这是「由 AI 助手代管的本机环境」里唯一可靠的启动方式。

    实测结论（scripts/_probe_survive.py 可复现）：
      宿主会给每条命令的整个进程树套一个 Windows Job Object，命令结束即
      关闭 job 并连坐 kill 所有成员。以下手段**全部无效**：
        - DETACHED_PROCESS
        - CREATE_NEW_PROCESS_GROUP
        - CREATE_BREAKAWAY_FROM_JOB（返回 WinError 5，job 不允许 breakaway）
        - 绕过沙箱执行（只解除文件/网络策略，不解除进程回收）
      唯一有效的是让进程树挂在一个**后台常驻任务**下 —— 只要该任务存活，
      子进程就存活。

    因此本机启动基础设施的正确姿势是：
        python scripts/infra.py serve        # 用后台常驻方式运行
    而用户在自己的终端里跑 `up` 是不受影响的（普通终端没有这层 job）。
    """
    import signal

    acquire_serve_lock()
    try:
        cmd_up()
    except BaseException:
        release_serve_lock()
        raise
    log("基础设施已全部就绪，本进程常驻以维持服务存活")
    log("（结束本任务即停止全部服务；用户自己的终端可直接用 `up`）")

    stopping = {"flag": False}

    def _on_signal(signum, frame):  # noqa: ANN001
        stopping["flag"] = True
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            pass

    # ------------------------------------------------------------------ #
    # 看门狗
    # ------------------------------------------------------------------ #
    # ★ 为什么需要它：服务会被外部原因干掉，而且**不是一起死**。
    #   实测 2026-09-24 18:02 PG 日志：
    #       server process (PID 40432) was terminated by exception 0xC000013A
    #       DETAIL: Failed process was running: ROLLBACK
    #       ... startup process (PID 28180) was terminated by exception 0xC0000142
    #       ... aborting startup due to startup process failure
    #       ... database system is shut down
    #   0xC000013A = STATUS_CONTROL_C_EXIT（收到 Ctrl+C / 控制台关闭信号），
    #   0xC0000142 = STATUS_DLL_INIT_FAILED（崩溃恢复进程自己都没起来）。
    #   同一时刻 Redis(6379) 与 MinIO(9000) 毫发无损。
    #   ⇒ 没有看门狗的话，环境会在几小时后「悄悄少一个服务」，
    #     业务层表现成莫名的连接失败，排查成本很高。这里让它自愈。
    WATCH_EVERY = 15  # 秒
    MAX_TRIES = 5     # 连续失败多少次后熔断
    # ★ 这个列表必须和 cmd_up() 里的启动项**保持一致**。
    #   漏掉任何一项，那个服务就只在启动时起来一次、掉线后再也不回来。
    #   （我第一版就漏了「后端 API」：cmd_up 里有、这里没有，
    #     结果后端一挂就永远挂着，而基础设施却会自愈 —— 这种「一半会自愈」
    #     的状态比全都不自愈更难排查。）
    services = [
        ("PostgreSQL", PG_PORT, pg_start),
        ("Redis", REDIS_PORT, redis_start),
        ("MinIO", MINIO_PORT, minio_start),
        ("后端 API", APP_PORT, app_start),
    ]

    # ★ 每个服务一份退避状态。为什么必须有：
    #   如果某个服务起不来（数据目录损坏、端口被占、二进制丢了），
    #   没有退避的看门狗会**每 15 秒重启一次、永远不停**。
    #   每次重启都 spawn 一个进程 —— 这不只是刷日志，
    #   在 Windows 上意味着持续的资源消耗和进程堆积。
    #   连续失败到上限后熔断，宁可让服务保持「明确地挂着」，
    #   也不要制造一个用户无法使用电脑的循环。
    wd = {name: {"fails": 0, "next": 0.0} for name, _, _ in services}

    def watch_one(name: str, port: int, starter) -> None:
        st = wd[name]
        if port_open(port):
            if st["fails"]:
                log(f"[看门狗] {name} 已恢复（此前连续失败 {st['fails']} 次）")
            st["fails"] = 0
            st["next"] = 0.0
            return
        if st["fails"] >= MAX_TRIES:
            return                      # 已熔断，静默等待人工介入
        if time.monotonic() < st["next"]:
            return                      # 还在退避窗口里
        st["fails"] += 1
        log(f"[看门狗] {name} 掉线（{port} 无监听），"
            f"第 {st['fails']}/{MAX_TRIES} 次重启 ...")
        try:
            starter()
            log(f"[看门狗] {name} 已恢复")
            st["fails"] = 0
            st["next"] = 0.0
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            delay = min(15 * (2 ** (st["fails"] - 1)), 240)
            st["next"] = time.monotonic() + delay
            log(f"[看门狗] {name} 重启失败：{exc}")
            if st["fails"] >= MAX_TRIES:
                log(f"[看门狗] ★★ {name} 连续 {MAX_TRIES} 次重启失败，已熔断，"
                    f"不再自动重试。请手动排查后执行："
                    f"python scripts/infra.py up")
            else:
                log(f"[看门狗] {delay}s 后再试")

    tick = 0
    try:
        while not stopping["flag"]:
            time.sleep(1)
            tick += 1
            if tick % WATCH_EVERY:
                continue
            for name, port, starter in services:
                if stopping["flag"]:
                    break
                watch_one(name, port, starter)
    except KeyboardInterrupt:
        pass
    finally:
        log("收到退出信号，停止本进程启动过的服务 ...")
        try:
            cmd_down(only_own=True)      # ★ 只停自己启动过的
        finally:
            release_serve_lock()


def cmd_down(only_own: bool = False) -> None:
    """停止服务。

    only_own=True（serve 退出时用）只停**本进程启动过**的服务。
    ★ 这条规则防的是一类真实事故：用户自己开着的 PG，被我们的 serve
      退出时顺手杀掉。不是我们拉起来的，就不该由我们停掉。
      显式执行 `infra.py down` 时才停全部 —— 此时三个 stop 都是
      按 PGDATA / 端口精确定位的，不会误伤别人。
    """
    if only_own:
        for name, stop in (("postgres", pg_stop), ("redis", redis_stop),
                           ("minio", minio_stop), ("app", app_stop)):
            if name in _STARTED:
                stop()
        return
    pg_stop()
    redis_stop()
    minio_stop()
    app_stop()


def cmd_status() -> None:
    rows = [("PostgreSQL", PG_PORT), ("Redis", REDIS_PORT),
            ("MinIO", MINIO_PORT), ("后端 API", APP_PORT)]
    print()
    for name, port in rows:
        ok = port_open(port)
        print(f"  {'OK  ' if ok else 'DOWN'}  {name:<12} :{port}")
    print(f"\n  MinIO 控制台  http://127.0.0.1:{MINIO_CONSOLE_PORT}  "
          f"({MINIO_ROOT_USER} / {MINIO_ROOT_PASSWORD})")
    # ★ 把访问地址印在状态里。前端是后端托管的静态目录，不是独立服务 ——
    #   不写清楚的话，很容易误以为要另外起一个前端服务器。
    print(f"  ★ 前端界面    http://127.0.0.1:{APP_PORT}/app/     "
          f"（演示账号 demo / demo1234）")
    print(f"    API 文档    http://127.0.0.1:{APP_PORT}/docs")
    print(f"    入口索引    http://127.0.0.1:{APP_PORT}/")
    print()


def cmd_psql() -> None:
    env = pg_env()
    subprocess.run([pg_bin("psql"), "-h", "127.0.0.1", "-p", str(PG_PORT),
                    "-U", SUPERUSER, "-d", DB_NAME], env=env)


def main() -> None:
    ap = argparse.ArgumentParser(description="agri-pest-agent 基础设施管理器")
    ap.add_argument("action", choices=["init", "up", "down", "status", "psql",
                                       "serve", "pg", "pgvector-only", "app",
                                       "restart-app"])
    a = ap.parse_args()
    if a.action == "init":
        cmd_init()
    elif a.action == "up":
        cmd_up()
    elif a.action == "serve":
        cmd_serve()
    elif a.action == "app":
        # 只起后端。基础设施已经在跑的时候用它，不必重跑一遍 up。
        app_start()
        cmd_status()
    elif a.action == "restart-app":
        # 改完 backend/.env 之后用这条让新配置生效。
        app_restart()
    elif a.action == "pg":
        # 只拉起 PostgreSQL。单独给一个子命令是因为它是最容易掉的一个，
        # 而 up 会连带去起 Redis/MinIO（虽然是幂等的，但没必要）。
        pg_start()
        cmd_status()
    elif a.action == "down":
        cmd_down()
    elif a.action == "status":
        cmd_status()
    elif a.action == "psql":
        cmd_psql()
    elif a.action == "pgvector-only":
        pg_install_pgvector()


if __name__ == "__main__":
    main()
