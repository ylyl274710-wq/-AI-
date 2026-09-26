"""健康检查。

★ 为什么这个端点比一般项目重要：本项目有 5 个外部依赖（PostgreSQL /
  Redis / MinIO / LLM / Embedding），且**每一个都能降级**。降级带来一个
  副作用 —— 系统看起来能跑，但用的是假数据或兜底算法，农户拿到的是
  质量下降的结论而毫无察觉。所以 /health 必须把「哪些依赖真的活着、
  当前是不是降级模式」明确报出来，前端要把它显示在页面上，
  而不是藏在日志里。
"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db, ping
from app.models.knowledge import AgriculturalKnowledge, KnowledgeChunk
from app.schemas import HealthOut
from app.services.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["健康检查"])


def _tcp_ok(host: str, port: int, timeout: float = 1.5) -> bool:
    """只做 TCP 连通性探测，不调客户端库。

    ★ 不用 redis-py / minio-py 的 ping：它们会建连接池、可能抛各种
      与「服务到底活没活」无关的异常（认证失败、版本不匹配），
      在健康检查里会把「服务活着但密码错了」误报成「服务挂了」。
      TCP 能连上就说明进程在，细节问题留给各自的业务端点去暴露。
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _redis_status() -> str:
    u = urlparse(settings.redis_url)
    host, port = u.hostname or "127.0.0.1", u.port or 6379
    return "up" if _tcp_ok(host, port) else "down"


def _minio_status() -> str:
    """只回答「MinIO 进程的端口通不通」。

    ★ 注意这个字段**不能**用来判断「图片能不能存」：MinIO 可能进程活着、
      端口通、但所有 S3 操作被拒（例如缺 license）。那种情况下这里仍然是
      "up"，而实际存储已经降级。要判断真实存储后端请看 storage 字段。
    """
    host, _, port = settings.minio_endpoint.partition(":")
    return "up" if _tcp_ok(host or "127.0.0.1", int(port or 9000)) else "down"


def _storage_backend() -> str:
    """对象存储的**实际**后端名（minio / local）。

    ★ 为什么必须有这个字段：get_storage() 在 MinIO 不可用时是**静默降级**的，
      只在日志里留一行 warning。而这个模块的模块级注释写着「降级不能藏在
      日志里，/health 要明确报出来」—— 光有 minio 的 TCP 探测达不到这个
      要求，因为它对「进程活着但功能全废」这一类故障是瞎的。
      （实测踩过：MinIO 缺 license → S3 全部 AccessDenied → 降级到本地磁盘，
        而 /health 照样报 minio="up"，前端那条「图片存储不可用」提示永远不弹。）
    """
    try:
        return get_storage().name
    except Exception as exc:  # noqa: BLE001
        # ★ 兜底必须留痕。第一版这里写的是裸的 `except Exception: return "unknown"`，
        #   结果它把 `NameError: name 'get_storage' is not defined`（我少写了一行
        #   import）也吞成了 "unknown" —— 表面看像「探测失败」，
        #   实际是「代码写错了」。兜底可以宽，但兜底必须说话。
        logger.warning("探测对象存储后端失败: %s: %s", type(exc).__name__, exc)
        return "unknown"


@router.get("/health", response_model=HealthOut, summary="健康检查")
def health(db: Session = Depends(get_db)) -> HealthOut:
    db_ok, db_msg = ping()

    docs = chunks = 0
    if db_ok:
        docs = db.scalar(select(func.count(AgriculturalKnowledge.id))
                         .where(AgriculturalKnowledge.deleted_at.is_(None))) or 0
        chunks = db.scalar(select(func.count(KnowledgeChunk.id))) or 0

    return HealthOut(
        ok=bool(db_ok),
        app=settings.app_name,
        env=settings.env,
        database="up" if db_ok else f"down ({db_msg})",
        redis=_redis_status(),
        minio=_minio_status(),
        storage=_storage_backend(),
        llm_enabled=settings.llm_enabled,
        vision_enabled=settings.vision_enabled,
        embedding_mode=("api" if settings.embedding_is_api else "local"),
        embedding_dim=settings.embedding_dim,
        knowledge_docs=int(docs),
        knowledge_chunks=int(chunks),
    )


@router.get("/ready", summary="就绪探针")
def ready(db: Session = Depends(get_db)) -> dict:
    """给编排系统用的轻量探针：数据库通了就算就绪。

    ★ 刻意不检查 LLM：模型服务抖动是常态，把它算进就绪会导致实例被反复
      重启，而重启并不能让第三方 API 恢复。
    """
    db_ok, _ = ping()
    return {"ready": bool(db_ok)}
