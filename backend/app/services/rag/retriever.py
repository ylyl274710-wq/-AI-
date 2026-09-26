"""混合检索（文档难点 5 的落地实现）。

原文档写的是「向量检索 + 关键词检索 + 元数据过滤，最终采用混合检索」，
但没有说三者怎么融合。这里补上：

    向量召回 ─┐
              ├─→ RRF 融合 ─→ 元数据过滤已在召回前完成 ─→ Top-K
    关键词召回 ┘

为什么用 RRF（Reciprocal Rank Fusion）而不是加权求和：
向量分和 BM25 分的量纲完全不同，直接加权需要调一个说不清道理的系数；
RRF 只用「排名」，不需要归一化，工程上更稳、更好解释，答辩也说得清。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import jieba
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.models.knowledge import AgriculturalKnowledge, KnowledgeChunk
from app.services.rag.embedder import embedder
from app.services.rag.rewrite import RewrittenQuery

logger = logging.getLogger(__name__)

# 关键词检索的候选池上限。超过这个规模应该换成 PG 侧全文索引，
# 但在本项目的数据量级（几百到几千 chunk）下，Python 侧 BM25 更快也更准。
KEYWORD_POOL_LIMIT = 2000


@dataclass
class RetrievedChunk:
    chunk_id: int
    knowledge_id: int
    title: str = ""
    content: str = ""
    crop_name: str | None = None
    disease_name: str | None = None
    growth_stage: str | None = None
    category: str | None = None
    source_name: str | None = None
    source_url: str | None = None

    vector_score: float = 0.0
    keyword_score: float = 0.0
    fused_score: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def citation(self) -> str:
        if self.source_name:
            return f"{self.source_name}｜{self.title}"
        return self.title


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    query: RewrittenQuery
    vector_hits: int = 0
    keyword_hits: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.chunks

    @property
    def top_score(self) -> float:
        return self.chunks[0].fused_score if self.chunks else 0.0

    @property
    def score_margin(self) -> float:
        """第一名与第二名的融合分差 —— 置信度计算要用（见 services/confidence.py）。"""
        if len(self.chunks) < 2:
            return 1.0 if self.chunks else 0.0
        return self.chunks[0].fused_score - self.chunks[1].fused_score


def tokenize(text: str) -> list[str]:
    return [t.strip() for t in jieba.lcut(text or "") if t.strip()]


class HybridRetriever:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ #
    def _base_query(self, crop: str | None, growth_stage: str | None,
                    category: str | None) -> Select:
        """元数据过滤在召回前完成，而不是召回后再筛 —— 后者会把有效结果筛没。

        ★ 作物过滤必须用 agricultural_knowledge.crop_names（JSONB 数组）做
          包含判断，**不能**用 knowledge_chunks.crop_name（单值字符串）做等值
          判断。原因：一条知识常常适用于多个作物 —— 例如「黄瓜白粉病」同时
          登记在黄瓜、番茄、辣椒上。chunk 上只冗余了「主作物」用于展示，
          如果拿它做等值过滤，按「番茄」检索时这条知识会被静默排除，
          属于最难排查的一类 bug：不报错、只是召回少了。

          crop_names 为空数组代表通用植保知识，任何作物都应保留。
        """
        stmt = (select(KnowledgeChunk)
                .join(AgriculturalKnowledge,
                      KnowledgeChunk.knowledge_id == AgriculturalKnowledge.id)
                # 检索结果里要读 chunk.knowledge 取标题/来源；不预加载会对
                # 每个 chunk 触发一次懒加载查询（N+1）。selectinload 用一条
                # IN 查询批量取回。
                .options(selectinload(KnowledgeChunk.knowledge))
                .where(AgriculturalKnowledge.status == "active",
                       AgriculturalKnowledge.review_status == "approved",
                       AgriculturalKnowledge.deleted_at.is_(None)))
        if crop:
            stmt = stmt.where(or_(
                AgriculturalKnowledge.crop_names.contains([crop]),
                func.coalesce(func.jsonb_array_length(
                    AgriculturalKnowledge.crop_names), 0) == 0,
            ))
        if growth_stage:
            stmt = stmt.where(or_(KnowledgeChunk.growth_stage == growth_stage,
                                  KnowledgeChunk.growth_stage.is_(None)))
        if category:
            stmt = stmt.where(KnowledgeChunk.category == category)
        return stmt

    # ------------------------------------------------------------------ #
    def _vector_search(self, stmt: Select, query_text: str,
                       limit: int) -> list[tuple[KnowledgeChunk, float]]:
        if not query_text.strip():
            return []
        vec = embedder.embed_sync([query_text])[0]
        # cosine 距离 → 相似度：similarity = 1 - distance
        distance = KnowledgeChunk.embedding.cosine_distance(vec)
        rows = self.db.execute(
            stmt.add_columns(distance.label("dist"))
            .where(KnowledgeChunk.embedding.is_not(None))
            .order_by(distance)
            .limit(limit)
        ).all()
        return [(r[0], 1.0 - float(r[1])) for r in rows]

    # ------------------------------------------------------------------ #
    def _keyword_search(self, stmt: Select, query_text: str,
                        limit: int) -> list[tuple[KnowledgeChunk, float]]:
        q_tokens = tokenize(query_text)
        if not q_tokens:
            return []
        pool = self.db.scalars(stmt.limit(KEYWORD_POOL_LIMIT)).all()
        if not pool:
            return []

        corpus_tokens = [tokenize(c.keywords or c.content) for c in pool]
        try:
            from rank_bm25 import BM25Okapi
            bm25 = BM25Okapi(corpus_tokens)
            scores = bm25.get_scores(q_tokens)
        except Exception as exc:  # noqa: BLE001
            # rank_bm25 缺失时退化为「命中词数 / 查询词数」的简单打分
            logger.warning("BM25 不可用，改用词命中率打分: %s", exc)
            qset = set(q_tokens)
            scores = [len(qset & set(toks)) / max(len(qset), 1)
                      for toks in corpus_tokens]

        pairs = sorted(zip(pool, scores), key=lambda x: x[1], reverse=True)[:limit]
        return [(c, float(s)) for c, s in pairs if s > 0]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _rrf(rank_lists: list[tuple[list[RetrievedChunk], float]],
             k: int) -> dict[int, float]:
        """Reciprocal Rank Fusion：score = Σ weight / (k + rank)"""
        fused: dict[int, float] = {}
        for chunks, weight in rank_lists:
            for rank, ch in enumerate(chunks, start=1):
                fused[ch.chunk_id] = fused.get(ch.chunk_id, 0.0) + weight / (k + rank)
        return fused

    # ------------------------------------------------------------------ #
    def retrieve(self, query: RewrittenQuery, *, crop: str | None = None,
                 growth_stage: str | None = None, category: str | None = None,
                 top_k: int | None = None, candidate_k: int | None = None,
                 ) -> RetrievalResult:
        top_k = top_k or settings.rag_top_k
        cand_k = candidate_k or settings.rag_candidate_k
        crop = crop or query.crop
        growth_stage = growth_stage or query.growth_stage

        stmt = self._base_query(crop, growth_stage, category)

        vec_rows = self._vector_search(stmt, query.search_text, cand_k)
        kw_rows = self._keyword_search(stmt, query.keyword_text, cand_k)

        vec_chunks: list[RetrievedChunk] = []
        kw_chunks: list[RetrievedChunk] = []

        def build(chunk: KnowledgeChunk, **extra) -> RetrievedChunk:
            k = chunk.knowledge
            return RetrievedChunk(
                chunk_id=chunk.id, knowledge_id=chunk.knowledge_id,
                title=(k.title if k else "") or "",
                content=chunk.content,
                crop_name=chunk.crop_name, disease_name=chunk.disease_name,
                growth_stage=chunk.growth_stage, category=chunk.category,
                source_name=chunk.source_name or (k.source.name if k and k.source else None),
                source_url=chunk.source_url or (k.source.url if k and k.source else None),
                **extra)

        for chunk, score in vec_rows:
            vec_chunks.append(build(chunk, vector_score=round(score, 6)))
        for chunk, score in kw_rows:
            kw_chunks.append(build(chunk, keyword_score=round(score, 6)))

        by_id: dict[int, RetrievedChunk] = {}
        for lst, tag in ((vec_chunks, "vector"), (kw_chunks, "keyword")):
            for rank, ch in enumerate(lst, start=1):
                cur = by_id.setdefault(ch.chunk_id, ch)
                cur.ranks[tag] = rank
                if ch.vector_score:
                    cur.vector_score = ch.vector_score
                if ch.keyword_score:
                    cur.keyword_score = ch.keyword_score

        # ---- 第三路：标题命中 ----
        # 症状描述的两条通道都可能被同作物的近似病害稀释；但农户按病名提问
        # （「晚疫病打什么药」）或查询词正好撞上标题专业词时，文档标题是
        # 最强的语义信号。把「标题命中查询词个数」作为独立排名、以低权重
        # 并入 RRF —— 仍然只看排名，不引入新的量纲和魔法系数。
        q_tokens = set(tokenize(query.keyword_text)) | set(query.expanded_terms or [])
        title_chunks: list[RetrievedChunk] = []
        if q_tokens and by_id:
            scored = []
            for ch in by_id.values():
                n = len(q_tokens & set(tokenize(ch.title or "")))
                if n:
                    scored.append((ch.chunk_id, n))
            scored.sort(key=lambda x: x[1], reverse=True)
            title_chunks = [by_id[cid] for cid, _ in scored[:cand_k]]

        fused = self._rrf([
            (vec_chunks, settings.rag_vector_weight),
            (kw_chunks, settings.rag_keyword_weight),
            (title_chunks, 0.5),
        ], settings.rag_rrf_k)

        for cid, score in fused.items():
            if cid in by_id:
                by_id[cid].fused_score = round(score, 6)

        ranked = sorted(by_id.values(), key=lambda c: c.fused_score, reverse=True)
        ranked = [c for c in ranked if c.fused_score >= settings.rag_min_score][:top_k]

        return RetrievalResult(chunks=ranked, query=query,
                               vector_hits=len(vec_chunks),
                               keyword_hits=len(kw_chunks))
