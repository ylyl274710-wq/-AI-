"""分类器证据接入置信度的行为测试（D39：强确认抬升 / 强分歧保守 / 无声不变）。"""
from app.services.confidence import compute_confidence
from tests.unit._retrieval_stub import empty_retrieval


def _run(**kw):
    return compute_confidence(
        top1_name="苹果黑星病", retrieval=empty_retrieval(),
        shot_types=["leaf_front"], **kw)


def test_classifier_confirms_boosts_score():
    bd = _run(classifier_top={"name": "苹果黑星病", "prob": 1.0, "second_prob": 0.0})
    assert bd.passed, f"score={bd.score}"
    assert bd.agreement == 1.0 and bd.coverage == 1.0 and bd.margin == 1.0
    assert any("分类器" in r for r in bd.reasons)


def test_classifier_disagreement_is_conservative():
    bd = _run(classifier_top={"name": "苹果白粉病", "prob": 0.95, "second_prob": 0.0})
    assert bd.agreement <= 0.3
    assert not bd.passed
    assert any("不一致" in r for r in bd.reasons)


def test_weak_classifier_signal_changes_nothing():
    base = _run()
    weak = _run(classifier_top={"name": "苹果黑星病", "prob": 0.3, "second_prob": 0.1})
    assert base.score == weak.score
    assert base.agreement == weak.agreement
