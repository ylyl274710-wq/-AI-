"""知识切块的纯函数层（从 indexer.py 抽出）。

为什么单独一个模块：切块是纯文本逻辑，不该拖着 embedder / 数据库依赖 ——
抽出来之后 pytest 可以在无数据库、无模型文件的 CI 环境直接测
（backend/tests/unit/test_chunk_text.py）。
切块策略详见项目 README 与 docs/实施问题清单.md D32。
"""
from __future__ import annotations

import re

import jieba

from app.core.config import settings

_PARA_SPLIT = re.compile(r"\n\s*\n|\n")
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])")
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题把文档切成 (节标题, 节正文)。

    为什么先切节再切块：一篇长文档往往「症状 / 发生条件 / 防治方法」各成一节，
    按标题切能保证一个 chunk 不会横跨两节说两件事 —— 检索命中「防治」的
    chunk 就该只讲防治，而不是前半截症状后半截防治。
    没有标题的文本返回 [("", 原文)]，此时行为与旧版完全一致。
    """
    sections: list[tuple[str, str]] = []
    head, buf = "", []
    for line in text.splitlines():
        m = _HEADING_LINE.match(line)
        if m:
            if head or any(l.strip() for l in buf):
                sections.append((head, "\n".join(buf)))
            head, buf = m.group(1), []
        else:
            buf.append(line)
    sections.append((head, "\n".join(buf)))
    return [(h, b) for h, b in sections if b.strip() or h]


def chunk_text(text: str, size: int | None = None,
               overlap: int | None = None) -> list[str]:
    size = size or settings.rag_chunk_size
    overlap = overlap or settings.rag_chunk_overlap
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    for head, body in _split_sections(text):
        # 节标题冗余到该节每个 chunk：单独一个 chunk 常常看不出在说什么
        # （比如「该阶段需控制湿度」），带上了标题才知道是哪一节在讲话。
        # ★ 标题的长度要先从 size 里扣掉（预算制），而不是切完再拼 ——
        #   后者会产生超过 size 的块，这里的自测（check_chunker.py）抓到过。
        prefix = f"{head[:max(size // 4, 0)]}\n" if head else ""
        budget = size - len(prefix)

        # 1) 先切成「语义单元」：段落 → 句子
        units: list[str] = []
        for para in _PARA_SPLIT.split(body):
            para = para.strip()
            if not para:
                continue
            if len(para) <= budget:
                units.append(para)
                continue
            units.extend(s for s in _SENT_SPLIT.split(para) if s.strip())

        # 2) 贪心合并到 budget，超长单元硬切
        section_chunks: list[str] = []
        buf = ""
        for u in units:
            if len(u) > budget:
                if buf:
                    section_chunks.append(buf)
                    buf = ""
                for i in range(0, len(u), budget - overlap):
                    section_chunks.append(u[i:i + budget])
                continue
            if len(buf) + len(u) <= budget:
                buf += u
            else:
                section_chunks.append(buf)
                # 带上尾部 overlap，保证跨块语义连续
                tail = buf[-overlap:] if overlap > 0 else ""
                buf = tail + u
        if buf.strip():
            section_chunks.append(buf)

        chunks.extend(prefix + piece for piece in section_chunks)
    return [c.strip() for c in chunks if c.strip()]


def tokenize_for_index(text: str) -> str:
    return " ".join(t for t in jieba.lcut(text or "") if t.strip())
