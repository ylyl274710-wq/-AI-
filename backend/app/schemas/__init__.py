"""请求 / 响应模型。

约定：
- 所有对外字段用 snake_case；
- 时间由 FastAPI 自动序列化成 ISO8601；
- 响应里不回传敏感字段（密码哈希、API Key、EXIF 原始信息）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[Any] = Field(default_factory=list)


class Ok(BaseModel):
    ok: bool = True
    message: str = ""


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=6, max_length=64)
    display_name: str = Field(default="", max_length=64)
    phone: str = Field(default="", max_length=20)
    city: str = Field(default="", max_length=32)

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 72:
            raise ValueError("密码 UTF-8 编码不能超过 72 字节")
        return v

    @field_validator("username")
    @classmethod
    def _username_charset(cls, v: str) -> str:
        if not v.replace("_", "").isalnum():
            raise ValueError("用户名只能包含字母、数字和下划线")
        return v


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(ORMBase):
    id: int
    username: str
    display_name: str | None = None
    phone: str | None = None
    city: str | None = None
    role: str
    elder_mode: bool
    created_at: datetime | None = None


class UserUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=32)
    elder_mode: bool | None = None


# --------------------------------------------------------------------------- #
# 农场 / 地块 / 作物
# --------------------------------------------------------------------------- #


class CropOut(ORMBase):
    id: int
    name: str
    aliases: list | None = None
    category: str | None = None
    growth_stages: list | None = None
    description: str | None = None


class FieldOut(ORMBase):
    id: int
    farm_id: int
    name: str
    area_mu: float | None = None
    soil_type: str | None = None
    irrigation_type: str | None = None
    lat: float | None = None
    lng: float | None = None


class FieldIn(BaseModel):
    name: str = Field(max_length=64)
    area_mu: float | None = None
    soil_type: str | None = Field(default=None, max_length=32)
    irrigation_type: str | None = Field(default=None, max_length=32)
    lat: float | None = None
    lng: float | None = None


class FarmOut(ORMBase):
    id: int
    name: str
    province: str | None = None
    city: str | None = None
    district: str | None = None
    total_area_mu: float | None = None
    remark: str | None = None
    fields: list[FieldOut] = Field(default_factory=list)


class FarmIn(BaseModel):
    name: str = Field(max_length=64)
    province: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=32)
    district: str | None = Field(default=None, max_length=32)
    lat: float | None = None
    lng: float | None = None
    total_area_mu: float | None = None
    remark: str | None = None


class GrowthRecordOut(ORMBase):
    id: int
    field_id: int
    crop_id: int
    variety: str | None = None
    stage: str | None = None
    sowing_date: date | None = None
    stage_start_date: date | None = None
    expected_harvest_date: date | None = None
    is_current: bool = True


class GrowthRecordIn(BaseModel):
    field_id: int
    crop_id: int
    variety: str | None = Field(default=None, max_length=64)
    # ★ stage 必填。模型里 CropGrowthRecord.stage 是 nullable=False，
    #   如果这里允许 None，插库时抛 IntegrityError → 500。
    #   与其让用户看到 500，不如在入口就返回 422 并说清缺了什么。
    #   业务上它也确实必填：不知道作物长到哪一步，Agent 没法判断
    #   「这个阶段容易得什么病」「这个阶段能不能用某个药」。
    stage: str = Field(max_length=32)
    sowing_date: date | None = None
    stage_start_date: date | None = None
    expected_harvest_date: date | None = None


# --------------------------------------------------------------------------- #
# 诊断 / 对话
# --------------------------------------------------------------------------- #

SHOT_TYPE_LITERAL = Literal["leaf_front", "leaf_back", "whole_plant",
                            "lesion_closeup", "unknown"]


class DiagnoseIn(BaseModel):
    """图片走 multipart，这里只放文字参数（见 api/v1/diagnosis.py）。"""

    query: str = Field(default="", max_length=1000)
    crop_name: str | None = Field(default=None, max_length=32)
    field_id: int | None = None
    growth_stage: str | None = Field(default=None, max_length=32)
    session_id: str | None = Field(default=None, max_length=64)
    shot_types: list[SHOT_TYPE_LITERAL] | None = None


class Citation(BaseModel):
    index: int
    title: str = ""
    source: str | None = None
    url: str | None = None


class ConfidenceOut(BaseModel):
    score: float = 0.0
    margin: float = 0.0
    agreement: float = 0.0
    coverage: float = 0.0
    passed: bool = False
    rejected: bool = False
    reasons: list[str] = Field(default_factory=list)
    missing_shots: list[str] = Field(default_factory=list)
    method: str | None = None


class TraceStepOut(BaseModel):
    node: str
    status: str = "ok"
    elapsed_ms: int = 0
    detail: str | None = None
    tools: list[str] = Field(default_factory=list)


class CandidateOut(BaseModel):
    """识别候选（VLM 与本地分类器合并后）。source: vlm / classifier。"""
    name: str
    source: str = "vlm"
    confidence: float | None = None       # 分类器候选的概率（可复现）
    classifier_prob: float | None = None  # VLM 候选被分类器确认时的概率


class DiagnoseOut(BaseModel):
    diagnosis_id: int | None = None
    session_id: str = ""
    state: str = ""
    intent: str = ""
    answer: str = ""
    answer_sections: dict[str, str] = Field(default_factory=dict)
    top1: str | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)
    confidence: ConfidenceOut | None = None
    citations: list[Citation] = Field(default_factory=list)
    trace: list[TraceStepOut] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    risk: list[dict] = Field(default_factory=list)
    rule: dict | None = None
    tasks_created: list[int] = Field(default_factory=list)
    degraded: bool = False
    llm_model: str | None = None
    token_usage: dict | None = None
    errors: list[str] = Field(default_factory=list)


class ChatMessageOut(ORMBase):
    id: int
    session_id: str
    role: str
    content: str
    intent: str | None = None
    trace: list | None = None
    tool_calls: list | None = None
    references: list | None = None
    degraded: bool = False
    diagnosis_id: int | None = None
    created_at: datetime | None = None


class SessionOut(BaseModel):
    session_id: str
    last_at: datetime | None = None
    message_count: int = 0
    last_question: str | None = None


# --------------------------------------------------------------------------- #
# 知识库 / 农药
# --------------------------------------------------------------------------- #


class DiseaseOut(ORMBase):
    id: int
    standard_name: str
    aliases: list | None = None
    category: str
    crop_names: list | None = None
    pathogen: str | None = None
    symptoms: str | None = None
    conditions: dict | None = None
    stage_susceptibility: dict | None = None
    control_advice: str | None = None
    severity: str | None = None


class DiseaseBrief(BaseModel):
    id: int
    standard_name: str
    category: str
    crop_names: list | None = None
    severity: str | None = None


class KnowledgeSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    crop_name: str | None = None
    growth_stage: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class ChunkHit(BaseModel):
    chunk_id: int
    knowledge_id: int
    title: str = ""
    content: str = ""
    disease_name: str | None = None
    crop_name: str | None = None
    category: str | None = None
    fused_score: float = 0.0
    vector_score: float = 0.0
    keyword_score: float = 0.0
    ranks: dict[str, int] = Field(default_factory=dict)
    source_name: str | None = None
    source_url: str | None = None


class KnowledgeSearchOut(BaseModel):
    query_original: str
    query_rewritten: str
    expanded_terms: list[str] = Field(default_factory=list)
    by_llm: bool = False
    hits: list[ChunkHit] = Field(default_factory=list)


class IngredientOut(ORMBase):
    id: int
    standard_name: str
    aliases: list | None = None
    category: str | None = None
    resistance_group: str | None = None
    toxicity: str | None = None


class PesticideOut(ORMBase):
    id: int
    product_name: str
    active_ingredient_names: list | None = None
    formulation: str | None = None
    manufacturer: str | None = None
    registered_crops: list | None = None
    target_pests: list | None = None
    applicable_stages: list | None = None
    safety_interval_days: int | None = None
    dosage_text: str | None = None
    precautions: str | None = None
    registration_no: str | None = None
    source_url: str | None = None
    data_verified_at: date | None = None
    review_status: str | None = None
    # ★ 明确告诉前端：数据是人工整理的，且登记信息不含「使用阶段」，
    #   该字段为人工标注，展示时必须带提示（文档难点 3）
    data_caveat: str | None = None


class RuleCheckIn(BaseModel):
    crop_name: str
    disease_name: str
    growth_stage: str | None = None


class RuleCheckItem(BaseModel):
    product_id: int
    product_name: str
    passed: bool
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    label_dosage_text: str | None = None
    safety_interval_days: int | None = None
    active_ingredients: list[str] = Field(default_factory=list)
    resistance_groups: list[str] = Field(default_factory=list)
    source_url: str | None = None


class RuleCheckOut(BaseModel):
    crop_name: str | None = None
    disease_name: str | None = None
    growth_stage: str | None = None
    checked: int = 0
    hard_blocked: bool = False
    message: str = ""
    approved: list[RuleCheckItem] = Field(default_factory=list)
    blocked: list[RuleCheckItem] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 用药记录 / 任务 / 天气
# --------------------------------------------------------------------------- #


class UsageIn(BaseModel):
    field_id: int | None = None
    crop_id: int | None = None
    product_id: int | None = None
    product_name_raw: str | None = Field(default=None, max_length=128)
    active_ingredient_name: str | None = Field(default=None, max_length=64)
    resistance_group: str | None = Field(default=None, max_length=32)
    used_at: date | None = None
    note: str | None = Field(default=None, max_length=500)
    confirmed: bool = False


class UsageOut(ORMBase):
    id: int
    field_id: int | None = None
    product_name_raw: str | None = None
    active_ingredient_name: str | None = None
    resistance_group: str | None = None
    used_at: date | None = None
    record_source: str | None = None
    confirmed: bool
    note: str | None = None


class TaskIn(BaseModel):
    title: str = Field(max_length=128)
    task_type: str = Field(default="other", max_length=24)
    due_date: date | None = None
    detail: str | None = Field(default=None, max_length=1000)
    priority: int = 2
    field_id: int | None = None


class TaskOut(ORMBase):
    id: int
    title: str
    task_type: str
    status: str
    due_date: date | None = None
    detail: str | None = None
    priority: int = 2
    field_id: int | None = None
    created_by_node: str | None = None
    # ★ 这两个字段必须返回：农户完成任务时填的那句话，是判断「Agent 的建议
    #   到底有没有用」的唯一真实信号（难点 13）。只写进库不返回给前端，
    #   「已完成」列表里就看不到当时反馈了什么，这个字段等于白存。
    feedback: str | None = None
    done_at: datetime | None = None


class WeatherOut(BaseModel):
    record_date: date | None = None
    temp_avg: float | None = None
    temp_max: float | None = None
    temp_min: float | None = None
    humidity_avg: float | None = None
    humidity_max: float | None = None
    rainfall_hours: float | None = None
    wind_level: float | None = None
    provider: str | None = None
    is_mock: bool = False
    caveat: str | None = None


class RiskOut(BaseModel):
    risk_level: str = "low"
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    advice: str = ""
    disease_name: str | None = None


class FarmOverviewOut(BaseModel):
    user: UserOut
    farms: list[FarmOut] = Field(default_factory=list)
    current_crops: list[dict] = Field(default_factory=list)
    pending_tasks: list[TaskOut] = Field(default_factory=list)
    recent_diagnoses: list[dict] = Field(default_factory=list)
    weather: WeatherOut | None = None
    risks: list[RiskOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 健康检查
# --------------------------------------------------------------------------- #


class HealthOut(BaseModel):
    ok: bool
    app: str
    env: str
    database: str = ""
    redis: str = ""
    minio: str = ""
    # ★ 对象存储的**实际后端**（minio / local），与上面的 minio 字段不是一回事：
    #   minio 只回答「MinIO 进程的端口通不通」，
    #   storage 回答「图片最终存在哪」。两者会不一致 —— 例如 MinIO 进程活着
    #   但所有 S3 操作被拒（缺 license），此时 minio="up" 而 storage="local"。
    #   前端要判断「图片存哪儿」必须看 storage，看 minio 会漏报降级。
    storage: str = ""
    llm_enabled: bool = False
    vision_enabled: bool = False
    embedding_mode: str = ""
    embedding_dim: int = 0
    knowledge_docs: int = 0
    knowledge_chunks: int = 0
    version: str = "0.1.0"
