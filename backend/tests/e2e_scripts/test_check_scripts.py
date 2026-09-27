"""把 backend/scripts/ 下的自检脚本接进 pytest。

两层分工：
  - unit/ 直接 import 纯函数测逻辑（CI 可跑，无基础设施）；
  - 这里以子进程跑整条自检脚本（含数据库 / 后端服务的全链路脚本），
    资源不在就 skip —— 「跳过」必须显式报出来，不能让测试静默变少。

脚本的判据与 unit/ 测试刻意重叠一部分（如 check_chunker）：
双入口防「改了脚本忘了改测试」或反之。
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPTS = BACKEND / "scripts"
REPO = BACKEND.parent
LEAF = REPO / "_infra" / "logs" / "leaf_test.jpg"


def _run(script: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(script)], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, cwd=str(BACKEND))


def _port_open(port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def test_check_chunker_script():
    r = _run(SCRIPTS / "check_chunker.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "16 项通过" in r.stdout


@pytest.mark.e2e
@pytest.mark.skipif(not LEAF.exists(), reason="缺样张 _infra/logs/leaf_test.jpg")
def test_check_vision_wiring_script():
    r = _run(SCRIPTS / "check_vision_wiring.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "全部 8 项通过" in r.stdout


@pytest.mark.e2e
@pytest.mark.skipif(not LEAF.exists(), reason="缺样张 _infra/logs/leaf_test.jpg")
def test_check_classifier_wiring_script():
    r = _run(SCRIPTS / "check_classifier_wiring.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "全部 10 项通过" in r.stdout


@pytest.mark.e2e
@pytest.mark.skipif(not _port_open(5432), reason="PostgreSQL 未运行")
def test_check_tool_loop_script():
    r = _run(SCRIPTS / "check_tool_loop.py")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "全部 6 项通过" in r.stdout


@pytest.mark.e2e
@pytest.mark.skipif(not _port_open(5432), reason="PostgreSQL 未运行")
def test_check_retrieval_eval():
    r = _run(SCRIPTS / "check_retrieval.py", timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "hit@3" in r.stdout


@pytest.mark.e2e
@pytest.mark.skipif(not _port_open(8001), reason="后端服务未运行")
def test_smoke_http_suite():
    r = _run(SCRIPTS / "smoke_http.py", timeout=600)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "失败 0" in r.stdout
