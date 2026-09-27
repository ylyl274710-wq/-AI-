"""工具循环与多轮记忆的纯函数测试（CI 可跑，无基础设施）。"""
import pytest

from app.agent.prompts import HISTORY_MAX_MESSAGES, history_block
from app.agent.tool_loop import TOOL_SPECS, build_evidence_block, parse_tool_args


# ---------------- 多轮记忆：history_block ---------------- #
def test_history_block_empty():
    assert history_block([]) == ""
    assert history_block([{"role": "user", "content": "  "}]) == ""


def test_history_block_truncates_to_recent():
    hist = [{"role": "user", "content": f"问题{i}"} for i in range(20)]
    block = history_block(hist)
    assert "问题19" in block
    assert "问题3" not in block                     # 只留最近 8 条
    assert block.count("用户：") <= HISTORY_MAX_MESSAGES


def test_history_block_roles_and_order():
    hist = [{"role": "user", "content": "番茄叶子发黄怎么办"},
            {"role": "assistant", "content": "可能是早疫病"},
            {"role": "user", "content": "那辣椒呢"}]
    block = history_block(hist)
    lines = block.splitlines()
    assert lines[0] == "【对话历史（从旧到新）】"
    assert lines[1] == "用户：番茄叶子发黄怎么办"
    assert lines[-1] == "用户：那辣椒呢"            # 顺序：从旧到新，指代可解析


# ---------------- 工具循环：参数解析与证据块 ---------------- #
def test_parse_tool_args_valid():
    assert parse_tool_args('{"query": "白粉病 防治", "crop": "黄瓜"}') == \
        {"query": "白粉病 防治", "crop": "黄瓜"}


@pytest.mark.parametrize("bad", ["", "not json", "null", "[1,2]", '{"a":}', None])
def test_parse_tool_args_bad_json_returns_empty(bad):
    # 模型输出格式问题绝不能炸掉工具循环 —— 坏参数按全缺省执行
    assert parse_tool_args(bad) == {}


def test_build_evidence_block_empty_is_explicit():
    block = build_evidence_block([])
    assert "没有调用任何工具" in block               # 明示，而不是空串误判


def test_build_evidence_block_joins():
    block = build_evidence_block(["【get_farm_context】{...}",
                                  "【search_knowledge】{...}"])
    assert "【get_farm_context】" in block and "【search_knowledge】" in block


def test_tool_specs_well_formed():
    names = {t["function"]["name"] for t in TOOL_SPECS}
    assert {"search_knowledge", "get_weather_risk", "get_farm_context"} <= names
    for spec in TOOL_SPECS:
        fn = spec["function"]
        assert spec["type"] == "function"
        assert fn["description"], f"{fn['name']} 缺 description（模型靠它决定何时调用）"
        assert "type" in fn["parameters"]
