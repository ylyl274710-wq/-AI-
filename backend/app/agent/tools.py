"""Agent 工具注册表与「每个节点允许调用的工具」策略（文档难点 6）。

原文档要求：「每个节点明确输入、输出和允许调用的工具」。这里把它做成硬约束：
节点调工具必须走 `registry.invoke(state, name, ctx)`，如果该工具不在当前节点的
允许清单里，直接抛 ToolNotAllowed —— 而不是「Prompt 里提醒模型别乱调」。
Prompt 是建议，代码才是约束。

另外每个工具有独立的超时与重试上限，防止某个外部依赖卡住整条链路。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.agent.state import AgentContext, AgentState

logger = logging.getLogger(__name__)

ToolFn = Callable[..., Awaitable[Any]]


class ToolNotAllowed(RuntimeError):
    pass


class ToolTimeout(RuntimeError):
    pass


@dataclass
class ToolSpec:
    name: str
    description: str
    handler: ToolFn
    timeout_seconds: float = 20.0
    max_retries: int = 1
    # 高风险工具（涉及用药）必须经过规则节点，不允许在别的节点被调用
    high_risk: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._policy: dict[AgentState, set[str]] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def set_policy(self, policy: dict[AgentState, list[str]]) -> None:
        self._policy = {s: set(names) for s, names in policy.items()}

    def allowed(self, state: AgentState) -> set[str]:
        return self._policy.get(state, set())

    def describe(self) -> list[dict]:
        return [{"name": s.name, "description": s.description,
                 "high_risk": s.high_risk,
                 "allowed_states": sorted(
                     st.value for st, names in self._policy.items()
                     if s.name in names)}
                for s in self._tools.values()]

    async def invoke(self, state: AgentState, name: str, ctx: AgentContext,
                     **kwargs) -> Any:
        spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"未注册的工具: {name}")
        if name not in self.allowed(state):
            # 越权调用属于「Agent 失控」的直接证据，必须留痕再抛
            ctx.tool_calls.append({
                "node": state.value, "tool": name, "ok": False,
                "error": "not_allowed", "elapsed_ms": 0,
            })
            raise ToolNotAllowed(
                f"节点 {state.value} 不允许调用工具 {name}"
                f"（允许: {sorted(self.allowed(state))}）")

        last_err: Exception | None = None
        for attempt in range(spec.max_retries + 1):
            t0 = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    spec.handler(ctx=ctx, **kwargs), timeout=spec.timeout_seconds)
                ms = int((time.monotonic() - t0) * 1000)
                logger.info("tool %s ok %.0fms", name, ms)
                # ★ 每次工具调用都记一笔：节点 / 工具名 / 耗时 / 是否成功。
                #   没有这张表，排查「为什么给出这个回答」只能靠猜；
                #   有了它，回答可以追溯到具体某次检索、某次规则校验。
                ctx.tool_calls.append({
                    "node": state.value, "tool": name, "ok": True,
                    "elapsed_ms": ms, "attempt": attempt + 1,
                    "high_risk": spec.high_risk,
                })
                return result
            except asyncio.TimeoutError as exc:
                last_err = ToolTimeout(f"工具 {name} 超时（{spec.timeout_seconds}s）")
                logger.warning("tool %s 超时 attempt=%d", name, attempt)
                del exc
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logger.warning("tool %s 失败 attempt=%d: %s", name, attempt, exc)
            if attempt < spec.max_retries:
                await asyncio.sleep(0.4 * (2 ** attempt))

        ctx.tool_calls.append({
            "node": state.value, "tool": name, "ok": False,
            "error": "工具执行失败", "elapsed_ms": 0,
            "attempts": spec.max_retries + 1, "high_risk": spec.high_risk,
        })
        raise last_err if last_err else RuntimeError(f"工具 {name} 调用失败")


registry = ToolRegistry()

# ---------------------------------------------------------------------- #
# 工具 → 允许的节点。这张表就是「有限状态」的具体体现：
# 高风险工具 check_pesticide_rules 只在 RULE_CHECK 节点可用，
# 任何别的节点想调它都会直接抛异常。
TOOL_POLICY: dict[AgentState, list[str]] = {
    AgentState.CONTEXT: ["load_farm_context", "load_usage_history"],
    AgentState.DIAGNOSE: ["vision_diagnose"],
    AgentState.RETRIEVE: ["retrieve_knowledge", "rewrite_query"],
    AgentState.RULE_CHECK: ["check_pesticide_rules", "search_pesticide_products"],
    AgentState.COMPOSE: [],
    AgentState.PERSIST: ["save_diagnosis", "save_usage_record"],
    AgentState.REMIND: ["create_farm_task", "list_farm_tasks"],
}

registry.set_policy(TOOL_POLICY)

__all__ = ["registry", "ToolSpec", "ToolNotAllowed", "ToolTimeout", "TOOL_POLICY",
           "ToolRegistry"]
