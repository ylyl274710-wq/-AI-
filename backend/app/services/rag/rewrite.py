"""Query Rewrite —— 把农户的口语症状转成能检索的表达（文档难点 5）。

原文档只说「先由 LLM 将口语症状转成检索关键词」，但没说 LLM 不可用时怎么办。
这里做成两级：
  1. 有模型时：LLM 输出 {"keywords":[...], "symptoms":[...], "crop":"...", "stage":"..."}
  2. 无模型时：本地口语词典 + jieba 分词兜底

本地词典是这一层的价值所在 —— 农户说的「叶子卷了」「叶背有小白点」在知识库里
根本不存在，必须映射到「卷叶」「白粉虱」这类标准表达。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import jieba

from app.core.config import settings
from app.services.llm import LLMUnavailable, llm

logger = logging.getLogger(__name__)

# 口语 → 专业表达映射。这张表是人工积累的，越用越准。
COLLOQUIAL_MAP: dict[str, list[str]] = {
    "叶子卷了": ["卷叶", "叶片卷曲"],
    "叶子卷": ["卷叶", "叶片卷曲"],
    "卷起来了": ["卷叶"],
    "叶背有小白点": ["白粉虱", "叶背", "白色斑点"],
    "小白点": ["白粉虱", "粉虱", "白色斑点"],
    "叶子发黄": ["黄化", "叶片发黄", "缺素"],
    "发黄": ["黄化", "缺素"],
    "烂了": ["腐烂", "软腐"],
    "烂根": ["根腐", "根系腐烂"],
    "长毛了": ["霉层", "霜霉", "灰霉"],
    "白毛": ["白粉病", "霜霉病", "菌丝"],
    "有虫": ["虫害", "害虫"],
    "小虫": ["蚜虫", "蓟马", "粉虱"],
    "绿色的小虫": ["蚜虫"],
    "打蔫": ["萎蔫", "凋萎"],
    "蔫了": ["萎蔫", "缺水", "根腐"],
    "枯了": ["枯死", "枯萎"],
    "有斑点": ["病斑", "斑点"],
    "黑斑": ["黑斑病", "病斑"],
    "掉果": ["落果", "果实脱落"],
    "裂果": ["裂果", "水分失调"],
    "不长": ["生长缓慢", "僵苗"],
    "畸形": ["畸形果", "病毒病"],
    "叶子有洞": ["孔洞", "虫咬", "咀嚼式口器"],
    "钻心": ["钻蛀", "螟虫"],
    "一坨一坨": ["聚集", "群集"],
    "发白": ["白化", "褪绿"],
    "发黑": ["黑变", "坏死"],
    # 通用农事口语（按「是否可跨作物复用」筛选后加入，评测集未命中驱动）
    "白霉": ["白色霉层", "霉层"],
    "小蛾子": ["粉虱", "蛾类害虫"],
    "一圈一圈": ["同心轮纹", "轮纹"],
    "轮纹": ["同心轮纹"],
    "密密麻麻": ["群集", "虫口密集"],
    "吸汁": ["刺吸式口器", "吸食汁液"],
    "开水烫过": ["水渍状", "烫伤状病斑"],
    "效果越来越差": ["抗药性", "药效下降"],
    "抗药": ["抗药性"],
    "黏糊糊": ["蜜露", "发黏"],
    "多少天": ["间隔期", "天数"],
    "摘果": ["采收", "采摘"],
}

# 与作物无关的填充词，检索前去掉
STOPWORDS = {
    "我", "的", "了", "是", "在", "有", "和", "跟", "这个", "那个", "怎么", "办",
    "怎么办", "为什么", "为什么呀", "请问", "一下", "现在", "今天", "最近", "好像",
    "感觉", "是不是", "要不要", "能不能", "什么", "啥", "呀", "啊", "呢", "吧",
    "这", "那", "个", "很", "好", "都", "还", "就", "也", "地", "得", "着",
}

_CJK = re.compile(r"[\u4e00-\u9fff]")


@dataclass
class RewrittenQuery:
    original: str
    keywords: list[str] = field(default_factory=list)
    expanded_terms: list[str] = field(default_factory=list)
    crop: str | None = None
    growth_stage: str | None = None
    symptom_text: str = ""
    by_llm: bool = False

    @property
    def search_text(self) -> str:
        """给向量检索用的文本：原句 + 扩展专业词。"""
        parts = [self.original]
        if self.expanded_terms:
            parts.append(" ".join(self.expanded_terms))
        return " ".join(parts)

    @property
    def keyword_text(self) -> str:
        """给关键词检索用的文本。"""
        return " ".join(self.keywords or self.expanded_terms) or self.original


def local_rewrite(query: str, known_crops: list[str] | None = None,
                  known_stages: list[str] | None = None,
                  crop_aliases: dict[str, str] | None = None) -> RewrittenQuery:
    """无模型时的本地改写：口语映射 + 分词 + 停用词过滤。

    crop_aliases：别名 → 标准名（如 西红柿→番茄、土豆→马铃薯）。
    农户口语里别名的使用频率往往高于学名；不映射的话有两个后果：
    rq.crop 识别不到（元数据过滤失效），且别名词在知识库里检索不到
    （库里写的是标准名）。识别到别名时按标准名落 crop，并把标准名
    补进扩展词 —— 这样两条检索通道都能吃到了。
    """
    text = (query or "").strip()
    expanded: list[str] = []

    # 1) 口语短语映射（长词优先，避免「叶子卷了」被「叶子」截断）
    for phrase in sorted(COLLOQUIAL_MAP, key=len, reverse=True):
        if phrase in text:
            expanded.extend(COLLOQUIAL_MAP[phrase])

    # 2) 识别作物与生长阶段（标准名优先，别名其次）
    crop = next((c for c in (known_crops or []) if c and c in text), None)
    if crop is None:
        for alias, std in sorted((crop_aliases or {}).items(),
                                 key=lambda kv: len(kv[0]), reverse=True):
            if alias and alias in text:
                crop = std
                expanded.append(std)
                break
    stage = next((s for s in (known_stages or []) if s and s in text), None)

    # 3) 分词取实词
    tokens = [t.strip() for t in jieba.lcut(text) if t.strip()]
    keywords = [t for t in tokens
                if t not in STOPWORDS and len(t) > 1 and _CJK.search(t)]
    keywords = list(dict.fromkeys(keywords + expanded))

    return RewrittenQuery(original=text, keywords=keywords,
                          expanded_terms=list(dict.fromkeys(expanded)),
                          crop=crop, growth_stage=stage, symptom_text=text)


_REWRITE_SCHEMA = """{
  "keywords": ["检索关键词，3~8个，用农业专业术语"],
  "symptoms": ["规范化后的症状描述"],
  "crop": "作物标准名，没有就填 null",
  "growth_stage": "生长阶段，没有就填 null"
}"""

_REWRITE_SYSTEM = """你是农业植保领域的检索助手。用户会用方言或口语描述作物异常。
你的任务是把口语转成农业专业检索词，不要回答问题本身。

要求：
1. keywords 必须是专业术语，例如「叶子卷了」→「卷叶」；「叶背有小白点」→「白粉虱 叶背」。
2. 不要把作物名放进 keywords，作物单独放 crop 字段。
3. 只输出 JSON，不要任何解释。"""


async def rewrite_query(query: str, known_crops: list[str] | None = None,
                        known_stages: list[str] | None = None,
                        crop_aliases: dict[str, str] | None = None) -> RewrittenQuery:
    """优先用 LLM 改写，失败或未配置 Key 时用本地词典兜底。"""
    base = local_rewrite(query, known_crops, known_stages, crop_aliases)
    if not settings.llm_enabled:
        return base
    hint = ""
    if known_crops:
        hint += f"\n可选作物标准名（只能从中选）: {'、'.join(known_crops[:60])}"
        if crop_aliases:
            hint += "\n常见别名同样按标准名填写: " + \
                    "、".join(f"{a}→{s}" for a, s in list(crop_aliases.items())[:60])
    if known_stages:
        hint += f"\n可选生长阶段: {'、'.join(known_stages[:30])}"
    try:
        data, _ = await llm.chat_json(
            [{"role": "system", "content": _REWRITE_SYSTEM + hint},
             {"role": "user", "content": query}],
            schema_hint=_REWRITE_SCHEMA, temperature=0.0, max_tokens=400)
    except LLMUnavailable as exc:
        logger.info("Query Rewrite 降级为本地词典: %s", exc)
        return base

    if not isinstance(data, dict):
        return base

    kws = [str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()]
    syms = [str(s).strip() for s in (data.get("symptoms") or []) if str(s).strip()]
    crop = data.get("crop") or None
    stage = data.get("growth_stage") or None
    if isinstance(crop, str) and crop.lower() in ("null", "none", ""):
        crop = None
    if isinstance(stage, str) and stage.lower() in ("null", "none", ""):
        stage = None
    # 作物必须落在已知集合内，避免模型自己造一个标准名出来
    if crop and known_crops and crop not in known_crops:
        crop = base.crop

    merged = list(dict.fromkeys(base.keywords + kws + syms))
    return RewrittenQuery(
        original=query, keywords=merged,
        expanded_terms=list(dict.fromkeys(base.expanded_terms + kws + syms)),
        crop=crop or base.crop, growth_stage=stage or base.growth_stage,
        symptom_text=" ".join(syms) or base.symptom_text, by_llm=True)
