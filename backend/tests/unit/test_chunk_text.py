"""切块器不变量（与 backend/scripts/check_chunker.py 同一套判据的 pytest 形态）。

两份判据必须保持一致：脚本是「改完手动跑」的快速自检，
这里是 CI 里每次提交自动跑的回归。谁改了判据，两边一起改。
"""
import pytest

from app.services.rag.chunking import chunk_text

SIZE = 420
OVERLAP = 60


def test_short_text_passthrough():
    short = "黄瓜霜霉病主要危害叶片。"
    assert chunk_text(short, size=SIZE, overlap=OVERLAP) == [short]


@pytest.mark.parametrize("bad", ["", "  \n  ", "\n\n"])
def test_empty_text(bad):
    assert chunk_text(bad, size=SIZE, overlap=OVERLAP) == []


def test_long_plain_splits_with_overlap():
    long_plain = "黄瓜霜霉病是设施黄瓜的第一大病害。" * 30  # ~480 字，无换行
    out = chunk_text(long_plain, size=SIZE, overlap=OVERLAP)
    assert len(out) >= 2
    assert all(len(c) <= SIZE for c in out)
    # 相邻块共享 overlap 尾巴，保证跨块语义连续
    shared = out[0][-OVERLAP:]
    assert out[1].startswith(shared) or shared in out[1]


def test_short_paragraph_not_split_mid_sentence():
    para = "第一句讲症状表现。第二句讲发生条件。第三句讲防治方法。"
    assert chunk_text(para, size=SIZE, overlap=OVERLAP) == [para]


def test_markdown_splits_by_heading():
    md = ("## 症状表现\n" + "危害叶片，出现多角形病斑。" * 20 + "\n\n"
          + "## 发生条件\n" + "高温高湿利于发病。" * 20 + "\n\n"
          + "## 防治方法\n" + "发病前喷保护性杀菌剂。" * 20)
    out = chunk_text(md, size=SIZE, overlap=OVERLAP)
    assert len(out) == 3
    assert all(c.startswith(("症状表现", "发生条件", "防治方法")) for c in out)
    # 节与节之间不串内容：检索命中「防治」不该拖出「症状」
    assert all("多角形病斑" not in c for c in out[1:])


def test_oversize_section_carries_heading_and_stays_in_budget():
    long_md = "## 防治方法\n" + "轮作倒茬并加强田间管理。" * 60
    out = chunk_text(long_md, size=SIZE, overlap=OVERLAP)
    assert len(out) >= 2
    assert all(len(c) <= SIZE for c in out)          # D32 修过的 bug：标题挤爆预算
    assert all(c.startswith("防治方法") for c in out)


def test_unpunctuated_long_text_hard_cut():
    hard = "黄瓜" * 400
    out = chunk_text(hard, size=SIZE, overlap=OVERLAP)
    assert len(out) >= 2
    assert all(len(c) <= SIZE for c in out)


def test_legacy_short_doc_unchanged():
    old_style = "叶片发黄是最常见的咨询问题，原因至少有以下几类。"
    assert chunk_text(old_style, size=SIZE, overlap=OVERLAP) == [old_style]
