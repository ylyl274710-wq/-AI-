"""对象存储抽象（文档第二章 MinIO）。

★ 需要说明的一点：MinIO 的开源版（minio/minio）已于 2025 年归档停止维护，
  官方下载页 dl.min.io/server 现在返回 410 Gone，只保留商业版 Aistor 的构建。
  所以这里做成接口 + 双实现：
    - MinioStorage  : 按文档接 MinIO（本项目已在 _infra/minio 部署）
    - LocalStorage  : MinIO 不可用时退化为本地磁盘 + FastAPI 静态目录
  业务代码只依赖 Storage 接口，将来换 SeaweedFS / S3 / OSS 只改一个类。
"""
from __future__ import annotations

import logging
import mimetypes
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)


class Storage(ABC):
    name: str = "abstract"

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str | None = None) -> str: ...

    @abstractmethod
    def url_for(self, key: str) -> str: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def healthy(self) -> bool: ...

    @staticmethod
    def build_key(prefix: str, filename: str, ext: str = ".jpg") -> str:
        return f"{prefix}/{uuid.uuid4().hex}{ext}"


class LocalStorage(Storage):
    name = "local"

    def __init__(self, base: Path | None = None, public_prefix: str = "/media") -> None:
        self.base = base or (settings.storage_dir / "media")
        self.base.mkdir(parents=True, exist_ok=True)
        self.public_prefix = public_prefix

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        path = self.base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.url_for(key)

    def url_for(self, key: str) -> str:
        return f"{self.public_prefix}/{key}"

    def delete(self, key: str) -> None:
        (self.base / key).unlink(missing_ok=True)

    def healthy(self) -> bool:
        return True


class MinioStorage(Storage):
    name = "minio"

    def __init__(self) -> None:
        from minio import Minio  # 延迟导入，没装也不影响启动
        self.client = Minio(settings.minio_endpoint,
                            access_key=settings.minio_access_key,
                            secret_key=settings.minio_secret_key,
                            secure=settings.minio_secure)
        self.bucket = settings.minio_bucket
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
                logger.info("已创建 MinIO bucket: %s", self.bucket)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"MinIO bucket 初始化失败: {exc}") from exc

    def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        import io
        ctype = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data),
                               content_type=ctype)
        return self.url_for(key)

    def url_for(self, key: str) -> str:
        return f"{settings.minio_public_base_url.rstrip('/')}/{self.bucket}/{key}"

    def delete(self, key: str) -> None:
        try:
            self.client.remove_object(self.bucket, key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除对象失败 %s: %s", key, exc)

    def healthy(self) -> bool:
        try:
            return bool(self.client.bucket_exists(self.bucket))
        except Exception:  # noqa: BLE001
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    """优先 MinIO，不可用则自动降级为本地磁盘。"""
    global _storage
    if _storage is not None:
        return _storage
    try:
        st = MinioStorage()
        if st.healthy():
            logger.info("对象存储: MinIO (%s)", settings.minio_endpoint)
            _storage = st
            return _storage
    except Exception as exc:  # noqa: BLE001
        logger.warning("MinIO 不可用，降级为本地存储: %s", exc)
    _storage = LocalStorage()
    logger.info("对象存储: 本地磁盘 (%s)", _storage.base)
    return _storage


def reset_storage() -> None:
    """测试用：强制下次重新探测。"""
    global _storage
    _storage = None
