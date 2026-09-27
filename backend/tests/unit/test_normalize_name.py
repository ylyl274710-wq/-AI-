"""VLM 候选名规范化——「疑似」前缀导致规则引擎全拦的回归防线（D38）。"""
from app.agent.tool_impl import normalize_candidate_name


def test_strips_suspect_prefix():
    assert normalize_candidate_name("疑似苹果斑点落叶病") == "苹果斑点落叶病"


def test_strips_other_hedges():
    assert normalize_candidate_name("怀疑是炭疽病") == "炭疽病"
    assert normalize_candidate_name("可能是早疫病") == "早疫病"
    assert normalize_candidate_name("大概是锈病。") == "锈病"


def test_clean_name_untouched():
    assert normalize_candidate_name("苹果斑点落叶病") == "苹果斑点落叶病"


def test_empty():
    assert normalize_candidate_name("") == ""
    assert normalize_candidate_name(None) == ""
