"""知识入库与切块。

原文档只说「agricultural_knowledge：农业知识文档及元数据」，
但 RAG 真正检索的是 chunk，不是整篇文档。整篇 2000 字塞进一个向量里，
检索精度会明显下降 —— 这是文档难点 5「检索结果不准确」的一个隐藏成因。

切块策略：
- 优先按段落切，段落过长再按句号切，最后才硬切；
- 保留 overlap，避免答案正好落在切口上；
- 元数据（作物 / 病害 / 生长阶段 / 来源）冗余到每个 chunk，
  这样检索时可以在一条 SQL 里同时做向量排序和元数据过滤。
"""
from __future__ import annotations

import logging
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.knowledge import AgriculturalKnowledge, KnowledgeChunk
from app.services.rag.embedder import embedder

logger = logging.getLogger(__name__)

from app.services.rag.chunking import chunk_text, tokenize_for_index  # noqa: F401  兼容旧导入路径

class KnowledgeIndexer:
    def __init__(self, db: Session) -> None:
        self.db = db

    def reindex_document(self, knowledge_id: int) -> int:
        doc = self.db.get(AgriculturalKnowledge, knowledge_id)
        if doc is None:
            raise ValueError(f"知识文档不存在: {knowledge_id}")

        self.db.execute(delete(KnowledgeChunk).where(
            KnowledgeChunk.knowledge_id == knowledge_id))
        self.db.flush()

        pieces = chunk_text(doc.content)
        if not pieces:
            return 0

        vectors = embedder.embed_sync([self._embed_text(doc, p) for p in pieces])
        source_name = doc.source.name if doc.source else None
        source_url = doc.source.url if doc.source else None

        for i, (content, vec) in enumerate(zip(pieces, vectors)):
            self.db.add(KnowledgeChunk(
                knowledge_id=doc.id, chunk_index=i, content=content,
                token_count=len(content),
                crop_name=(doc.crop_names or [None])[0] if doc.crop_names else None,
                disease_name=doc.disease_name,
                growth_stage=doc.growth_stage,
                category=doc.category,
                source_name=source_name, source_url=source_url,
                embedding=vec,
                keywords=tokenize_for_index(content),
            ))
        self.db.flush()
        logger.info("文档 %s 切成 %d 个 chunk 并完成向量化", doc.id, len(pieces))
        return len(pieces)

    @staticmethod
    def _embed_text(doc: AgriculturalKnowledge, piece: str) -> str:
        """给切块补上标题与作物上下文再向量化。

        单独一个 chunk 常常看不出在说什么（比如「该阶段需控制湿度」），
        拼上标题后向量质量明显更好。
        """
        head = doc.title or ""
        if doc.crop_names:
            head += " " + " ".join(str(c) for c in doc.crop_names)
        if doc.disease_name:
            head += " " + doc.disease_name
        return f"{head}\n{piece}".strip()

    def reindex_all(self, only_pending: bool = False) -> int:
        stmt = select(AgriculturalKnowledge.id).where(
            AgriculturalKnowledge.deleted_at.is_(None))
        if only_pending:
            stmt = stmt.where(AgriculturalKnowledge.review_status == "approved")
        ids = list(self.db.scalars(stmt).all())
        total = 0
        for kid in ids:
            total += self.reindex_document(kid)
        self.db.commit()
        logger.info("重建索引完成：%d 篇文档，%d 个 chunk", len(ids), total)
        return total

    def stats(self) -> dict:
        from sqlalchemy import func
        total = self.db.scalar(select(func.count(KnowledgeChunk.id))) or 0
        embedded = self.db.scalar(
            select(func.count(KnowledgeChunk.id)).where(
                KnowledgeChunk.embedding.is_not(None))) or 0
        return {"chunks": total, "embedded": embedded,
                "embedding_mode": embedder.mode, "dim": embedder.dim}
