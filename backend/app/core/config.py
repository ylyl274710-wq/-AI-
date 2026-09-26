"""全局配置。

所有可调参数集中在这里，通过 backend/.env 覆盖。设计原则：
- 任何外部依赖（LLM / Redis / MinIO / 天气）缺失时都要能降级，不允许因为
  少一个 Key 就让整条链路跑不起来（对应文档第七章的错误兜底要求）。
- 因此配置里大量出现 `*_enabled` / `provider` 这类开关，而不是硬编码。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "农业病虫害智能诊断与农事决策 Agent"
    env: str = "dev"
    debug: bool = True
    api_prefix: str = "/api/v1"
    # ★ 必须包含 "null"：用 file:// 直接打开 index.html 时，浏览器发出的
    #   Origin 头就是字符串 "null"。不加这一项，前端「双击打开」的用法会被
    #   CORS 全部拦掉，而浏览器控制台只会说「CORS 失败」，很难联想到
    #   是 file:// 的 origin 特殊。
    cors_origins: str = ("http://localhost:5173,http://127.0.0.1:5173,"
                         "http://127.0.0.1:8001,http://localhost:8001,null")

    # ---------------- 数据库 ----------------
    database_url: str = "postgresql+psycopg2://agri:agri_dev_pwd@127.0.0.1:5432/agri_pest"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ---------------- Redis / Celery ----------------
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"
    celery_result_backend: str = "redis://127.0.0.1:6379/2"
    use_celery: bool = True

    # ---------------- MinIO ----------------
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "agriadmin"
    minio_secret_key: str = "agri_dev_pwd"
    minio_bucket: str = "agri-media"
    minio_secure: bool = False
    minio_public_base_url: str = "http://127.0.0.1:9000"

    # ---------------- LLM ----------------
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_vision_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2

    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""

    # ---------------- Embedding ----------------
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    hf_endpoint: str = "https://hf-mirror.com"

    # ---------------- RAG ----------------
    rag_top_k: int = 8
    rag_candidate_k: int = 40
    rag_vector_weight: float = 1.0
    rag_keyword_weight: float = 1.0
    rag_rrf_k: int = 60
    rag_min_score: float = 0.0
    rag_chunk_size: int = 420
    rag_chunk_overlap: int = 60

    # ---------------- 置信度闸门 ----------------
    confidence_threshold: float = 0.55
    confidence_w_margin: float = 0.45
    confidence_w_agreement: float = 0.30
    confidence_w_coverage: float = 0.25
    confidence_reject_threshold: float = 0.25

    # ---------------- 农药规则 ----------------
    rule_hard_block: bool = True
    safety_interval_warn_days: int = 3
    resistance_repeat_limit: int = 2
    dose_guard_enabled: bool = True

    # ---------------- 天气 ----------------
    weather_provider: str = "mock"
    weather_api_key: str = ""
    weather_api_base: str = "https://devapi.qweather.com/v7"
    weather_timeout_seconds: int = 8
    weather_cache_ttl_seconds: int = 1800

    # ---------------- 本地图像分类器（可选增强，缺模型文件时自动停用） ----------------
    classifier_enabled: bool = True
    classifier_model_path: str = "models/pv_classifier.onnx"
    classifier_labels_path: str = "models/pv_labels.json"
    classifier_top_k: int = 3
    classifier_min_prob: float = 0.15

    # ---------------- 认证 ----------------
    jwt_secret: str = "dev-only-change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080

    # ---------------- 上传 ----------------
    max_upload_mb: int = 10
    allowed_image_types: str = "image/jpeg,image/png,image/webp"
    strip_exif: bool = True

    # ---------------- 存储路径 ----------------
    storage_dir: Path = Field(default=PROJECT_ROOT / "storage")
    # 本地 Embedding 模型目录。scripts/fetch_embedding_model.py 会把模型下到这里，
    # embedder 优先用本地目录，避免每次启动都去连 huggingface。
    model_dir: Path = Field(default=PROJECT_ROOT / "models")

    # ------------------------------------------------------------------ #
    @field_validator("llm_base_url", "embedding_base_url", "llm_fallback_base_url",
                     mode="before")
    @classmethod
    def _strip_trailing_slash(cls, v):
        return v.rstrip("/") if isinstance(v, str) else v

    # ------------------------------------------------------------------ #
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_image_type_list(self) -> list[str]:
        return [t.strip() for t in self.allowed_image_types.split(",") if t.strip()]

    @property
    def llm_enabled(self) -> bool:
        """是否具备真实调用大模型的条件。"""
        return bool(self.llm_api_key.strip())

    @property
    def vision_enabled(self) -> bool:
        """视觉诊断是否可用。没有单独的视觉模型时，退回复用主模型。"""
        return bool(self.llm_api_key.strip())

    @property
    def effective_vision_model(self) -> str:
        return self.llm_vision_model.strip() or self.llm_model

    @property
    def embedding_is_api(self) -> bool:
        return self.embedding_provider.lower() == "openai"

    def ensure_dirs(self) -> None:
        for sub in ("uploads", "thumbnails", "knowledge"):
            (self.storage_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # 让 sentence-transformers 走国内镜像
    if s.hf_endpoint:
        os.environ.setdefault("HF_ENDPOINT", s.hf_endpoint)
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    return s


settings = get_settings()
