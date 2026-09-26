"""置信度计算 —— 这是我对原文档改动最大的一处。

原文档（难点 1 + 第七章）说「设置置信度阈值；低于阈值时不进入具体用药流程」，
但没说置信度从哪来。如果直接用多模态大模型输出的「我觉得是番茄晚疫病，置信度 0.9」，
这个 0.9 是模型的措辞习惯，不是概率，**不可校准、不能当阈值用**。

本模块把置信度定义为三项可计算量的加权：

    margin     候选证据分差   —— 结论与最强竞争候选的差距（区分度）
    agreement  多图一致性     —— 多张照片是否指向同一个结论
    coverage   证据覆盖度     —— 结论自身拿到的证据强度

三个分量都能追溯到具体数据，可以复算、可以写进论文做消融实验。

★ 一个必须说明的工程细节：前两个分量都以「RRF 理论最高融合分」为分母
  （见 max_fused_score）。RRF 的原始分极小且排名间差异被 k 压缩，直接用
  原始分算分差会把 margin 结构性压到 0.01 以下，导致加权总分永远够不到
  阈值 —— 闸门 A 退化成「永远拦截」。归一化之后分量都落在 [0,1]，
  阈值与 k、通道数解耦，换配置不用重新调阈值。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import settings
from app.services.rag.retriever import RetrievalResult

# 拍摄角度枚举，用于「补充拍照」提示（文档难点 1）
SHOT_TYPES = ("leaf_front", "leaf_back", "whole_plant", "lesion_closeup")
SHOT_TYPE_LABELS = {
    "leaf_front": "叶片正面",
    "leaf_back": "叶片背面",
    "whole_plant": "整株全景",
    "lesion_closeup": "病斑近景",
}
# 单图情况下 agreement 的中性取值。不给 1.0 —— 单张照片没有交叉验证，
# 给满分等于把「没证据」当成「证据很强」。
SINGLE_IMAGE_AGREEMENT = 0.6
# 完全没有照片、只凭文字描述时的取值。比单图更低：文字描述的信息量
# 通常不如一张照片，而且农户描述症状时容易漏掉关键特征。
NO_IMAGE_AGREEMENT = 0.4
# 区分度低于该值视为「与竞争候选难以区分」
MARGIN_LOW = 0.2
# 证据强度低于理论最高值的该比例，视为证据偏薄
COVERAGE_THIN = 0.5
# ★ margin 的实际动态范围偏窄：RRF 本身是「按排名融合」的，当两个候选在
#   向量与关键词两条通道里都排前列时，融合分天然接近，margin 会落在
#   0.01~0.10；只有当两条通道判断分歧（一方双通道命中、另一方仅单通道）
#   时才会跳到 0.4~0.6。这不是 bug —— 两条通道都支持同一组候选时，
#   系统本来就应该说「分不清」。但调阈值时要意识到：
#   单图场景的总分主要由 agreement(0.6) 与 coverage 撑起，
#   margin 在「两候选都强」时贡献接近 0。若发现闸门过严，先动
#   CONFIDENCE_THRESHOLD，而不是去放大 CONFIDENCE_W_MARGIN。


def max_fused_score() -> float:
    """RRF 的理论最高融合分：假设某条知识在每条通道都排第一。

    ★ 为什么需要它：RRF 的原始分数量纲是 Σ weight/(k+rank)，
      k=60 时 rank1 只有 1/61≈0.0164，而且 rank1 与 rank2 的绝对差仅 1.6%。
      如果直接用原始分算「分差」，margin 会被结构性压到 0.01 以下，
      加权总分永远够不到阈值 —— 闸门 A 就变成了「永远拦截」。
      除以理论最高分后，两个分量都落在 [0,1] 且与 k、通道数无关，
      阈值可以跨配置复用，也便于写进论文做消融实验。
    """
    return ((settings.rag_vector_weight + settings.rag_keyword_weight)
            / (settings.rag_rrf_k + 1))


@dataclass
class ConfidenceBreakdown:
    score: float = 0.0
    margin: float = 0.0
    agreement: float = 0.0
    coverage: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)
    passed: bool = False
    rejected: bool = False
    reasons: list[str] = field(default_factory=list)
    missing_shots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 4),
            "margin": round(self.margin, 4),
            "agreement": round(self.agreement, 4),
            "coverage": round(self.coverage, 4),
            "weights": self.weights,
            "passed": self.passed,
            "rejected": self.rejected,
            "reasons": self.reasons,
            "missing_shots": self.missing_shots,
            "method": "weighted(margin, agreement, coverage) —— 非模型自报",
        }


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def candidate_evidence(retrieval: RetrievalResult) -> dict[str, float]:
    """按疾病名聚合检索证据分。

    fused_score 来自 RRF，量纲是 1/(k+rank)，所以同一结论命中越多 chunk，
    累计分越高 —— 这正好是我们想要的「证据强度」。
    """
    scores: dict[str, float] = {}
    for ch in retrieval.chunks:
        name = (ch.disease_name or "").strip()
        if not name:
            continue
        scores[name] = scores.get(name, 0.0) + ch.fused_score
    return scores


def compute_confidence(*, top1_name: str | None,
                       retrieval: RetrievalResult,
                       image_top1_names: list[str] | None = None,
                       shot_types: list[str] | None = None,
                       evidence: dict[str, float] | None = None) -> ConfidenceBreakdown:
    """计算置信度并给出闸门判定。"""
    bd = ConfidenceBreakdown(weights={
        "margin": settings.confidence_w_margin,
        "agreement": settings.confidence_w_agreement,
        "coverage": settings.confidence_w_coverage,
    })

    if not top1_name:
        bd.reasons.append("没有得到任何候选结论")
        bd.rejected = True
        return bd

    evidence = evidence if evidence is not None else candidate_evidence(retrieval)
    e_max = max_fused_score()

    # ---- 分量 1：候选证据分差（区分度）----
    # 以「结论本身」为基准去和最强的竞争候选比，而不是无脑取检索前两名 ——
    # 否则当结论不是检索第一名时，margin 会答非所问。
    ranked = sorted(evidence.items(), key=lambda kv: kv[1], reverse=True)
    e_own = evidence.get(top1_name, 0.0)
    e_rival = max((v for k, v in evidence.items() if k != top1_name), default=0.0)

    if e_own <= 0 and len(ranked) >= 2:
        # 结论名与知识库标准名没对齐（例如模型说「番茄白粉病」，而库里只有
        # 「黄瓜白粉病」）。退化为按检索前两名估算，并在 reasons 里点明，
        # 便于排查是不是名称归一化没做好。
        e_own, e_rival = ranked[0][1], ranked[1][1]
        bd.reasons.append(
            f"结论「{top1_name}」未与知识库条目直接对齐，区分度按检索前两名估算")

    if e_own > 0 and e_rival > 0:
        bd.margin = _clamp((e_own - e_rival) / e_max) if e_max > 0 else 0.0
        if bd.margin < MARGIN_LOW:
            bd.reasons.append(
                f"结论「{top1_name}」与竞争候选的证据强度接近"
                f"（区分度 {bd.margin:.2f}），难以区分")
    elif e_own > 0:
        # 只有单一候选有证据：没有对照，给中性值。用该候选自身强度做上界，
        # 避免把「没有对照」误判成「区分度极高」。
        bd.margin = _clamp(0.5 * e_own / e_max) if e_max > 0 else 0.0
        bd.reasons.append("知识库中只有单一候选有证据，缺少对照")
    else:
        bd.margin = 0.0
        bd.reasons.append("知识库中没有匹配到该结论的证据")

    # ---- 分量 2：多图一致性 ----
    names = [n for n in (image_top1_names or []) if n]
    # 照片张数取「视觉模型给出的结论数」与「实际上传的拍摄角度数」的较大者：
    # 没有视觉模型时前者为空，但用户确实上传了照片，不能当成没拍。
    n_images = max(len(names), len(shot_types or []))
    if len(names) >= 2:
        agree = sum(1 for n in names if n == top1_name) / len(names)
        bd.agreement = _clamp(agree)
        if agree < 1.0:
            bd.reasons.append(
                f"{len(names)} 张照片的结论不完全一致（一致率 {agree:.0%}），"
                "可能拍到了不同部位或不同问题")
    elif n_images == 0:
        bd.agreement = NO_IMAGE_AGREEMENT
        bd.reasons.append("没有上传照片，只能依据文字描述判断")
    else:
        bd.agreement = SINGLE_IMAGE_AGREEMENT
        bd.reasons.append("只有 1 张照片，缺少交叉验证")

    # ---- 分量 3：证据覆盖度（结论自身拿到的证据强度）----
    bd.coverage = _clamp(e_own / e_max) if e_max > 0 else 0.0
    hits = sum(1 for ch in retrieval.chunks
               if (ch.disease_name or "") == top1_name)
    if hits == 0:
        bd.reasons.append("检索结果中没有直接支持该结论的知识条目")
    elif bd.coverage < COVERAGE_THIN:
        bd.reasons.append(
            f"支持「{top1_name}」的证据强度仅为理论最高值的 "
            f"{bd.coverage:.0%}，证据偏薄")

    # ---- 加权汇总 ----
    bd.score = _clamp(
        bd.margin * settings.confidence_w_margin
        + bd.agreement * settings.confidence_w_agreement
        + bd.coverage * settings.confidence_w_coverage)

    bd.passed = bd.score >= settings.confidence_threshold
    bd.rejected = bd.score < settings.confidence_reject_threshold

    # ---- 给出该补拍什么 ----
    have = set(shot_types or [])
    bd.missing_shots = [s for s in SHOT_TYPES if s not in have]
    return bd


def supplementary_shot_hint(missing: list[str]) -> str:
    """把缺失的拍摄角度转成农户看得懂的话（文档难点 9：少术语）。"""
    if not missing:
        return "建议换一个角度再拍一张，或者把整株和病斑都拍上。"
    labels = "、".join(SHOT_TYPE_LABELS.get(m, m) for m in missing)
    return f"麻烦再补拍：{labels}。拍的时候对着光、靠近一点、手别抖。"
