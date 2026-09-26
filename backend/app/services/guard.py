"""输出层安全护栏。

原文档第七章只写了「农药规则不匹配 → 不推荐该产品」，但没有防住另一种情况：
规则节点明明没放行，LLM 却在自由行文里「顺便」说出了剂量。
Prompt 里写「禁止编造剂量」是约束，不是保证；输出层必须有一道机械的兜底。

本模块做三件事：
  1. 剂量正则拦截 —— 命中即从回答里剔除该句，并记录拦截日志；
  2. 白名单外产品名检测 —— 回答里出现了不在白名单的农药名，标红提示；
  3. 免责声明强制注入 —— 保证任何含农药内容的回答都带提示。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.core.config import settings

logger = logging.getLogger(__name__)

# 剂量/浓度表达。覆盖农户常见写法与标签常见写法。
DOSE_PATTERNS: list[tuple[str, str]] = [
    (r"\d+(?:\.\d+)?\s*[-~至]\s*\d+(?:\.\d+)?\s*(?:克|g|G|毫升|ml|ML|mL)\s*/\s*(?:亩|公顷|hm2|升水)", "每亩用量"),
    (r"\d+(?:\.\d+)?\s*(?:克|g|G|千克|kg|毫升|ml|ML|mL|升|L)\s*/\s*(?:亩|公顷|hm2)", "每亩用量"),
    (r"每\s*(?:亩|公顷)\s*(?:用|施用|喷施)?\s*\d+(?:\.\d+)?\s*(?:克|g|千克|kg|毫升|ml|升|L)", "每亩用量"),
    (r"稀释\s*\d+(?:\.\d+)?\s*[-~至]?\s*\d*\s*倍", "稀释倍数"),
    (r"\d+(?:\.\d+)?\s*[-~至]\s*\d+(?:\.\d+)?\s*倍\s*液?", "稀释倍数"),
    (r"\d+(?:\.\d+)?\s*(?:ppm|PPm|PPM|毫克/升|mg/L)", "浓度"),
    (r"兑水\s*\d+(?:\.\d+)?\s*(?:斤|公斤|千克|升|L|毫升|ml)", "兑水量"),
    (r"(?:用量|剂量|浓度)\s*(?:为|是|约|大约)?\s*\d+(?:\.\d+)?", "剂量表述"),
    (r"每亩\s*\d+(?:\.\d+)?\s*(?:克|g|毫升|ml)", "每亩用量"),
]

DOSE_RE = [re.compile(p) for p, _ in DOSE_PATTERNS]

DISCLAIMER = ("农药使用请以**当前产品标签和说明书**为准，并结合当地农业技术"
              "部门的指导。不同作物、不同生长阶段的用药要求不同，"
              "本回答仅作参考，不替代专业人员现场判断。")

_SENT_SPLIT = re.compile(r"(?<=[。！？；\n])")


@dataclass
class GuardResult:
    text: str
    blocked_sentences: list[str] = field(default_factory=list)
    matched_patterns: list[str] = field(default_factory=list)
    unknown_products: list[str] = field(default_factory=list)
    disclaimer_added: bool = False

    @property
    def has_pesticide_content(self) -> bool:
        return bool(self.blocked_sentences or self.unknown_products)

    def to_dict(self) -> dict:
        return {
            "blocked_sentences": self.blocked_sentences,
            "matched_patterns": self.matched_patterns,
            "unknown_products": self.unknown_products,
            "disclaimer_added": self.disclaimer_added,
        }


def find_dose_spans(text: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for pattern, label in zip(DOSE_RE, [l for _, l in DOSE_PATTERNS]):
        for m in pattern.finditer(text or ""):
            hits.append((m.group(0), label))
    return hits


def strip_doses(text: str) -> tuple[str, list[str], list[str]]:
    """逐句剔除含剂量的句子。

    逐句而不是整段删除：一段话里通常只有一句含剂量，
    整段删掉会把有效信息也一起删了，用户体验更差。
    """
    if not text:
        return text, [], []
    sentences = [s for s in _SENT_SPLIT.split(text) if s]
    kept: list[str] = []
    blocked: list[str] = []
    labels: list[str] = []
    for s in sentences:
        hits = find_dose_spans(s)
        if hits:
            blocked.append(s.strip())
            labels.extend(lbl for _, lbl in hits)
        else:
            kept.append(s)
    out = "".join(kept).strip()
    if blocked:
        out += ("\n\n> 出于安全考虑，本条回答中涉及具体用量/浓度的表述已移除。"
                "具体用量请查看产品标签。")
    return out, blocked, list(dict.fromkeys(labels))


# 明显不是农药商品名的词。中文没有词边界，光靠「XX素 / XX酯」这种命名模式
# 必然误伤 —— 实测「这与缺素引起的黄化不同」里的「这与缺素」被当成了商品名，
# 会在回答里弹出一个吓人的「检测到未知农药」提示。
# 这里列的是高频误报词根：缺素/激素/维生素这类「素」结尾的普通名词。
_NOT_PRODUCT_WORDS = (
    "缺素", "元素", "激素", "维生素", "毒素", "色素", "尿素", "叶绿素",
    "生长素", "青霉素", "链霉素", "胰岛素", "纤维素", "木质素", "角质素",
    "生理", "药害", "肥害", "抗性", "残留",
)


def detect_unknown_products(text: str, whitelist: list[str]) -> list[str]:
    """检测回答里出现的、不在白名单中的农药商品名。

    只做粗筛（命中「XX 灵 / XX 净 / XX 酯 / XX 素」这类命名模式且不在白名单里），
    目的是给出提示而不是判罪 —— 中文农药商品名没有可靠的正则边界。
    因此必须配一份非产品词表压误报，否则「缺素」「维生素」都会被当成农药。
    """
    if not text:
        return []
    candidates = re.findall(
        r"[\u4e00-\u9fff]{2,6}(?:灵|净|酯|素|乳油|悬浮剂|可湿性粉剂)", text)
    wl = set(whitelist or [])
    out: list[str] = []
    for c in dict.fromkeys(candidates):
        if c in wl:
            continue
        if any(bad in c for bad in _NOT_PRODUCT_WORDS):
            continue
        out.append(c)
    return out


def apply_output_guard(text: str, *, whitelist_products: list[str] | None = None,
                       pesticide_involved: bool = False) -> GuardResult:
    res = GuardResult(text=text or "")
    if settings.dose_guard_enabled:
        res.text, res.blocked_sentences, res.matched_patterns = strip_doses(res.text)
    res.unknown_products = detect_unknown_products(res.text, whitelist_products or [])
    if pesticide_involved or res.has_pesticide_content:
        if DISCLAIMER not in res.text:
            res.text = res.text.rstrip() + "\n\n" + DISCLAIMER
            res.disclaimer_added = True
    if res.blocked_sentences:
        logger.warning("输出护栏拦截剂量表述 %d 处: %s",
                       len(res.blocked_sentences), res.matched_patterns)
    return res
