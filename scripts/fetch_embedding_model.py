#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""下载本地 Embedding 模型到项目内 models/ 目录。

为什么要自己下、而不是让 sentence-transformers 自动下：
  1. 国内直连 huggingface.co 不通，需要走 hf-mirror.com；
  2. sentence-transformers 默认把模型塞进用户级 HF 缓存
     （C:/Users/<user>/.cache/huggingface），项目换台机器就得重下；
  3. 受管环境里长命令会被提升为「无沙箱」执行，而该模式下到
     127.0.0.1 代理的连接不可用，huggingface_hub 会直接报
     LocalEntryNotFoundError —— 自己下可以完全绕开这个不确定性。

下载后模型落在 <项目根>/models/<repo_id 里的 / 换成 __>/，
app/services/rag/embedder.py 会自动优先使用该目录。

用法：
    python scripts/fetch_embedding_model.py
    python scripts/fetch_embedding_model.py --repo BAAI/bge-base-zh-v1.5
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"

DEFAULT_REPO = "BAAI/bge-small-zh-v1.5"
DEFAULT_ENDPOINT = "https://hf-mirror.com"

# 不需要的权重格式与说明文件；safetensors 优先于 pytorch_model.bin
SKIP_SUFFIXES = (".gitattributes", "README.md", "pytorch_model.bin")


def local_dir_for(repo: str) -> Path:
    return MODELS_DIR / repo.replace("/", "__")


def log(msg: str) -> None:
    print(f"[model] {msg}", flush=True)


def list_files(session, endpoint: str, repo: str) -> list[str]:
    url = f"{endpoint}/api/models/{repo}"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    siblings = r.json().get("siblings", [])
    return [s["rfilename"] for s in siblings]


def download_file(session, endpoint: str, repo: str, name: str, dest: Path) -> None:
    url = f"{endpoint}/{repo}/resolve/main/{name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    # 断点续传：如果已有 .part，带上 Range 头继续
    headers = {}
    mode = "wb"
    if tmp.exists() and tmp.stat().st_size > 0:
        headers["Range"] = f"bytes={tmp.stat().st_size}-"
        mode = "ab"

    with session.get(url, stream=True, timeout=120, headers=headers) as r:
        if r.status_code == 416:            # 已下完
            tmp.replace(dest)
            return
        if r.status_code not in (200, 206):
            raise RuntimeError(f"HTTP {r.status_code} 下载 {name} 失败")
        total = int(r.headers.get("Content-Length", 0))
        done = tmp.stat().st_size if mode == "ab" else 0
        t0 = time.time()
        with open(tmp, mode) as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 / total
                    speed = done / max(time.time() - t0, 0.01) / 1024
                    print(f"\r    {name}  {pct:5.1f}%  "
                          f"{done/1048576:.1f}/{total/1048576:.1f} MB  "
                          f"{speed:.0f} KB/s", end="", flush=True)
        print()
    tmp.replace(dest)


def main() -> None:
    ap = argparse.ArgumentParser(description="下载本地 Embedding 模型")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--endpoint", default=os.environ.get("HF_ENDPOINT",
                                                         DEFAULT_ENDPOINT))
    a = ap.parse_args()

    try:
        import requests
    except ImportError:
        sys.exit("需要 requests：pip install requests")

    session = requests.Session()
    session.headers["User-Agent"] = "agri-pest-agent/1.0"

    dest_dir = local_dir_for(a.repo)
    log(f"仓库   : {a.repo}")
    log(f"镜像   : {a.endpoint}")
    log(f"目标   : {dest_dir}")

    files = list_files(session, a.endpoint, a.repo)
    wanted = [f for f in files
              if not any(f.endswith(s) or f == s for s in SKIP_SUFFIXES)]
    log(f"待下载 : {len(wanted)} 个文件")

    failed: list[str] = []
    for name in wanted:
        dest = dest_dir / name
        if dest.exists() and dest.stat().st_size > 0:
            log(f"  跳过（已存在） {name}  {dest.stat().st_size/1024:.0f} KB")
            continue
        try:
            download_file(session, a.endpoint, a.repo, name, dest)
            log(f"  完成 {name}  {dest.stat().st_size/1024:.0f} KB")
        except Exception as exc:  # noqa: BLE001
            log(f"  失败 {name}: {type(exc).__name__}: {exc}")
            failed.append(name)

    if failed:
        log(f"有 {len(failed)} 个文件失败：{failed}")
        sys.exit(1)

    total = sum(f.stat().st_size for f in dest_dir.rglob("*") if f.is_file())
    log(f"全部完成，共 {total/1048576:.1f} MB")
    log(f"把 backend/.env 里的 EMBEDDING_MODEL 保持为 {a.repo} 即可，"
        f"embedder 会自动优先使用本地目录")


if __name__ == "__main__":
    main()
