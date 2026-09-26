"""知识库查询与检索调试（文档第五章）。

三个用途：
  1. 让农户/农技员能直接查「番茄有哪些病害」
  2. 把检索过程摊开给开发者看 —— RRF 的向量排名、关键词排名、融合分
     全部返回，不然「为什么这条知识排在前面」只能靠猜
  3. 知识库体检：文档数、chunk 数、来源分布、有没有孤儿 chunk

★ 为什么把检索细节暴露成 API：文档难点 5（RAG 不准）与难点 13（结果无法
  验证）的共同解法就是「可观测」。没有这个端点，调阈值、换 Embedding 模型、
  改分词策略之后效果是变好还是变坏，只能凭感觉。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.farm import Crop
from app.models.knowledge import (AgriculturalKnowledge, DiseaseRecord,
                                  KnowledgeChunk, KnowledgeSource)
from app.models.user import User
from app.schemas import (ChunkHit, DiseaseBrief, DiseaseOut,
                         KnowledgeSearchIn, KnowledgeSearchOut)
from app.services.rag.retriever import HybridRetriever
from app.services.rag.rewrite import rewrite_query

router = APIRouter(prefix="/knowledge", tags=["知识库"])


# --------------------------------------------------------------------------- #
# 病害条目
# --------------------------------------------------------------------------- #


@router.get("/diseases", response_model=list[DiseaseBrief],
            summary="病害虫害列表")
def list_diseases(
    crop_name: str | None = Query(None, description="按作物过滤"),
    category: str | None = Query(None, description="disease/pest/physiological/environmental"),
    keyword: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[DiseaseRecord]:
    stmt = select(DiseaseRecord).where(DiseaseRecord.deleted_at.is_(None))
    if crop_name:
        # ★ 用 crop_names 数组包含判断，不用单值字段：一条知识常同时
        #   登记在多个作物上（「黄瓜白粉病」也在番茄、辣椒上）。用等值
        #   过滤会静默漏结果 —— 不报错，只是少给你几条。
        stmt = stmt.where(DiseaseRecord.crop_names.contains([crop_name]))
    if category:
        stmt = stmt.where(DiseaseRecord.category == category)
    if keyword:
        stmt = stmt.where(DiseaseRecord.standard_name.ilike(f"%{keyword}%"))
    return list(db.scalars(
        stmt.order_by(DiseaseRecord.standard_name).limit(limit)).all())


@router.get("/diseases/{disease_id}", response_model=DiseaseOut,
            summary="病害详情")
def disease_detail(disease_id: int, db: Session = Depends(get_db),
                   _: User = Depends(get_current_user)) -> DiseaseRecord:
    rec = db.get(DiseaseRecord, disease_id)
    if rec is None or rec.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "条目不存在")
    return rec


# --------------------------------------------------------------------------- #
# 检索调试
# --------------------------------------------------------------------------- #


@router.post("/search", response_model=KnowledgeSearchOut,
             summary="检索知识库（返回 RRF 明细）")
async def search(payload: KnowledgeSearchIn,
                 db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)) -> KnowledgeSearchOut:
    crops = [c for c in db.scalars(
        select(Crop.name).where(Crop.deleted_at.is_(None))).all()]

    rq = await rewrite_query(payload.query, known_crops=crops)
    if payload.crop_name:
        rq.crop = payload.crop_name
    if payload.growth_stage:
        rq.growth_stage = payload.growth_stage

    retriever = HybridRetriever(db)
    result = retriever.retrieve(rq, crop=payload.crop_name,
                                growth_stage=payload.growth_stage,
                                top_k=payload.top_k)

    return KnowledgeSearchOut(
        query_original=rq.original,
        query_rewritten=rq.search_text,
        expanded_terms=list(rq.expanded_terms),
        by_llm=bool(rq.by_llm),
        hits=[ChunkHit(
            chunk_id=c.chunk_id, knowledge_id=c.knowledge_id,
            title=c.title, content=c.content,
            disease_name=c.disease_name, crop_name=c.crop_name,
            category=c.category,
            fused_score=round(c.fused_score, 6),
            vector_score=round(c.vector_score, 6),
            keyword_score=round(c.keyword_score, 6),
            ranks=dict(c.ranks or {}),
            source_name=c.source_name, source_url=c.source_url,
        ) for c in result.chunks],
    )


# --------------------------------------------------------------------------- #
# 体检
# --------------------------------------------------------------------------- #


@router.get("/stats", summary="知识库体检")
def stats(db: Session = Depends(get_db),
          _: User = Depends(get_current_user)) -> dict:
    total_docs = db.scalar(
        select(func.count(AgriculturalKnowledge.id))
        .where(AgriculturalKnowledge.deleted_at.is_(None))) or 0
    approved = db.scalar(
        select(func.count(AgriculturalKnowledge.id))
        .where(AgriculturalKnowledge.deleted_at.is_(None),
               AgriculturalKnowledge.review_status == "approved")) or 0
    chunks = db.scalar(select(func.count(KnowledgeChunk.id))) or 0

    # ★ 这两个数字是知识库最常见的两种「隐性坏掉」：
    #   · 孤儿 chunk：文档删了但 chunk 没跟着删（外键没设 CASCADE 时会这样），
    #     它们照样会被检索到，等于库里混进了幽灵知识。
    #   · 未向量化 chunk：indexer 跑到一半失败，这些 chunk 永远搜不到，
    #     表现为「明明有这条知识却检索不出来」。
    orphan = db.scalar(
        select(func.count(KnowledgeChunk.id))
        .outerjoin(AgriculturalKnowledge,
                   KnowledgeChunk.knowledge_id == AgriculturalKnowledge.id)
        .where(AgriculturalKnowledge.id.is_(None))) or 0
    unembedded = db.scalar(
        select(func.count(KnowledgeChunk.id))
        .where(KnowledgeChunk.embedding.is_(None))) or 0

    by_category = db.execute(
        select(AgriculturalKnowledge.category, func.count(AgriculturalKnowledge.id))
        .where(AgriculturalKnowledge.deleted_at.is_(None))
        .group_by(AgriculturalKnowledge.category)).all()

    by_source = db.execute(
        select(KnowledgeSource.name, KnowledgeSource.trust_level,
               func.count(AgriculturalKnowledge.id))
        .join(AgriculturalKnowledge,
              AgriculturalKnowledge.source_id == KnowledgeSource.id)
        .group_by(KnowledgeSource.name, KnowledgeSource.trust_level)).all()

    return {
        "documents": int(total_docs),
        "documents_approved": int(approved),
        "documents_pending": int(total_docs - approved),
        "chunks": int(chunks),
        "chunks_orphan": int(orphan),
        "chunks_unembedded": int(unembedded),
        "diseases": db.scalar(
            select(func.count(DiseaseRecord.id))
            .where(DiseaseRecord.deleted_at.is_(None))) or 0,
        "crops": db.scalar(
            select(func.count(Crop.id))
            .where(Crop.deleted_at.is_(None))) or 0,
        "by_category": [{"category": c or "未分类", "count": int(n)}
                        for c, n in by_category],
        "by_source": [{"name": n, "trust_level": t, "count": int(c)}
                      for n, t, c in by_source],
    }


@router.get("/sources", summary="知识来源清单")
def list_sources(db: Session = Depends(get_db),
                 _: User = Depends(get_current_user)) -> list[dict]:
    """文档难点 12（知识过期）要求每条知识可追溯。这里把来源与可信度
    一并返回，前端在引用旁边显示「来源：XX 植保站（官方）」，
    农户才知道该不该信。"""
    rows = db.scalars(select(KnowledgeSource)
                      .order_by(KnowledgeSource.trust_level,
                                KnowledgeSource.name)).all()
    return [{
        "id": s.id, "name": s.name, "publisher": s.publisher, "url": s.url,
        "trust_level": s.trust_level, "license": s.license,
        "version": s.version, "status": s.status,
        "fetched_at": s.fetched_at.isoformat() if s.fetched_at else None,
    } for s in rows]
