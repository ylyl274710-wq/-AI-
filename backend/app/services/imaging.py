"""图片处理：EXIF 清洗、压缩、缩略图。

原文档难点 14 说「用户的农田位置……属于业务数据」，但没提照片 EXIF。
现实是手机拍照默认写入 GPS 坐标 —— 一张照片就足以定位到农户的地块。
所以在**入库前**必须把 EXIF 剥掉，而不是等到展示时再处理。

同时这里承担文档难点 10 的「图片压缩」：农户上传的原图常见 4~8MB，
压缩到长边 1600 之后通常不到 500KB，上传和推理都明显变快。
"""
from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

MAX_EDGE = 1600          # 主图长边上限
THUMB_EDGE = 320         # 缩略图长边
JPEG_QUALITY = 85
THUMB_QUALITY = 75

Image.MAX_IMAGE_PIXELS = 80_000_000   # 防 decompression bomb


@dataclass
class ProcessedImage:
    main_bytes: bytes
    thumb_bytes: bytes
    width: int
    height: int
    sha256: str
    had_exif: bool
    had_gps: bool


class ImageProcessError(ValueError):
    pass


def process_upload(raw: bytes, *, strip_exif: bool = True) -> ProcessedImage:
    """把上传的原图处理成「可入库的主图 + 缩略图」。"""
    if not raw:
        raise ImageProcessError("图片内容为空")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.width * img.height > Image.MAX_IMAGE_PIXELS:
                raise ImageProcessError("图片像素数量超过安全上限")
            img.load()
            had_exif = bool(img.getexif())
            had_gps = False
            try:
                exif = img.getexif()
                # GPS 信息在 IFD 34853
                had_gps = bool(exif.get_ifd(0x8825))
            except Exception:  # noqa: BLE001
                had_gps = False

            # 依 EXIF 方向摆正，否则手机竖拍的照片会躺着
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            width, height = img.size

            main = img.copy()
            main.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
            thumb = img.copy()
            thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)

            main_buf = io.BytesIO()
            # 不传 exif 参数 = 不写 EXIF，这是剥离的关键一步
            save_kwargs: dict = {"format": "JPEG", "quality": JPEG_QUALITY,
                                 "optimize": True}
            if not strip_exif:
                # 明确要求保留时才回写原 EXIF（默认关闭）
                save_kwargs["exif"] = img.info.get("exif", b"")
            main.save(main_buf, **save_kwargs)

            thumb_buf = io.BytesIO()
            thumb.save(thumb_buf, format="JPEG", quality=THUMB_QUALITY,
                       optimize=True)
    except ImageProcessError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImageProcessError(f"图片解析失败: {exc}") from exc

    main_bytes = main_buf.getvalue()
    if had_gps:
        logger.info("已剥离图片中的 GPS 位置信息")
    return ProcessedImage(
        main_bytes=main_bytes,
        thumb_bytes=thumb_buf.getvalue(),
        width=width, height=height,
        sha256=hashlib.sha256(main_bytes).hexdigest(),
        had_exif=had_exif, had_gps=had_gps,
    )


def to_data_url(jpeg_bytes: bytes) -> str:
    """转成多模态模型能吃的 data URL。"""
    import base64
    return "data:image/jpeg;base64," + base64.b64encode(jpeg_bytes).decode("ascii")
