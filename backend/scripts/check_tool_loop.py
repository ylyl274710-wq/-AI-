#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""工具循环接线检查：LLM 自主调工具 → 真实分发器执行 → 证据回填。

★ 为什么需要它：工具循环只在 llm_enabled 且 intent=consult 时走，
  且每次要花真实 token —— 不替换掉 llm 层就永远无法稳定验证这条链路。
  与 check_vision_wiring / check_classifier_wiring 同一方法论。

做法：替换 llm.chat_with_tools（第一轮返回 search_knowledge 调用，
第二轮返回 DONE），分发器与知识库检索走**真实**实现 —— 验证的是
「模型要的工具真的接得上、工具结果真的回得去」。

用法：
    python backend/scripts/check_tool_loop.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.agent import tool_loop as tl  # noqa: E402
from app.agent.state import AgentContext  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402

calls: list[dict] = []


async def fake_chat_with_tools(messages, tools, **kw):  # noqa: ANN001
    calls.append({"n_tools": len(tools), "n_messages": len(messages),
                  "last_role": messages[-1]["role"]})
    if len(calls) == 1:
        return {"content": "", "model": "fake", "tool_calls": [
            {"id": "call_1", "name": "search_knowledge",
             "arguments": '{"query": "白粉病 防治", "crop": "黄瓜"}'}]}
    # 第二轮：模型看到工具结果后认为信息足够（按约定输出 DONE）
    assert any(m["role"] == "tool" for m in messages), "工具结果没回灌进消息"
    return {"content": "DONE", "model": "fake", "tool_calls": []}


async def main() -> int:
    checks: list[bool] = []

    def check(name: str, ok: bool, extra: str = "") -> None:
        checks.append(ok)
        print(f"  [{'OK  ' if ok else '失败'}] {name}" + (f"  {extra}" if extra else ""))

    orig = tl.llm.chat_with_tools
    tl.llm.chat_with_tools = fake_chat_with_tools
    try:
        db = SessionLocal()
        ctx = AgentContext(user_id=1, session_id="probe-tool-loop",
                           query="黄瓜白粉病怎么防治", crop_name="黄瓜")
        out = await tl.run_tool_loop(ctx, db, max_rounds=3)
        db.close()
    finally:
        tl.llm.chat_with_tools = orig

    check("走了两轮（工具轮 + DONE 轮）", len(calls) == 2, f"实际 {len(calls)} 轮")
    check("工具清单传给了模型（3 个工具）", calls[0]["n_tools"] == 3)
    trace = [t for t in ctx.tool_calls if t.get("node") == "tool_loop"]
    check("工具调用已留痕（node=tool_loop, ok=True）",
          len(trace) == 1 and trace[0]["ok"] is True
          and trace[0]["tool"] == "search_knowledge")
    check("证据块非空且带工具名",
          len(out["evidence"]) == 1 and "search_knowledge" in out["evidence"][0])
    check("检索结果带引用编号（compose 的 [1][2] 有出处）",
          bool(ctx.citations) and all("index" in c for c in ctx.citations),
          f"引用 {len(ctx.citations)} 条")
    check("证据同步写进了 ctx（compose 节点要用）",
          ctx.tool_loop_evidence == out["evidence"])

    print()
    if all(checks):
        print(f"全部 {len(checks)} 项通过 —— 工具循环接线正确")
        return 0
    print(f"{checks.count(False)} / {len(checks)} 项失败")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
