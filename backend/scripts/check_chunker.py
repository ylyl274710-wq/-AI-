#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""切块器自测：chunk_text 的不变量与结构切分行为。

★ 为什么需要它：切块器是检索质量的源头，而它的回归表现很隐蔽 ——
  「切错了」不报错，只是召回变差。这里把每条不变量写成断言：
    - 短文本原样返回，不多不少；
    - 无标题长文本：块数 >1、每块 ≤ size、相邻块有 overlap、不在句中硬断；
    - Markdown 文档：按节切分，每节若干块、每块带节标题前缀；
    - 单段超长且无标点：走硬切分支，仍然 ≤ size。
  改 chunk_text 或调 rag_chunk_size 之后必须跑一遍。

用法：
    python backend/scripts/check_chunker.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.services.rag.indexer import chunk_text  # noqa: E402

SIZE = 420
OVERLAP = 60

checks: list[bool] = []


def check(name: str, ok: bool, extra: str = "") -> None:
    checks.append(ok)
    print(f"  [{'OK  ' if ok else '失败'}] {name}" + (f"  {extra}" if extra else ""))


# ---- 1. 短文本：原样返回 ----
short = "黄瓜霜霉病主要危害叶片。"
out = chunk_text(short, size=SIZE, overlap=OVERLAP)
check("短文本返回 1 块", len(out) == 1)
check("短文本内容不变", out[0] == short)

# ---- 2. 空文本 ----
check("空文本返回空", chunk_text("") == [] and chunk_text("  \n  ") == [])

# ---- 3. 无标题长文本：多块、限长、有 overlap ----
long_plain = ("黄瓜霜霉病是设施黄瓜的第一大病害。" * 30)   # ~480 字，无换行
out = chunk_text(long_plain, size=SIZE, overlap=OVERLAP)
check("无标题长文本切成多块", len(out) >= 2, f"共 {len(out)} 块")
check("每块都不超过 size", all(len(c) <= SIZE for c in out))
if len(out) >= 2:
    shared = out[0][-OVERLAP:]
    check("相邻块带 overlap（尾部出现在下一块头部）", out[1].startswith(shared) or shared in out[1])

# ---- 4. 句边界：段落不超过 size 时不在句中切断 ----
para_text = "第一句讲症状表现。" + "第二句讲发生条件。" + "第三句讲防治方法。"
out = chunk_text(para_text, size=SIZE, overlap=OVERLAP)
check("短段不切碎句子", out == [para_text])

# ---- 5. Markdown 标题切分 ----
md = ("## 症状表现\n" + "危害叶片，出现多角形病斑。" * 20 + "\n\n"
      + "## 发生条件\n" + "高温高湿利于发病。" * 20 + "\n\n"
      + "## 防治方法\n" + "发病前喷保护性杀菌剂。" * 20)
out = chunk_text(md, size=SIZE, overlap=OVERLAP)
check("Markdown 按节切成 3 块", len(out) == 3, f"实际 {len(out)} 块")
check("每块 ≤ size", all(len(c) <= SIZE for c in out))
check("每块带节标题前缀", all(c.startswith(("症状表现", "发生条件", "防治方法")) for c in out))
check("第二块不含第一节的症状内容", all("多角形病斑" not in c for c in out[1:]),
      "节与节之间不应串内容")

# ---- 6. 单节超长：节内继续切块，标题仍带到每块 ----
long_md = ("## 防治方法\n" + "轮作倒茬并加强田间管理。" * 60)
out = chunk_text(long_md, size=SIZE, overlap=OVERLAP)
check("超长节切成多块", len(out) >= 2, f"共 {len(out)} 块")
check("超长节每块 ≤ size", all(len(c) <= SIZE for c in out))
check("超长节每块都带标题", all(c.startswith("防治方法") for c in out))

# ---- 7. 无标点超长段：硬切分支 ----
hard = "黄瓜" * 400                      # 800 字无标点
out = chunk_text(hard, size=SIZE, overlap=OVERLAP)
check("无标点超长段走硬切且限长", len(out) >= 2 and all(len(c) <= SIZE for c in out))

# ---- 8. 旧语料回归：无标题短文（现有 36 篇的形态）不受影响 ----
old_style = "叶片发黄是最常见的咨询问题，原因至少有以下几类。" * 1
check("现有短文形态不受影响", chunk_text(old_style, size=SIZE, overlap=OVERLAP) == [old_style])

print()
if all(checks):
    print(f"全部 {len(checks)} 项通过 —— 切块器不变量成立")
    sys.exit(0)
print(f"{checks.count(False)} / {len(checks)} 项失败")
sys.exit(1)
