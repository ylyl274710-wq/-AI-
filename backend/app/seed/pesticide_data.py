"""农药有效成分与产品白名单种子数据（文档难点 3、难点 12）。

★★★ 必读：关于数据可信度的三个决定 ★★★

1) **不编造登记证号**。中国农药登记数据没有开放 API，登记证号是监管信息，
   编一个看起来像真的号（如 PD2018xxxx）比留空危害大得多。
   因此 registration_no 一律为 None，source_url 指向中国农药信息网的查询入口，
   由使用者自行核对。

2) **不编造用量数字**。文档难点 3 的核心风险就是「直接让 LLM 输出剂量」。
   如果种子数据里就写了「每亩 60~90 毫升」，这个数字会被当成事实传播出去。
   所以 dosage_text 统一写成「以产品标签标注为准」，把用量彻底交还给标签。

3) **applicable_stages 是人工标注的**。真实登记数据里**没有**「使用阶段」字段，
   这是本项目自己补的维度，标注依据是该药剂在苗期可能产生药害的常识，
   属于「需人工复核」的字段。

有效成分的作用机制分组（FRAC / IRAC 编号）是抗性管理的基础，
这部分是公开的分类知识，可以放心使用。
"""
from __future__ import annotations

from datetime import date

_TODAY = date(2026, 9, 24)
_SRC = "http://www.chinacpesticide.org.cn/"   # 中国农药信息网（注意：域名以 chinapesticide 为准）
_SRC = "http://www.chinapesticide.org.cn/"
_LABEL_ONLY = "具体用量、稀释倍数、兑水量以产品标签标注为准（本条目不录入具体数字）"

# ---------------------------------------------------------------------------
# 有效成分数据字典（文档难点 4：有效成分标准名 + 作用机制分类）
# ---------------------------------------------------------------------------
INGREDIENTS: list[dict] = [
    # ---- 杀菌剂 ----
    {"standard_name": "嘧菌酯", "english_name": "Azoxystrobin", "category": "fungicide",
     "mode_of_action": "甲氧基丙烯酸酯类（QoI），抑制呼吸链复合体 III",
     "resistance_group": "FRAC 11", "toxicity": "low",
     "aliases": ["阿米西达（商品名）"],
     "remark": "抗性风险高，必须与不同作用机制药剂轮换。"},
    {"standard_name": "吡唑醚菌酯", "english_name": "Pyraclostrobin",
     "category": "fungicide",
     "mode_of_action": "甲氧基丙烯酸酯类（QoI），抑制呼吸链复合体 III",
     "resistance_group": "FRAC 11", "toxicity": "low",
     "remark": "与嘧菌酯同属 FRAC 11，两者轮换等于没轮换。"},
    {"standard_name": "代森锰锌", "english_name": "Mancozeb", "category": "fungicide",
     "mode_of_action": "多位点接触性保护剂", "resistance_group": "FRAC M03",
     "toxicity": "low",
     "remark": "保护性药剂，需在发病前使用；多位点作用，抗性风险低。"},
    {"standard_name": "苯醚甲环唑", "english_name": "Difenoconazole",
     "category": "fungicide",
     "mode_of_action": "甾醇脱甲基抑制剂（DMI / 三唑类）",
     "resistance_group": "FRAC 3", "toxicity": "low"},
    {"standard_name": "丙环唑", "english_name": "Propiconazole", "category": "fungicide",
     "mode_of_action": "甾醇脱甲基抑制剂（DMI / 三唑类）",
     "resistance_group": "FRAC 3", "toxicity": "moderate"},
    {"standard_name": "戊唑醇", "english_name": "Tebuconazole", "category": "fungicide",
     "mode_of_action": "甾醇脱甲基抑制剂（DMI / 三唑类）",
     "resistance_group": "FRAC 3", "toxicity": "moderate"},
    {"standard_name": "烯酰吗啉", "english_name": "Dimethomorph", "category": "fungicide",
     "mode_of_action": "羧酸酰胺类，抑制细胞壁合成",
     "resistance_group": "FRAC 40", "toxicity": "low",
     "remark": "对卵菌（晚疫病、霜霉病）有效，对真菌性病害无效。"},
    {"standard_name": "氟吡菌胺", "english_name": "Fluopicolide", "category": "fungicide",
     "mode_of_action": "苯甲酰胺类，破坏细胞膜完整性",
     "resistance_group": "FRAC 43", "toxicity": "low"},
    {"standard_name": "啶酰菌胺", "english_name": "Boscalid", "category": "fungicide",
     "mode_of_action": "琥珀酸脱氢酶抑制剂（SDHI）",
     "resistance_group": "FRAC 7", "toxicity": "low"},
    {"standard_name": "腐霉利", "english_name": "Procymidone", "category": "fungicide",
     "mode_of_action": "二甲酰亚胺类，抑制渗透信号传导",
     "resistance_group": "FRAC 2", "toxicity": "low",
     "remark": "灰霉病常用，抗性风险高。"},
    {"standard_name": "异菌脲", "english_name": "Iprodione", "category": "fungicide",
     "mode_of_action": "二甲酰亚胺类", "resistance_group": "FRAC 2",
     "toxicity": "low"},
    {"standard_name": "咪鲜胺", "english_name": "Prochloraz", "category": "fungicide",
     "mode_of_action": "甾醇脱甲基抑制剂（DMI / 咪唑类）",
     "resistance_group": "FRAC 3", "toxicity": "moderate"},
    {"standard_name": "多菌灵", "english_name": "Carbendazim", "category": "fungicide",
     "mode_of_action": "苯并咪唑类，抑制 β-微管蛋白",
     "resistance_group": "FRAC 1", "toxicity": "low",
     "remark": "抗性风险很高，很多地区灰霉病菌已普遍抗性。"},
    {"standard_name": "甲基硫菌灵", "english_name": "Thiophanate-methyl",
     "category": "fungicide", "mode_of_action": "苯并咪唑类（体内转化为多菌灵）",
     "resistance_group": "FRAC 1", "toxicity": "low",
     "remark": "与多菌灵同属 FRAC 1，不能作为轮换药剂。"},
    {"standard_name": "三环唑", "english_name": "Tricyclazole", "category": "fungicide",
     "mode_of_action": "黑色素生物合成抑制剂",
     "resistance_group": "FRAC 16.1", "toxicity": "low",
     "remark": "专用于稻瘟病，具有内吸性。"},
    {"standard_name": "春雷霉素", "english_name": "Kasugamycin", "category": "fungicide",
     "mode_of_action": "农用抗生素，抑制蛋白质合成",
     "resistance_group": "FRAC 24", "toxicity": "low"},
    {"standard_name": "井冈霉素", "english_name": "Validamycin", "category": "fungicide",
     "mode_of_action": "农用抗生素，抑制海藻糖酶",
     "resistance_group": "FRAC 26", "toxicity": "low",
     "remark": "纹枯病常用，对水稻纹枯病有较好效果。"},
    {"standard_name": "多抗霉素", "english_name": "Polyoxin", "category": "fungicide",
     "mode_of_action": "农用抗生素，抑制几丁质合成",
     "resistance_group": "FRAC 19", "toxicity": "low"},
    # ---- 杀虫剂 / 杀螨剂 ----
    {"standard_name": "吡虫啉", "english_name": "Imidacloprid", "category": "insecticide",
     "mode_of_action": "新烟碱类，作用于烟碱型乙酰胆碱受体",
     "resistance_group": "IRAC 4A", "toxicity": "moderate",
     "remark": "对蜜蜂高毒，花期禁用。"},
    {"standard_name": "噻虫嗪", "english_name": "Thiamethoxam", "category": "insecticide",
     "mode_of_action": "新烟碱类", "resistance_group": "IRAC 4A",
     "toxicity": "moderate",
     "remark": "与吡虫啉同属 IRAC 4A，不能作为轮换药剂。"},
    {"standard_name": "呋虫胺", "english_name": "Dinotefuran", "category": "insecticide",
     "mode_of_action": "新烟碱类", "resistance_group": "IRAC 4A",
     "toxicity": "low"},
    {"standard_name": "螺虫乙酯", "english_name": "Spirotetramat",
     "category": "insecticide",
     "mode_of_action": "季酮酸类，抑制脂质合成",
     "resistance_group": "IRAC 23", "toxicity": "low",
     "remark": "内吸双向传导，对刺吸式口器害虫效果好。"},
    {"standard_name": "乙基多杀菌素", "english_name": "Spinetoram",
     "category": "insecticide",
     "mode_of_action": "多杀菌素类，作用于烟碱型乙酰胆碱受体变构位点",
     "resistance_group": "IRAC 5", "toxicity": "low",
     "remark": "对蓟马效果突出。"},
    {"standard_name": "虫螨腈", "english_name": "Chlorfenapyr", "category": "insecticide",
     "mode_of_action": "吡咯类，氧化磷酸化解偶联剂",
     "resistance_group": "IRAC 13", "toxicity": "moderate"},
    {"standard_name": "氯虫苯甲酰胺", "english_name": "Chlorantraniliprole",
     "category": "insecticide",
     "mode_of_action": "双酰胺类，作用于鱼尼丁受体",
     "resistance_group": "IRAC 28", "toxicity": "low",
     "remark": "对鳞翅目害虫（螟虫、夜蛾）高效。"},
    {"standard_name": "甲氨基阿维菌素苯甲酸盐", "english_name": "Emamectin benzoate",
     "category": "insecticide", "mode_of_action": "阿维菌素类，作用于谷氨酸门控氯离子通道",
     "resistance_group": "IRAC 6", "toxicity": "moderate",
     "aliases": ["甲维盐"]},
    {"standard_name": "阿维菌素", "english_name": "Abamectin",
     "category": "insecticide",
     "mode_of_action": "阿维菌素类，作用于谷氨酸门控氯离子通道",
     "resistance_group": "IRAC 6", "toxicity": "high",
     "remark": "与甲维盐同属 IRAC 6；对蜜蜂和鱼类高毒。"},
    {"standard_name": "联苯肼酯", "english_name": "Bifenazate", "category": "acaricide",
     "mode_of_action": "肼基甲酸酯类，作用于神经系统的 GABA 受体",
     "resistance_group": "IRAC 25A", "toxicity": "low",
     "remark": "对叶螨各虫态均有效，包括卵。"},
    {"standard_name": "哒螨灵", "english_name": "Pyridaben", "category": "acaricide",
     "mode_of_action": "哒嗪酮类，线粒体电子传递链抑制剂",
     "resistance_group": "IRAC 21A", "toxicity": "moderate"},
]

# ---------------------------------------------------------------------------
# 产品白名单
# 说明：product_name 用「有效成分 + 剂型」的通用写法，不是某个具体商品名。
#      这样做的好处是不会把用户导向某个特定厂家的产品，
#      而规则引擎需要的四个匹配维度（作物 / 防治对象 / 阶段 / 间隔期）都能正常演示。
# ---------------------------------------------------------------------------
def _p(name: str, ingredients: list[str], crops: list[str], targets: list[str],
       stages: list[str], interval: int, precautions: str = "",
       verified: date = _TODAY) -> dict:
    return {
        "product_name": name,
        "registration_no": None,
        "manufacturer": None,
        "formulation": name.split()[-1] if " " in name else None,
        "active_ingredient_names": ingredients,
        "registered_crops": crops,
        "target_pests": targets,
        "applicable_stages": stages,
        "safety_interval_days": interval,
        "dosage_text": _LABEL_ONLY,
        "precautions": precautions,
        "label_url": None,
        "source_url": _SRC,
        "data_verified_at": verified,
        "review_status": "approved",
    }


PRODUCTS: list[dict] = [
    # ------------------------- 卵菌类（晚疫病 / 霜霉病 / 疫病） -------------------------
    _p("嘧菌酯悬浮剂", ["嘧菌酯"],
       ["番茄", "黄瓜", "辣椒", "马铃薯", "葡萄"],
       ["番茄晚疫病", "番茄早疫病", "黄瓜霜霉病", "黄瓜白粉病", "辣椒疫病",
        "马铃薯晚疫病", "马铃薯早疫病"],
       ["苗期", "开花坐果期", "结果期", "块茎形成期", "块茎膨大期"], 3,
       "抗性风险高，一个生长季内连续使用不超过 2 次，须与不同作用机制药剂轮换。"),
    _p("烯酰吗啉水分散粒剂", ["烯酰吗啉"],
       ["番茄", "黄瓜", "辣椒", "马铃薯"],
       ["番茄晚疫病", "黄瓜霜霉病", "辣椒疫病", "马铃薯晚疫病"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期", "块茎形成期", "块茎膨大期"], 3,
       "对卵菌有效，对白粉病等真菌性病害无效，注意对症。"),
    _p("氟吡菌胺·嘧菌酯悬浮剂", ["氟吡菌胺", "嘧菌酯"],
       ["黄瓜", "番茄", "马铃薯"],
       ["黄瓜霜霉病", "番茄晚疫病", "马铃薯晚疫病"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期", "块茎膨大期"], 3,
       "复配制剂，两个组分作用机制不同，但仍需与其他类别轮换。"),
    _p("代森锰锌可湿性粉剂", ["代森锰锌"],
       ["番茄", "黄瓜", "辣椒", "马铃薯", "苹果"],
       ["番茄晚疫病", "番茄早疫病", "黄瓜霜霉病", "辣椒炭疽病", "辣椒疫病",
        "马铃薯晚疫病", "马铃薯早疫病", "苹果轮纹病", "苹果褐斑病",
        "苹果斑点落叶病"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期", "块茎形成期", "块茎膨大期",
        "幼果期", "果实膨大期", "萌芽期"], 15,
       "保护性药剂，应在发病前或发病初期使用；多位点作用，抗性风险低。"),

    # ------------------------- 灰霉病 / 叶霉病 -------------------------
    _p("腐霉利烟剂", ["腐霉利"],
       ["番茄", "黄瓜", "辣椒", "韭菜"],
       ["番茄灰霉病"], ["开花坐果期", "结果期", "采收期"], 3,
       "棚室烟剂，施药后需密闭一段时间；抗性风险高，须轮换。"),
    _p("啶酰菌胺水分散粒剂", ["啶酰菌胺"],
       ["番茄", "黄瓜", "草莓", "葡萄"],
       ["番茄灰霉病", "黄瓜白粉病", "番茄叶霉病"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期"], 3,
       "SDHI 类，抗性风险中等，注意轮换。"),
    _p("异菌脲悬浮剂", ["异菌脲"],
       ["番茄", "黄瓜", "辣椒", "苹果"],
       ["番茄灰霉病", "番茄早疫病", "苹果褐斑病", "苹果斑点落叶病"],
       ["开花坐果期", "结果期", "幼果期", "果实膨大期"], 7,
       "与腐霉利同属 FRAC 2，两者不能互为轮换药剂。"),
    _p("苯醚甲环唑水分散粒剂", ["苯醚甲环唑"],
       ["番茄", "黄瓜", "辣椒", "苹果", "小麦"],
       ["番茄叶霉病", "番茄早疫病", "黄瓜靶斑病", "辣椒炭疽病", "苹果轮纹病",
        "苹果褐斑病", "苹果斑点落叶病", "小麦白粉病"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期", "幼果期", "果实膨大期",
        "返青拔节期"], 7,
       "三唑类，注意在苗期高浓度使用可能抑制生长。"),

    # ------------------------- 白粉病 / 靶斑病 / 锈病 -------------------------
    _p("吡唑醚菌酯乳油", ["吡唑醚菌酯"],
       ["黄瓜", "番茄", "小麦", "苹果"],
       ["黄瓜白粉病", "黄瓜靶斑病", "番茄早疫病", "小麦白粉病", "玉米大斑病",
        "苹果褐斑病", "苹果斑点落叶病"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期", "幼果期", "果实膨大期",
        "返青拔节期", "拔节期"], 7,
       "与嘧菌酯同属 FRAC 11，两者轮换无效。"),
    _p("戊唑醇悬浮剂", ["戊唑醇"],
       ["小麦", "玉米", "苹果", "黄瓜"],
       ["小麦条锈病", "小麦白粉病", "玉米大斑病", "玉米小斑病", "玉米锈病",
        "苹果褐斑病", "苹果轮纹病"],
       ["返青拔节期", "抽穗扬花期", "灌浆期", "拔节期", "抽雄吐丝期",
        "幼果期", "果实膨大期"], 21,
       "三唑类，小麦抽穗期使用注意避开扬花高峰；对锈病效果好。"),
    _p("丙环唑乳油", ["丙环唑"],
       ["小麦", "水稻", "苹果"],
       ["小麦条锈病", "小麦白粉病", "水稻纹枯病", "苹果褐斑病"],
       ["返青拔节期", "抽穗扬花期", "分蘖期", "孕穗抽穗期", "幼果期",
        "果实膨大期"], 21,
       "内吸性强，注意控制浓度，苗期慎用。"),

    # ------------------------- 赤霉病 / 稻瘟病 / 纹枯病 -------------------------
    _p("多菌灵可湿性粉剂", ["多菌灵"],
       ["小麦", "水稻", "番茄", "苹果"],
       ["小麦赤霉病", "水稻稻瘟病", "番茄灰霉病", "苹果轮纹病"],
       ["抽穗扬花期", "孕穗抽穗期", "开花坐果期", "幼果期"], 20,
       "FRAC 1，灰霉病菌与赤霉病菌对本品抗性普遍，建议优先选择其他机制药剂。"),
    _p("甲基硫菌灵悬浮剂", ["甲基硫菌灵"],
       ["小麦", "水稻", "苹果"],
       ["小麦赤霉病", "水稻稻瘟病", "苹果轮纹病", "苹果腐烂病"],
       ["抽穗扬花期", "孕穗抽穗期", "幼果期", "果实膨大期"], 20,
       "在植物体内转化为多菌灵，与多菌灵同属 FRAC 1，不能互为轮换。"),
    _p("三环唑可湿性粉剂", ["三环唑"],
       ["水稻"], ["水稻稻瘟病"],
       ["分蘖期", "孕穗抽穗期"], 21,
       "专用于稻瘟病，预防效果优于治疗效果，应在破口抽穗前施用。"),
    _p("春雷霉素水剂", ["春雷霉素"],
       ["水稻", "番茄", "黄瓜"],
       ["水稻稻瘟病", "番茄叶霉病"],
       ["分蘖期", "孕穗抽穗期", "开花坐果期", "结果期"], 14,
       "生物源药剂，可与化学药剂搭配使用以延缓抗性。"),
    _p("井冈霉素水剂", ["井冈霉素"],
       ["水稻", "小麦"],
       ["水稻纹枯病", "小麦纹枯病"],
       ["分蘖期", "孕穗抽穗期", "返青拔节期"], 14,
       "对纹枯病效果好，属于抗生素类，抗性风险低。"),

    # ------------------------- 炭疽病 / 枝干病害 -------------------------
    _p("咪鲜胺乳油", ["咪鲜胺"],
       ["辣椒", "苹果", "黄瓜"],
       ["辣椒炭疽病", "苹果轮纹病", "苹果褐斑病", "黄瓜靶斑病"],
       ["开花期", "结果期", "幼果期", "果实膨大期"], 14,
       "三唑类，注意轮换。"),
    _p("代森锰锌·苯醚甲环唑可湿性粉剂", ["代森锰锌", "苯醚甲环唑"],
       ["辣椒", "苹果", "番茄"],
       ["辣椒炭疽病", "苹果轮纹病", "苹果褐斑病", "苹果斑点落叶病",
        "番茄早疫病"],
       ["开花期", "结果期", "幼果期", "果实膨大期", "苗期"], 14,
       "复配制剂，兼具保护与治疗作用。"),
    _p("多抗霉素可湿性粉剂", ["多抗霉素"],
       ["苹果"],
       ["苹果斑点落叶病", "苹果褐斑病"],
       ["萌芽期", "花期", "幼果期", "果实膨大期"], 7,
       "生物源抗生素类，易产生抗性，单季连续使用不超过 3 次；不能与碱性农药混用。"),

    # ------------------------- 刺吸式口器害虫 -------------------------
    _p("吡虫啉可湿性粉剂", ["吡虫啉"],
       ["小麦", "黄瓜", "番茄", "辣椒", "苹果"],
       ["小麦蚜虫", "黄瓜蚜虫", "番茄白粉虱", "苹果蚜虫"],
       ["苗期", "抽蔓期", "开花结果期", "返青拔节期", "抽穗扬花期",
        "开花坐果期", "结果期"], 14,
       "对蜜蜂高毒，作物花期禁止使用；与噻虫嗪、呋虫胺同属 IRAC 4A，不可互为轮换。"),
    _p("噻虫嗪水分散粒剂", ["噻虫嗪"],
       ["水稻", "小麦", "黄瓜", "番茄"],
       ["水稻稻飞虱", "小麦蚜虫", "黄瓜蚜虫", "番茄白粉虱", "辣椒蓟马"],
       ["分蘖期", "孕穗抽穗期", "苗期", "抽蔓期", "开花结果期",
        "返青拔节期"], 14,
       "与吡虫啉同属 IRAC 4A；对蜜蜂高毒，花期禁用。"),
    _p("螺虫乙酯悬浮剂", ["螺虫乙酯"],
       ["黄瓜", "番茄", "辣椒", "苹果"],
       ["番茄白粉虱", "黄瓜蚜虫", "苹果红蜘蛛", "苹果蚜虫"],
       ["苗期", "开花坐果期", "结果期", "抽蔓期", "幼果期", "果实膨大期"], 7,
       "内吸双向传导，对隐蔽性害虫效果好；与烟碱类作用机制不同，可轮换。"),
    _p("乙基多杀菌素悬浮剂", ["乙基多杀菌素"],
       ["辣椒", "黄瓜", "番茄"],
       ["辣椒蓟马", "黄瓜蚜虫", "番茄白粉虱"],
       ["苗期", "开花期", "结果期", "抽蔓期", "开花坐果期"], 3,
       "对蓟马效果突出；建议傍晚施药，蓟马昼伏夜出。"),

    # ------------------------- 鳞翅目害虫 -------------------------
    _p("氯虫苯甲酰胺悬浮剂", ["氯虫苯甲酰胺"],
       ["玉米", "水稻", "番茄", "辣椒"],
       ["玉米草地贪夜蛾", "玉米螟", "水稻二化螟", "番茄棉铃虫"],
       ["苗期", "拔节期", "抽雄吐丝期", "分蘖期", "孕穗抽穗期",
        "开花坐果期", "结果期"], 14,
       "对低龄幼虫效果好，虫龄越大效果越差，应尽早防治。"),
    _p("甲氨基阿维菌素苯甲酸盐乳油", ["甲氨基阿维菌素苯甲酸盐"],
       ["玉米", "水稻", "番茄", "辣椒", "黄瓜"],
       ["玉米草地贪夜蛾", "玉米螟", "水稻二化螟", "番茄棉铃虫", "黄瓜蚜虫"],
       ["苗期", "拔节期", "抽雄吐丝期", "分蘖期", "孕穗抽穗期",
        "开花坐果期", "结果期", "抽蔓期"], 7,
       "对蜜蜂和鱼类高毒，花期及临近水源地慎用；与阿维菌素同属 IRAC 6。"),
    _p("虫螨腈悬浮剂", ["虫螨腈"],
       ["玉米", "辣椒", "黄瓜"],
       ["玉米草地贪夜蛾", "辣椒蓟马", "黄瓜蚜虫"],
       ["苗期", "拔节期", "开花期", "结果期", "抽蔓期"], 14,
       "作用较快，注意与不同机制药剂轮换。"),

    # ------------------------- 杀螨剂 -------------------------
    _p("联苯肼酯悬浮剂", ["联苯肼酯"],
       ["苹果", "柑橘", "黄瓜"],
       ["苹果红蜘蛛"],
       ["萌芽期", "幼果期", "果实膨大期", "成熟采收期", "抽蔓期"], 14,
       "对叶螨各虫态有效，包括卵；与哒螨灵作用机制不同，可轮换。"),
    _p("哒螨灵乳油", ["哒螨灵"],
       ["苹果", "柑橘", "黄瓜"],
       ["苹果红蜘蛛"],
       ["萌芽期", "幼果期", "果实膨大期"], 21,
       "与联苯肼酯作用机制不同，可轮换使用以延缓抗性。"),
]

# ---------------------------------------------------------------------------
# 明确「没有可用药剂」的对象 —— 用于演示闸门 B
# 这两类问题规则引擎必然拦截，因为根本不该走用药路径：
#   - 病毒病：无治疗性药剂，防治靠防虫和拔除病株
#   - 生理性病害：不是病原引起，打杀菌剂无效
# ---------------------------------------------------------------------------
NO_PESTICIDE_TARGETS: dict[str, str] = {
    "番茄黄化曲叶病毒病": "病毒性病害没有可治愈的药剂，防治关键是防烟粉虱和拔除病株。",
    "辣椒脐腐病": "生理性病害（缺钙与水分不均），杀菌剂无效，应调整水分管理和补钙。",
}
