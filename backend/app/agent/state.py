"""Agent 状态定义与运行时上下文。

原文档难点 6 推荐流程：Intent → Context → Diagnosis → Retrieval → Rule Check →
Response。本项目把它扩成可执行的 8 个节点，并补上文档没写但必须有的两个终止分支
（等用户补充拍照 / 判定无法识别）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentState(str, Enum):
    # ---- 主链路 ----
    INTENT = "intent"
    CONTEXT = "context"
    DIAGNOSE = "diagnose"
    RETRIEVE = "retrieve"
    TOOL_LOOP = "tool_loop"
    RULE_CHECK = "rule_check"
    COMPOSE = "compose"
    PERSIST = "persist"
    REMIND = "remind"
    # ---- 终止态 ----
    DONE = "done"
    NEEDS_MORE_INFO = "needs_more_info"
    REJECTED = "rejected"
    FAILED = "failed"


TERMINAL_STATES = {AgentState.DONE, AgentState.NEEDS_MORE_INFO,
                   AgentState.REJECTED, AgentState.FAILED}

# 主链路顺序（用于兜底推进，避免某个节点返回了非法状态时卡死）
MAIN_CHAIN = [AgentState.INTENT, AgentState.CONTEXT, AgentState.DIAGNOSE,
              AgentState.RETRIEVE, AgentState.RULE_CHECK, AgentState.COMPOSE,
              AgentState.PERSIST, AgentState.REMIND, AgentState.DONE]

# 意图 → 需要执行的节点子集。不是每个问题都要走全流程：
# 「叶子发黄是怎么回事」不需要农药规则校验；「这个药能不能用」则需要。
#
# consult 走 TOOL_LOOP 而不是固定 RETRIEVE：咨询是开放问题（「现在适合打药吗」
# 可能需要天气、地块、知识库三种信息），让 LLM 自己决定调什么工具、调几轮；
# 诊断保持固定管线（确定性优先）。两种形态并存是有意的设计对照。
INTENT_ROUTES: dict[str, list[AgentState]] = {
    "diagnosis": [AgentState.INTENT, AgentState.CONTEXT, AgentState.DIAGNOSE,
                  AgentState.RETRIEVE, AgentState.RULE_CHECK, AgentState.COMPOSE,
                  AgentState.PERSIST, AgentState.REMIND, AgentState.DONE],
    "consult": [AgentState.INTENT, AgentState.CONTEXT, AgentState.TOOL_LOOP,
                AgentState.COMPOSE, AgentState.PERSIST, AgentState.DONE],
    "pesticide": [AgentState.INTENT, AgentState.CONTEXT, AgentState.RETRIEVE,
                  AgentState.RULE_CHECK, AgentState.COMPOSE, AgentState.PERSIST,
                  AgentState.DONE],
    "farm_analysis": [AgentState.INTENT, AgentState.CONTEXT, AgentState.RETRIEVE,
                      AgentState.RULE_CHECK, AgentState.COMPOSE,
                      AgentState.PERSIST, AgentState.REMIND, AgentState.DONE],
    "task": [AgentState.INTENT, AgentState.CONTEXT, AgentState.COMPOSE,
             AgentState.PERSIST, AgentState.REMIND, AgentState.DONE],
}

INTENT_LABELS = {
    "diagnosis": "病虫害诊断",
    "consult": "农业咨询",
    "pesticide": "用药查询",
    "farm_analysis": "农情分析",
    "task": "农事提醒",
}


@dataclass
class TraceStep:
    node: str
    status: str = "ok"          # ok / skipped / retried / error / blocked
    started_at: float = field(default_factory=time.time)
    elapsed_ms: int = 0
    detail: str = ""
    tools: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"node": self.node, "status": self.status,
                "elapsed_ms": self.elapsed_ms, "detail": self.detail,
                "tools": self.tools}


@dataclass
class AgentContext:
    """整个状态机共享的上下文。

    所有节点只读写这个对象，不直接互相调用 —— 这样每个节点都可以单测，
    也方便把整条执行轨迹落库（chat_messages.trace）。
    """
    # ---- 输入 ----
    user_id: int | None = None
    session_id: str = ""
    query: str = ""
    images: list[bytes] = field(default_factory=list)
    image_shot_types: list[str] = field(default_factory=list)
    crop_name: str | None = None
    field_id: int | None = None
    growth_stage: str | None = None
    days_to_harvest: int | None = None

    # ---- 运行时 ----
    state: AgentState = AgentState.INTENT
    intent: str = "consult"
    trace: list[TraceStep] = field(default_factory=list)
    # 每次工具调用记一笔：{node, tool, ok, elapsed_ms, ...}。
    # 由 ToolRegistry.invoke 追加。回答要能追溯到「哪一步查了什么」，
    # 这是文档难点 6（Agent 失控）与难点 13（结果无法验证）的落地手段。
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    degraded: bool = False
    steps: int = 0

    # ---- 多轮记忆：本会话最近几轮对话（run 开始时加载，只读） ----
    history: list[dict[str, str]] = field(default_factory=list)
    # ---- token 流式：SSE 路由注入的队列，compose 产出 delta 时推送；
    #      一次性路由不注入（None），compose 走原同步路径 ----
    stream_queue: Any = None
    # ---- 工具循环产物：LLM 自主调用工具收集到的证据块（consult 路径） ----
    tool_loop_evidence: list[str] = field(default_factory=list)

    # ---- 各节点产物 ----
    user_context: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    image_top1_names: list[str] = field(default_factory=list)
    retrieval: Any = None                 # RetrievalResult
    retrieval_result: dict[str, Any] = field(default_factory=dict)
    confidence: Any = None                # ConfidenceBreakdown
    rule_result: Any = None               # RuleEngineResult
    risk: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    answer_sections: dict[str, str] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    tasks_created: list[int] = field(default_factory=list)
    diagnosis_id: int | None = None
    guard_info: dict[str, Any] = field(default_factory=dict)

    # ---- 输出 ----
    llm_model: str | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    def add_trace(self, node: str, *, status: str = "ok", detail: str = "",
                  tools: list[str] | None = None,
                  elapsed_ms: int = 0) -> None:
        self.trace.append(TraceStep(node=node, status=status, detail=detail,
                                    tools=tools or [], elapsed_ms=elapsed_ms))

    @property
    def has_images(self) -> bool:
        return bool(self.images)

    def trace_dicts(self) -> list[dict]:
        return [t.to_dict() for t in self.trace]
