"""模型聚合导出。

Alembic autogenerate 依赖这里把所有模型都 import 进来，
否则新表不会被识别为「需要创建」。
"""
from app.models.base import (Base, PKMixin, SoftDeleteMixin, TimestampMixin,
                             VersionedMixin)
from app.models.diagnosis import (ST_FAILED, ST_NEEDS_MORE_INFO, ST_PENDING,
                                  ST_REJECTED, ST_SUCCEEDED, DiagnosisImage,
                                  DiagnosisRecord)
from app.models.farm import Crop, CropGrowthRecord, Farm, Field
from app.models.knowledge import (CAT_DISEASE, CAT_ENVIRONMENTAL, CAT_PEST,
                                  CAT_PHYSIOLOGICAL, CATEGORIES,
                                  AgriculturalKnowledge, DiseaseRecord,
                                  KnowledgeAuditLog, KnowledgeChunk,
                                  KnowledgeSource)
from app.models.misc import (TASK_STATUS, TASK_TYPES, ChatMessage, FarmTask,
                             WeatherRecord)
from app.models.pesticide import (PESTICIDE_CATEGORIES, TOXICITY_LEVELS,
                                  PesticideActiveIngredient, PesticideOcrDraft,
                                  PesticideProduct, PesticideUsage)
from app.models.user import ROLES, ROLE_ADMIN, ROLE_AGRONOMIST, ROLE_FARMER, User

__all__ = [
    "Base", "PKMixin", "TimestampMixin", "SoftDeleteMixin", "VersionedMixin",
    "User", "ROLE_FARMER", "ROLE_AGRONOMIST", "ROLE_ADMIN", "ROLES",
    "Farm", "Field", "Crop", "CropGrowthRecord",
    "KnowledgeSource", "DiseaseRecord", "AgriculturalKnowledge", "KnowledgeChunk",
    "KnowledgeAuditLog", "CATEGORIES", "CAT_DISEASE", "CAT_PEST",
    "CAT_PHYSIOLOGICAL", "CAT_ENVIRONMENTAL",
    "PesticideActiveIngredient", "PesticideProduct", "PesticideUsage",
    "PesticideOcrDraft", "PESTICIDE_CATEGORIES", "TOXICITY_LEVELS",
    "DiagnosisRecord", "DiagnosisImage",
    "ST_PENDING", "ST_SUCCEEDED", "ST_NEEDS_MORE_INFO", "ST_REJECTED", "ST_FAILED",
    "WeatherRecord", "FarmTask", "ChatMessage", "TASK_TYPES", "TASK_STATUS",
]
