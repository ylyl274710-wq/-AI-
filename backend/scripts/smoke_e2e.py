#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端冒烟：直接驱动有限状态 Agent，不经过 HTTP 层。

覆盖四类意图 × 有/无视觉模型 × 有/无 LLM 的组合，
重点看「没有 Key 时能不能诚实地降级」，而不是假装能看图。

用法（在 backend/ 下）：
    python scripts/smoke_e2e.py
    python scripts/smoke_e2e.py --intent diagnose
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.agent.fsm import AgriAgent          # noqa: E402
from app.agent.state import AgentContext     # noqa: E402
from app.core.config import settings         # noqa: E402
from app.core.db import SessionLocal         # noqa: E402
from app.models.user import User             # noqa: E402
from sqlalchemy import select                # noqa: E402


def banner(t: str) -> None:
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


def show(ctx: AgentContext) -> None:
    print(f"  终态      : {ctx.state.value}")
    print(f"  意图      : {ctx.intent}")
    print(f"  步数      : {ctx.steps}")
    print(f"  degraded  : {ctx.degraded}")
    if ctx.errors:
        print(f"  错误      : {ctx.errors}")
    print(f"  轨迹      : {' -> '.join(s.node for s in ctx.trace)}")
    if ctx.confidence is not None:
        c = ctx.confidence
        print(f"  置信度    : total={c.score:.4f} margin={c.margin:.4f} "
              f"agreement={c.agreement:.4f} coverage={c.coverage:.4f} "
              f"passed={c.passed} rejected={c.rejected}")
    if ctx.rule_result is not None:
        r = ctx.rule_result
        print(f"  规则      : hard_blocked={r.hard_blocked} "
              f"checked={r.checked} 通过={len(r.approved)} 拦截={len(r.blocked)}")
        if r.approved:
            print("              放行: "
                  + "、".join(c.product_name for c in r.approved[:5]))
        if r.blocked:
            from collections import Counter
            cnt = Counter(x for c in r.blocked for x in (c.blocked_reasons or ["?"]))
            print("              拦截原因分布: "
                  + "、".join(f"{k}×{v}" for k, v in cnt.most_common()))
        warns = [(c.product_name, w) for c in (r.approved + r.blocked)
                 for w in (c.warnings or [])]
        if warns:
            from collections import Counter
            wc = Counter(w for _, w in warns)
            print("              警告分布: "
                  + "、".join(f"{k}×{v}" for k, v in wc.most_common()))
        if r.message:
            print(f"              提示: {r.message[:200]}")
    if ctx.guard_info:
        print(f"  护栏      : {ctx.guard_info}")
    if ctx.tasks_created:
        print(f"  建任务    : {ctx.tasks_created}")
    if ctx.diagnosis_id:
        print(f"  诊断记录  : id={ctx.diagnosis_id}")
    if ctx.answer:
        print("  ---- 回答 ----")
        for line in ctx.answer.splitlines():
            print(f"  | {line}")
    else:
        print("  ---- 无回答 ----")


async def run_case(db, user: User, title: str, **kw) -> AgentContext:
    banner(title)
    ctx = AgentContext(user_id=user.id, session_id="smoke", **kw)
    agent = AgriAgent(db)
    await agent.run(ctx)
    db.commit()
    show(ctx)
    return ctx


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intent", default="all")
    a = ap.parse_args()

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == "demo"))
        if user is None:
            sys.exit("找不到演示用户，先跑 python -m app.seed.runner")

        print(f"LLM 可用 : {settings.llm_enabled}   "
              f"视觉可用 : {settings.vision_enabled}")
        print(f"Embedding: {settings.embedding_provider} / "
              f"{settings.embedding_model}")

        want = a.intent

        if want in ("all", "diagnose"):
            # 1) 文字描述诊断（无图片）：应走文字路径而不是假装看图
            await run_case(db, user,
                           "CASE 1  诊断 · 仅文字描述（无图片）",
                           query="番茄叶子背面有一层白霜一样的东西，叶子开始发黄",
                           crop_name="番茄", growth_stage="结果期")

            # 2) 文字描述 + 明确说出病名
            await run_case(db, user,
                           "CASE 2  诊断 · 文字提到明确症状",
                           query="大棚番茄叶子背面出现白色粉末状霉层，"
                                 "是不是白粉病？",
                           crop_name="番茄", growth_stage="结果期")

            # 3) 无药可治的对象（病毒病）→ 应触发闸门 B
            await run_case(db, user,
                           "CASE 3  诊断 · 病毒病（无登记农药，应触发闸门 B）",
                           query="番茄叶子皱缩卷曲，叶片变小发黄，植株矮化",
                           crop_name="番茄")

            # 4) 抗性演示：番茄 + FRAC 11 已用过两次
            await run_case(db, user,
                           "CASE 4  诊断 · 应触发抗性提醒（FRAC 11 已用 2 次）",
                           query="番茄叶片上有褐色轮纹病斑，下部叶片先发病",
                           crop_name="番茄", growth_stage="结果期")

        if want in ("all", "consult"):
            await run_case(db, user,
                           "CASE 5  咨询 · 通用问题",
                           query="黄瓜叶子发黄是什么原因？",
                           crop_name="黄瓜")

        if want in ("all", "remind"):
            await run_case(db, user,
                           "CASE 6  提醒 · 创建农事任务",
                           query="3 天后提醒我下地看看番茄叶子背面",
                           crop_name="番茄")

        if want in ("all", "weather"):
            await run_case(db, user,
                           "CASE 7  天气风险",
                           query="这两天天气怎么样，需要注意什么？",
                           crop_name="番茄", field_id=1)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
