"""文本向量化。

三级策略，保证任何环境下都能跑：
  1. provider=openai  → 走 OpenAI 兼容 /embeddings 接口
  2. provider=local   → 本地 sentence-transformers（默认 BAAI/bge-small-zh-v1.5）
  3. 两者都不可用     → 字符 n-gram 哈希向量（确定性、无需模型、纯 CPU）

★ 第 3 级不是玩具：它本质是「把文本映射成带位置信息的词袋向量」，
  配合 BM25 关键词检索一起用时，中文短文本的召回并不难看。
  它的意义是让「没网/没显存/模型下载失败」时系统依然可演示、可回归测试。
"""
from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Iterable

import numpy as np

from app.core.config import settings
from app.services.llm import LLMUnavailable, llm

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_st_model = None
_st_failed = False


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def resolve_model_path() -> str:
    """把配置里的模型名解析成实际可加载的路径。

    优先顺序：
      1. EMBEDDING_MODEL 直接就是一个存在的目录 → 用它
      2. <项目根>/models/<repo_id 的 / 换成 __> 存在且有文件 → 用它
      3. 原样返回（交给 sentence-transformers 去联网下载）

    为什么要有第 2 步：国内直连 huggingface.co 不通，sentence-transformers
    默认又会把模型塞进用户级缓存目录（换台机器就得重下）。项目内放一份，
    模型跟项目走，行为可预期。下载见 scripts/fetch_embedding_model.py。
    """
    name = (settings.embedding_model or "").strip()
    if not name:
        return name
    direct = Path(name)
    if direct.is_dir():
        return str(direct)
    local = Path(settings.model_dir) / name.replace("/", "__")
    if local.is_dir() and any(local.iterdir()):
        return str(local)
    return name


def hashing_embed(texts: list[str], dim: int) -> list[list[float]]:
    """字符 n-gram 哈希向量。

    中文没有天然空格分词，所以用 1~3 元字符 n-gram 做特征，
    再按 hash 落桶。加 n-gram 是为了保留一点局部词序信息。
    """
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, text in enumerate(texts):
        s = (text or "").strip()
        if not s:
            continue
        grams: list[str] = []
        for n in (1, 2, 3):
            grams.extend(s[j:j + n] for j in range(len(s) - n + 1))
        for g in grams:
            h = hashlib.md5(g.encode("utf-8")).digest()
            idx = int.from_bytes(h[:4], "little") % dim
            sign = 1.0 if h[4] & 1 else -1.0   # 带符号哈希，减少碰撞干扰
            out[i, idx] += sign
    return _l2_normalize(out).tolist()


def _load_st_model():
    global _st_model, _st_failed
    if _st_model is not None or _st_failed:
        return _st_model
    with _lock:
        if _st_model is not None or _st_failed:
            return _st_model
        try:
            from sentence_transformers import SentenceTransformer
            path = resolve_model_path()
            logger.info("加载本地 Embedding 模型: %s", path)
            _st_model = SentenceTransformer(path, device="cpu")
            logger.info("Embedding 模型就绪, dim=%d",
                        _st_model.get_sentence_embedding_dimension())
        except Exception as exc:  # noqa: BLE001
            _st_failed = True
            logger.warning("本地 Embedding 模型不可用，降级为哈希向量: %s", exc)
    return _st_model


class Embedder:
    """统一入口。维度必须与 settings.embedding_dim 一致，否则写库会报错。"""

    @property
    def dim(self) -> int:
        return settings.embedding_dim

    @property
    def mode(self) -> str:
        if settings.embedding_is_api:
            return "openai"
        return "local" if _load_st_model() is not None else "hashing"

    def embed_sync(self, texts: Iterable[str]) -> list[list[float]]:
        items = [t or "" for t in texts]
        if not items:
            return []
        if settings.embedding_is_api:
            # 同步入口不支持异步 API，交由 embed_async 处理
            raise RuntimeError("EMBEDDING_PROVIDER=openai 时请使用 embed_async")
        model = _load_st_model()
        if model is None:
            return hashing_embed(items, self.dim)
        vecs = model.encode(items, normalize_embeddings=True,
                            batch_size=32, show_progress_bar=False)
        arr = np.asarray(vecs, dtype=np.float32)
        if arr.shape[1] != self.dim:
            logger.warning("模型维度 %d 与配置 EMBEDDING_DIM=%d 不一致，"
                           "请修改配置并重建索引", arr.shape[1], self.dim)
        return arr.tolist()

    async def embed_async(self, texts: Iterable[str]) -> list[list[float]]:
        items = [t or "" for t in texts]
        if not items:
            return []
        if settings.embedding_is_api:
            try:
                return await llm.embed(items)
            except LLMUnavailable as exc:
                logger.warning("Embedding 接口不可用，降级为哈希向量: %s", exc)
                return hashing_embed(items, self.dim)
        return self.embed_sync(items)


embedder = Embedder()
