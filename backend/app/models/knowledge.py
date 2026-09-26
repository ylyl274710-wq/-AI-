"""农业知识库与 RAG 检索所需的数据结构。

★ 这里补了文档第五章缺失的三张表（原文档只有一句「agricultural_knowledge：
农业知识文档及元数据」）：
  - knowledge_sources  : 来源溯源（对应难点 12 的 source / version / status）
  - disease_records    : 病害虫害基础信息 + 结构化发生条件（对应难点 7）
  - knowledge_chunks   : RAG 切块与向量（没有它，文档难点 5 的检索无从落地）
"""
from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer,
                        String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.db import Base
from app.models.base import PKMixin, SoftDeleteMixin, TimestampMixin, VersionedMixin

# 病害类别（对应文档「识别病害、虫害、生理障碍或环境问题」）
CAT_DISEASE = "disease"                 # 病害（真菌/细菌/病毒）
CAT_PEST = "pest"                       # 虫害
CAT_PHYSIOLOGICAL = "physiological"     # 生理障碍（缺素、日灼…）
CAT_ENVIRONMENTAL = "environmental"     # 环境问题（药害、冻害…）
CATEGORIES = (CAT_DISEASE, CAT_PEST, CAT_PHYSIOLOGICAL, CAT_ENVIRONMENTAL)


class KnowledgeSource(Base, PKMixin, TimestampMixin):
    """知识来源。任何一条知识都必须能追溯到来源，否则不允许进生产库。"""
    __tablename__ = "knowledge_sources"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(120))
    url: Mapped[str | None] = mapped_column(String(512))
    # 可信度分级：official 官方/农业技术推广站，academic 学术，vendor 厂商，community 社区
    trust_level: Mapped[str] = mapped_column(String(16), nullable=False,
                                             default="community", index=True)
    license: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    remark: Mapped[str | None] = mapped_column(Text)


class DiseaseRecord(Base, PKMixin, TimestampMixin, SoftDeleteMixin, VersionedMixin):
    """病害 / 虫害 / 生理障碍标准条目。"""
    __tablename__ = "disease_records"

    standard_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    aliases: Mapped[list | None] = mapped_column(JSONB, default=list)
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    # 适用的作物标准名列表，例如 ["番茄","辣椒"]
    # ★ 原写法是 JSON + index=True，实测建表直接失败：
    #     psycopg2.errors.UndefinedObject: data type json has no default operator
    #     class for access method "btree"
    #   PostgreSQL 的 json 类型没有 btree 默认操作符类，而且就算能建，
    #   btree 对「数组包含」查询也没用。改用 JSONB + GIN 索引，
    #   让 `crop_names @> '["番茄"]'` 这类「某作物有哪些病害」的查询能走索引。
    crop_names: Mapped[list | None] = mapped_column(JSONB, default=list)
    pathogen: Mapped[str | None] = mapped_column(String(128))
    symptoms: Mapped[str | None] = mapped_column(Text)
    # ★ 结构化发生条件 —— 文档难点 7 说「根据知识库中的发生条件计算风险等级」，
    #   但没定义条件怎么存。这里定成可计算的字段：
    #   {"temp_min":20,"temp_max":30,"humidity_min":85,"rain_hours_min":4,
    #    "duration_days":2,"wind":"any","note":"..."}
    conditions: Mapped[dict | None] = mapped_column(JSONB)
    # 不同生长阶段的易感程度 {"苗期":"高","开花期":"中"}
    stage_susceptibility: Mapped[dict | None] = mapped_column(JSONB)
    control_advice: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(String(16))  # low/medium/high
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="SET NULL"))
    source: Mapped["KnowledgeSource | None"] = relationship(lazy="joined")

    __table_args__ = (
        # 数组/对象包含查询走 GIN（btree 对 json 类型不可用，见 crop_names 注释）
        Index("ix_disease_records_crop_names_gin", "crop_names",
              postgresql_using="gin"),
        Index("ix_disease_records_aliases_gin", "aliases",
              postgresql_using="gin"),
    )


class AgriculturalKnowledge(Base, PKMixin, TimestampMixin, SoftDeleteMixin,
                            VersionedMixin):
    """知识文档。一篇文档会被切成多个 chunk 供检索。"""
    __tablename__ = "agricultural_knowledge"

    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    crop_names: Mapped[list | None] = mapped_column(JSONB, default=list)
    disease_name: Mapped[str | None] = mapped_column(String(128), index=True)
    growth_stage: Mapped[str | None] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSONB, default=list)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="SET NULL"))
    # 人工审核状态（文档难点 4：通过人工审核后再进入生产知识库）
    review_status: Mapped[str] = mapped_column(String(16), nullable=False,
                                               default="pending", index=True)

    source: Mapped["KnowledgeSource | None"] = relationship(lazy="joined")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="knowledge", cascade="all, delete-orphan")


class KnowledgeChunk(Base, PKMixin, TimestampMixin):
    """RAG 切块 + 向量。

    检索元数据（crop_name / growth_stage / category）冗余在 chunk 上，
    这样元数据过滤可以和向量检索在同一个 SQL 里完成，不用回表 join。
    """
    __tablename__ = "knowledge_chunks"

    knowledge_id: Mapped[int] = mapped_column(
        ForeignKey("agricultural_knowledge.id", ondelete="CASCADE"),
        nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 冗余检索元数据
    crop_name: Mapped[str | None] = mapped_column(String(64), index=True)
    disease_name: Mapped[str | None] = mapped_column(String(128), index=True)
    growth_stage: Mapped[str | None] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(32), index=True)
    source_name: Mapped[str | None] = mapped_column(String(160))
    source_url: Mapped[str | None] = mapped_column(String(512))
    extra: Mapped[dict | None] = mapped_column(JSONB)
    # 向量列，维度来自配置；切换 Embedding 模型必须重新建索引
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.embedding_dim))
    # 关键词检索辅助：分词后的字符串（jieba 结果用空格连接）
    keywords: Mapped[str | None] = mapped_column(Text)

    knowledge: Mapped["AgriculturalKnowledge"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_crop_stage", "crop_name", "growth_stage"),
        Index("ix_chunks_category", "category"),
        # 向量近似索引：知识库规模不大时用 HNSW，召回质量优先
        Index("ix_chunks_embedding_hnsw", "embedding",
              postgresql_using="hnsw",
              postgresql_with={"m": 16, "ef_construction": 64},
              postgresql_ops={"embedding": "vector_cosine_ops"}),
        # 关键词兜底：中文用 pg_trgm 做相似度
        Index("ix_chunks_content_trgm", "content",
              postgresql_using="gin",
              postgresql_ops={"content": "gin_trgm_ops"}),
    )


class KnowledgeAuditLog(Base, PKMixin, TimestampMixin):
    """知识变更审计（文档难点 12：旧版本保留用于审计，但默认不参与推荐）。"""
    __tablename__ = "knowledge_audit_logs"

    knowledge_id: Mapped[int] = mapped_column(
        ForeignKey("agricultural_knowledge.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    from_version: Mapped[int | None] = mapped_column(Integer)
    to_version: Mapped[int | None] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    operator: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    is_rollback_target: Mapped[bool] = mapped_column(Boolean, default=False)
