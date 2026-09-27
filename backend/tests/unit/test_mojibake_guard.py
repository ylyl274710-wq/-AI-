"""入口乱码检测（GBK-as-Latin-1 特征）——2026-09-27 curl 污染事件的产品防线。"""
from app.api.v1.diagnosis import _mojibake_suspect


def test_gbk_mojibake_detected():
    # 「现在这段时间适合给番茄打药吗」的 GBK 字节按 Latin-1 解码后的样子
    assert _mojibake_suspect("ÏÖÔÚÕâ¶ÎÊ±¼äÊÊºÏ¸ø·¬ÇÑ´òÒ©Âð") is True


def test_normal_chinese_not_flagged():
    assert _mojibake_suspect("番茄叶子背面有一层白毛，像撒了面粉一样") is False


def test_empty_and_short():
    assert _mojibake_suspect("") is False
    assert _mojibake_suspect("Ã©") is False          # 单个合法拉丁字符不算
