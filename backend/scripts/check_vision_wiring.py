#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时探针：验证视觉诊断的提示词与拍摄部位标注真的发给了模型。

★ 为什么需要它：视觉路径只在 settings.vision_enabled 为真时才走到，
  而当前没有配 Key —— HTTP 冒烟测试完全覆盖不到这条链路。
  下面这两个 bug 就是这么漏掉的（都不报错，只是结果不对）：
    1. VISION_SYSTEM / VISION_SCHEMA 定义了却没传给模型 → 解析出空结果
    2. 图片预处理失败被跳过时，拍摄部位按下标回填会整体错位一位

做法：替换掉 tool_impl.llm.vision（真正发网络请求的那层），
      直接检查它收到的 prompt / system / 图片数量。

用法：
    python backend/scripts/_probe_vision_wiring.py
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.agent import tool_impl  # noqa: E402
from app.agent.prompts import VISION_SCHEMA, VISION_SYSTEM  # noqa: E402
from app.agent.state import AgentContext  # noqa: E402
from app.services.llm import LLMResult  # noqa: E402

LEAF = (HERE.parent.parent / "_infra" / "logs" / "leaf_test.jpg").read_bytes()

captured: dict = {}


async def fake_vision(*, prompt, image_data_urls, system=None, **kw):  # noqa: ANN001
    captured.update(prompt=prompt, n_images=len(image_data_urls), system=system, kw=kw)
    return LLMResult(text='{"image_quality": "good", "candidates": [{"name": "黄瓜白粉病"}]}',
                     model="fake")


tool_impl.llm.vision = fake_vision


async def main() -> int:
    checks: list[bool] = []

    def check(name: str, ok: bool, extra: str = "") -> None:
        checks.append(ok)
        print(f"  [{'OK  ' if ok else '失败'}] {name}" + (f"  {extra}" if extra else ""))

    # ---- 用例：3 张图，第 2 张是坏数据（预处理必然失败）----
    ctx = AgentContext(
        query="叶子背面有白毛",
        images=[LEAF, b"this-is-not-an-image", LEAF],
        image_shot_types=["leaf_front", "leaf_back", "lesion_closeup"],
        crop_name="黄瓜",
        growth_stage="结果期",
    )
    out = await tool_impl.vision_diagnose(ctx=ctx, db=None)

    print("用例：3 张图，第 2 张故意损坏\n")

    check("坏图被跳过，只送 2 张", captured.get("n_images") == 2,
          f"实际 {captured.get('n_images')}")
    check("函数没有抛异常，返回了结构", isinstance(out, dict) and "candidates" in out)

    prompt = captured.get("prompt", "")
    check("传了 VISION_SYSTEM 作为 system", captured.get("system") == VISION_SYSTEM,
          f"长度 {len(captured.get('system') or '')}")
    check("prompt 里带 VISION_SCHEMA 原文", VISION_SCHEMA.strip() in prompt,
          "断言原文而非抽查字段名：schema 一改，检查跟着改")
    check("prompt 里带作物与阶段", "黄瓜" in prompt and "结果期" in prompt)

    # 关键：错位检测。坏的是第 2 张（leaf_back），
    # 剩下的应当是 叶正面 + 病斑特写，绝不能出现「叶片背面」
    check("图 1 标注为叶片正面", "叶片正面" in prompt)
    check("图 3 标注为病斑特写（不错位）", "病斑特写" in prompt)
    check("【错位检测】不应出现「叶片背面」", "叶片背面" not in prompt,
          "出现即说明下标错位回填")

    print()
    if all(checks):
        print(f"全部 {len(checks)} 项通过 —— 视觉提示词与拍摄部位标注已正确接线")
        return 0
    print(f"{checks.count(False)} / {len(checks)} 项失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
