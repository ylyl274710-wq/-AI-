"""本地图像分类器（PlantVillage 微调模型，ONNX 推理）。

设计立场（与 storage / weather 的降级模式一致）：
  - 模型文件不存在或 onnxruntime 未安装时 available=False，predict 返回 []，
    调用方（tool_impl.vision_diagnose）照常走 VLM 诊断 —— 分类器是**可选增强**，
    绝不能因为它的缺席拖垮主链路；
  - 只输出「知识库里真实存在条目」的类别（pv_labels.json 里 standard_name
    非 null 的），输出不了的类别宁可不说 —— 这是闸门 A 的同一条诚实原则。

为什么用本地小分类器而不用 VLM 硬看：分类器的 top-k 概率是**可复现、
可校准**的信号，直接喂给置信度的 margin / agreement 分量；VLM 的「我觉得
是XX」做不到这一点（实施问题清单 A3 的结论）。

预处理必须与训练脚本（scripts/train_pv_classifier.py）严格一致：
resize 224×224 + ImageNet 归一化。
"""
from __future__ import annotations

import importlib.util
import json
import logging
from functools import lru_cache
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)
_INPUT_SIZE = 224


@lru_cache(maxsize=1)
def _load_label_map() -> dict:
    path = Path(settings.classifier_labels_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    # 顺序即模型输出的类别顺序：按 labels 字典序生成训练脚本时必须同序
    labels = sorted(data["labels"].keys())
    return {"labels": labels,
            "meta": data["labels"],
            "version": data.get("version", "unversioned")}


class DiseaseClassifier:
    """ONNX 分类器封装。首次 predict 时才加载模型（fail-safe）。"""

    def __init__(self) -> None:
        self._session = None
        self._load_attempted = False

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        if not settings.classifier_enabled:
            return False
        if not Path(settings.classifier_model_path).exists():
            return False
        if importlib.util.find_spec("onnxruntime") is None:
            return False
        try:
            _load_label_map()
        except Exception:  # noqa: BLE001
            logger.warning("分类器标签文件不可用", exc_info=True)
            return False
        return True

    def _get_session(self):
        if self._load_attempted:
            return self._session
        self._load_attempted = True
        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(
                settings.classifier_model_path, providers=["CPUExecutionProvider"])
            logger.info("分类器模型已加载: %s", settings.classifier_model_path)
        except Exception:  # noqa: BLE001
            logger.warning("分类器模型加载失败，分类增强不可用", exc_info=True)
            self._session = None
        return self._session

    # ------------------------------------------------------------------ #
    def _preprocess(self, image_bytes: bytes):
        import io

        import numpy as np
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            img = img.resize((_INPUT_SIZE, _INPUT_SIZE), Image.LANCZOS)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - np.array(_IMAGENET_MEAN, dtype=np.float32)) \
            / np.array(_IMAGENET_STD, dtype=np.float32)
        return arr.transpose(2, 0, 1)[np.newaxis, ...]   # (1, 3, 224, 224)

    def predict(self, image_bytes: bytes, top_k: int | None = None) -> list[dict]:
        """返回 top-k：[{standard_name, class, zh, prob}]，只含已映射条目。"""
        if not self.available:
            return []
        k = top_k or settings.classifier_top_k
        try:
            session = self._get_session()
            if session is None:
                return []
            import numpy as np

            inp = self._preprocess(image_bytes)
            input_name = session.get_inputs()[0].name
            logits = session.run(None, {input_name: inp})[0][0]
            probs = np.exp(logits - logits.max())
            probs = probs / probs.sum()

            meta = _load_label_map()["meta"]
            labels = _load_label_map()["labels"]
            order = np.argsort(probs)[::-1]
            out: list[dict] = []
            for idx in order[:k * 3]:          # 多取一些，过滤未映射类后仍够 top-k
                name = labels[int(idx)]
                info = meta.get(name, {})
                std = info.get("standard_name")
                if std is None:
                    continue                    # 未映射类：宁缺毋滥
                out.append({"standard_name": std, "class": name,
                            "zh": info.get("zh"), "prob": round(float(probs[idx]), 4),
                            "caveat": info.get("caveat")})
                if len(out) >= k:
                    break
            return out
        except Exception:  # noqa: BLE001
            # 任何推理失败都不能影响 VLM 主链路 —— 吞掉并留痕
            logger.warning("分类器推理失败，本次跳过分类增强", exc_info=True)
            return []

    def status(self) -> dict:
        return {"available": self.available,
                "model_path": settings.classifier_model_path,
                "labels": len(_load_label_map()["labels"]) if self.available else 0}


classifier = DiseaseClassifier()
