"""本地查询改写（local_rewrite）的单元测试：别名映射、口语词典、停用词。

覆盖 2026-09-26 修的两个真 bug 的回归：
  - 别名不入映射 → 作物过滤失效 + 别名词检索不到（D32）；
  - 「多少天」「摘果」无专业词 → 安全间隔期题命中不了（D32 评测题 34）。
"""
from app.services.rag.rewrite import local_rewrite

CROPS = ["番茄", "小麦", "黄瓜", "马铃薯"]
ALIASES = {"西红柿": "番茄", "土豆": "马铃薯", "麦子": "小麦"}


def test_alias_maps_to_standard_crop_and_expands():
    rq = local_rewrite("西红柿叶子上全是小白蛾子",
                       known_crops=CROPS, crop_aliases=ALIASES)
    assert rq.crop == "番茄"            # 元数据过滤用的是标准名，不是别名
    assert "番茄" in rq.expanded_terms  # 别名转成标准名补进检索词


def test_standard_name_beats_alias():
    rq = local_rewrite("番茄晚疫病用什么药",
                       known_crops=CROPS, crop_aliases=ALIASES)
    assert rq.crop == "番茄"
    # 命中标准名时不应再走别名分支重复扩展
    assert rq.expanded_terms.count("番茄") <= 1


def test_colloquial_map_expands_professional_terms():
    rq = local_rewrite("叶子卷了还发黄，是缺水吗", known_crops=CROPS)
    assert "卷叶" in rq.expanded_terms
    assert "黄化" in rq.expanded_terms


def test_interval_and_harvest_colloquial():
    rq = local_rewrite("打了药之后多少天才能摘果子卖", known_crops=CROPS)
    assert "间隔期" in rq.expanded_terms
    assert "采收" in rq.expanded_terms


def test_stopwords_removed_from_keywords():
    rq = local_rewrite("我的黄瓜叶子怎么办", known_crops=CROPS)
    for stop in ("我", "的", "怎么办"):
        assert stop not in rq.keywords


def test_search_text_carries_expansion_for_vector_channel():
    rq = local_rewrite("黄瓜叶背有小白点", known_crops=CROPS)
    assert "白粉虱" in rq.search_text  # search_text = 原句 + 扩展专业词
    assert "白粉虱" in rq.keyword_text  # keyword 通道同样吃到扩展词


def test_unknown_crop_left_none():
    rq = local_rewrite("这草长得太快了", known_crops=CROPS, crop_aliases=ALIASES)
    assert rq.crop is None
