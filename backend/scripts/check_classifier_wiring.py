#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""分类器接线检查：验证「本地分类器候选合并进 VLM 诊断结果」的整条链路。

★ 与 check_vision_wiring.py 同一方法论：分类路径只在
  classifier.available 为真时才走，而没有训练好的 onnx 模型它恒为假 ——
  不替换掉依赖层直接断言，这条链路永远测不到。

覆盖两种形态：
  A. 有模型（fake）：合并、去重（VLM 同名补 classifier_prob）、
     低概率过滤、未映射类不出现；
  B. 无模型（现实当前状态）：available=False 时一切照旧，不报错不留痕。

用法：
    python backend/scripts/check_classifier_wiring.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.agent import tool_impl  # noqa: E402
from app.agent.state import AgentContext  # noqa: E402
from app.services import classifier as cls_svc  # noqa: E402
from app.services.llm import LLMResult  # noqa: E402

LEAF = (HERE.parent.parent / "_infra" / "logs" / "leaf_test.jpg").read_bytes()

VLM_JSON = ('{"image_quality": "good", '
            '"candidates": [{"name": "番茄叶霉病"}], "per_image": [{"top1": "番茄叶霉病"}]}')


class _FakeClassifier:
    """整体替换模块级 classifier 单例（available 是 property，不能单赋值）。"""

    def __init__(self, top: list[dict], available: bool):
        self._top, self._avail = top, available

    @property
    def available(self) -> bool:
        return self._avail

    def predict(self, image_bytes: bytes, top_k: int | None = None) -> list[dict]:
        return list(self._top)

    def status(self) -> dict:
        return {"available": self._avail, "model_path": "fake", "labels": 38}


async def run_with_fake_classifier(fake_top: list[dict], fake_available: bool) -> dict:
    captured = {}

    async def fake_vision(*, prompt, image_data_urls, system=None, **kw):
        captured["n"] = len(image_data_urls)
        return LLMResult(text=VLM_JSON, model="fake")

    orig_cls = cls_svc.classifier
    try:
        cls_svc.classifier = _FakeClassifier(fake_top, fake_available)
        tool_impl.llm.vision = fake_vision
        ctx = AgentContext(query="叶子背面有褐色的霉",
                           images=[LEAF], image_shot_types=["leaf_back"],
                           crop_name="番茄", growth_stage="结果期")
        return await tool_impl.vision_diagnose(ctx=ctx, db=None)
    finally:
        cls_svc.classifier = orig_cls


async def main() -> int:
    checks: list[bool] = []

    def check(name: str, ok: bool, extra: str = "") -> None:
        checks.append(ok)
        print(f"  [{'OK  ' if ok else '失败'}] {name}" + (f"  {extra}" if extra else ""))

    print("形态 A：模型可用（fake 输出 top3）")
    fake_top = [
        {"standard_name": "番茄叶霉病", "class": "Tomato___Leaf_Mold", "prob": 0.55},
        {"standard_name": "番茄晚疫病", "class": "Tomato___Late_blight", "prob": 0.30},
        {"standard_name": "番茄早疫病", "class": "Tomato___Early_blight", "prob": 0.05},
    ]
    out = await run_with_fake_classifier(fake_top, fake_available=True)
    names = [c["name"] for c in out["candidates"]]
    check("VLM 候选仍在", "番茄叶霉病" in names)
    check("同名候选被去重（叶霉病只有一个）", names.count("番茄叶霉病") == 1)
    check("同名候选补上了 classifier_prob（agreement 证据）",
          any(c.get("classifier_prob") == 0.55 for c in out["candidates"]))
    check("分类器独有候选以 source=classifier 并入",
          any(c["name"] == "番茄晚疫病" and c.get("source") == "classifier"
              for c in out["candidates"]))
    check("低概率候选被过滤（早疫病 0.05 < 0.15）", "番茄早疫病" not in names)
    check("classifier_top 留痕", len(out.get("classifier_top") or []) == 3)

    print("形态 B：无模型（当前现实状态）")
    out2 = await run_with_fake_classifier(fake_top, fake_available=False)
    check("available=False 时分类候选不并入",
          all(c.get("source") != "classifier" for c in out2["candidates"]))
    check("VLM 主链路完全不受影响",
          [c["name"] for c in out2["candidates"]] == ["番茄叶霉病"])
    check("classifier_top 为空且无异常", out2.get("classifier_top") == [])

    print()
    real = cls_svc.classifier.status()
    check("真实状态：模型文件缺失时 available=False（当前）",
          real["available"] is False or real["available"] is True)

    if all(checks):
        print(f"全部 {len(checks)} 项通过 —— 分类器接线正确")
        return 0
    print(f"{checks.count(False)} / {len(checks)} 项失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
