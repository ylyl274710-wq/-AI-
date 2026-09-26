"""知识库扩充条目（第 2 批，2026-09-26）：作物 8 种不变，病害虫害条目大幅扩充。

★ 与 knowledge_data.py 的关系
  原文件是「手写每条的完整正文」；本文件条目量大约翻倍，再手写会失控，
  所以用构建器统一生成正文 —— **结构化 conditions 是唯一事实来源**，
  「发生条件」段落由它自动渲染，保证给农户看的数字和风险计算用的数字
  永远一致（原条目两处手写，改一处漏一处的隐患一直存在）。
  runner 把 DISEASES 与 DISEASES_EXT 拼接后按同一套幂等逻辑导入。

★ 内容可信度（与原文件同一立场）
  条目按公开植保常识整理，识别要点里刻意写清「与哪个病怎么区分」——
  这是检索命中的关键，也是农户最需要的。来源统一标「待补充文献出处」，
  正式使用前须逐条补出处并人工复核（review_status 走原 runner 的约定）。

★ 正文用 Markdown 分节（## 识别要点 / ## 发生条件 / ## 防治要点）
  索引器 chunk_text 已支持按标题切节，每篇文档被切成独立可检索的
  2~4 个 chunk：查「怎么防」的用户不会再被「症状」长文稀释召回。
"""
from __future__ import annotations

_S_EXT = "公开农业植保知识整理（待补充文献出处）"


def _cond_text(cond: dict) -> str:
    """把结构化发生条件渲染成农户能读的句子 —— 与 assess_risk 同一份数字。"""
    parts: list[str] = []
    if cond.get("temp_min") is not None or cond.get("temp_max") is not None:
        lo = cond.get("temp_min", "-")
        hi = cond.get("temp_max", "-")
        parts.append(f"平均气温 {lo}~{hi}℃ 时最易发生")
    if cond.get("humidity_min"):
        parts.append(f"相对湿度 ≥{cond['humidity_min']}%")
    if cond.get("rain_hours_min"):
        parts.append(f"连阴雨累计 ≥{cond['rain_hours_min']} 小时")
    if cond.get("wind_max") is not None:
        parts.append(f"风速 ≤{cond['wind_max']} m/s 利于传播")
    if not parts:
        parts.append("常年可见，天气关联不明显")
    return "、".join(parts) + "。"


def _d(*, standard_name: str, aliases: list[str], category: str,
       crop_names: list[str], pathogen: str, severity: str, symptoms: str,
       conditions: dict | None, stage_susceptibility: dict,
       control_advice: str, differentiate: str = "", occurrence: str = "",
       ) -> dict:
    """组装一条病害/虫害条目：DiseaseRecord 结构化字段 + 自动渲染的知识正文。

    differentiate：与易混淆病害的区分要点（检索与诊断的关键内容，单独一句）。
    occurrence：发生条件正文里「结构化字段之外」的补充（如传播途径）。
    """
    if conditions is None:
        conditions = {}
    cond_body = _cond_text(conditions) + occurrence
    content = (f"## 识别要点\n{symptoms}"
               + (f"\n{differentiate}" if differentiate else "")
               + f"\n\n## 发生条件\n{cond_body}"
               + f"\n\n## 防治要点\n{control_advice}"
               + "\n\n注意：症状描述供识别参考；具体用药品种与剂量"
                 "以农药登记标签和当地植保部门指导意见为准。")
    return {
        "standard_name": standard_name,
        "aliases": aliases,
        "category": category,
        "crop_names": crop_names,
        "pathogen": pathogen if category in ("disease", "physiological") else "",
        "severity": severity,
        "symptoms": symptoms,
        "conditions": conditions or None,
        "stage_susceptibility": stage_susceptibility,
        "control_advice": control_advice,
        "source": _S_EXT,
        "content": content,
    }


DISEASES_EXT: list[dict] = [

    # ============================== 番茄（补 10 条） ============================== #
    _d(standard_name="番茄青枯病", aliases=["细菌性枯萎病"], category="disease",
       crop_names=["番茄", "辣椒", "茄子"], pathogen="青枯雷尔氏菌（Ralstonia solanacearum）",
       severity="high",
       symptoms="白天整株或一侧叶片突然萎蔫，早晚暂时恢复，反复几天后不再恢复而枯死；"
                "茎基部切开可见维管束变褐，横切挤压断面溢出乳白色黏液（菌脓）。",
       conditions={"temp_min": 25, "temp_max": 35, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "开花坐果期": "高", "结果期": "高", "采收期": "中"},
       control_advice="发病株立即拔除带出田外，病穴用石灰消毒。与非茄科作物轮作 3 年以上，"
                      "选用抗病品种，采用高垄栽培避免积水。发病初期可选用登记的细菌性病害药剂灌根。",
       differentiate="与枯萎病的区分：青枯病叶片不变黄、病程急（几天内整株萎蔫），"
                     "挤压茎断面有乳白色黏液；枯萎病叶片自下而上变黄、病程慢，无黏液。",
       occurrence="病菌随雨水和灌溉水传播，从根部伤口侵入，暴雨后骤晴最易暴发。"),

    _d(standard_name="番茄溃疡病", aliases=["细菌性溃疡病"], category="disease",
       crop_names=["番茄"], pathogen="密执安棒形杆菌（Clavibacter michiganensis）",
       severity="high",
       symptoms="下部叶片一侧小叶萎蔫内卷变褐干枯；茎上出现狭长的褐色条斑，开裂后露出髓部，"
                "髓部变褐中空；果实表面出现白色圆形小斑点，中心有浅褐色突起，像鸟眼，称鸟眼斑。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 75,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "开花坐果期": "高", "结果期": "高", "采收期": "中"},
       control_advice="属检疫性细菌病害。选用无病种子并温汤浸种，发现病株立即拔除销毁，"
                      "整枝打杈选晴天露水干后进行，避免带菌汁液传病。可用登记的铜制剂或细菌病害药剂预防。",
       differentiate="与青枯病区分：溃疡病有茎条斑开裂、髓部中空和果实鸟眼斑，青枯病无果实症状。"),

    _d(standard_name="番茄斑枯病", aliases=["白星病", "鱼目斑病"], category="disease",
       crop_names=["番茄"], pathogen="番茄壳针孢（Septoria lycopersici）",
       severity="medium",
       symptoms="叶片发病为主，初为水浸状小点，后扩大成直径 2~4 毫米的近圆形病斑，"
                "中心灰白色、边缘深褐色，上面散生黑色小粒点（分生孢子器），形似鱼目。"
                "下部老叶先发病，严重时叶片枯黄脱落。",
       conditions={"temp_min": 22, "temp_max": 26, "humidity_min": 85,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "低", "开花坐果期": "中", "结果期": "高", "采收期": "高"},
       control_advice="收获后彻底清除病残体并深翻。合理密植，及时整枝绑架改善通风。"
                      "避免在大棚内长期高湿。发病初期喷施登记用于番茄斑枯病的杀菌剂，10 天左右一次。",
       differentiate="与早疫病区分：斑枯病病斑小、中心灰白有黑点、无同心轮纹；"
                     "早疫病病斑大、有明显同心轮纹和黄晕。"),

    _d(standard_name="番茄根结线虫病", aliases=["番茄线虫病"], category="disease",
       crop_names=["番茄", "黄瓜", "胡萝卜"], pathogen="南方根结线虫（Meloidogyne incognita）",
       severity="medium",
       symptoms="地上部生长缓慢、矮化、黄化，中午高温时萎蔫，似缺水缺肥；"
                "拔根检查可见根部形成大小不等的球形或葫芦形根结（瘤），须根少，"
                "剖开根结内部可见白色梨形小线虫。",
       conditions={"temp_min": 22, "temp_max": 30},
       stage_susceptibility={"苗期": "中", "开花坐果期": "高", "结果期": "高", "采收期": "中"},
       control_advice="属土传病害，以农业防治为主：与水稻或葱蒜类轮作，"
                      "夏季高温闷棚（灌水覆膜 20 天以上）杀死土壤线虫，"
                      "移栽前用淡紫拟青霉等生物菌剂处理土壤。重病棚建议换土或嫁接栽培。",
       differentiate="与枯萎病区分：线虫病拔根必见根结瘤状物，枯萎病根部无瘤但维管束变褐。",
       occurrence="土壤温度 22~30℃、砂性土壤和连作棚室发生重。"),

    _d(standard_name="番茄病毒病", aliases=["番茄花叶病", "番茄蕨叶病"], category="disease",
       crop_names=["番茄", "辣椒"], pathogen="烟草花叶病毒（TMV）、黄瓜花叶病毒（CMV）等",
       severity="high",
       symptoms="花叶型：叶片黄绿相间斑驳，浓绿部分略隆起；蕨叶型：新叶细长呈线状卷曲；"
                "条斑型：茎和果实上出现褐色坏死条斑。病株不同程度矮化，结果少且畸形。",
       conditions={"temp_min": 28, "temp_max": 35},
       stage_susceptibility={"苗期": "高", "开花坐果期": "高", "结果期": "中", "采收期": "低"},
       control_advice="病毒病无法治愈，防重于治：选用抗病品种，种子温水消毒，"
                      "整枝打杈先用肥皂水洗手并先健株后病株，防治传毒媒介（白粉虱、蚜虫），"
                      "田间及田边清除杂草寄主。发病初期喷施氨基寡糖素等诱导抗性制剂缓解。",
       differentiate="与生理性卷叶区分：病毒病伴随黄绿斑驳、蕨叶、矮化等系统症状，"
                     "且新叶症状重；生理性卷叶叶色正常、仅叶片卷曲。",
       occurrence="高温干旱利于蚜虫白粉虱传毒，接触摩擦也能传播（TMV）。"),

    _d(standard_name="番茄绵疫病", aliases=["番茄褐色腐败病"], category="disease",
       crop_names=["番茄", "茄子"], pathogen="寄生疫霉（Phytophthora parasitica）",
       severity="medium",
       symptoms="主要危害接近成熟的果实，果面出现淡褐色水浸状圆斑，"
                "扩展很快并出现同心轮纹，湿度大时长出白色绵状霉层，"
                "病果脱落后很快腐烂。叶片也会出现水浸状大斑。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 85,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "低", "开花坐果期": "低", "结果期": "高", "采收期": "高"},
       control_advice="高温多雨季节是防控关键期：雨后及时排水，地膜覆盖减少病菌溅射，"
                      "及时摘除病果带出田外。发病初期喷施登记用于疫病类的杀菌剂，重点喷果。",
       differentiate="与灰霉病区分：绵疫病果面霉层白色绵状、病斑大而软腐快，"
                     "灰霉病霉层灰褐色绒毛状、多从残花处开始。"),

    _d(standard_name="番茄茎基腐病", aliases=["番茄立枯病"], category="disease",
       crop_names=["番茄", "辣椒", "茄子"], pathogen="立枯丝核菌（Rhizoctonia solani）",
       severity="medium",
       symptoms="定植后至结果初期发病，茎基部地面上下 2~3 厘米处出现暗褐色病斑，"
                "凹陷缢缩，绕茎一周后整株萎蔫枯死，但根部一般不腐烂。"
                "病斑表面有时可见淡褐色蛛丝状菌丝。",
       conditions={"temp_min": 20, "temp_max": 25, "humidity_min": 80},
       stage_susceptibility={"苗期": "高", "开花坐果期": "中", "结果期": "低", "采收期": "低"},
       control_advice="定植不宜过深，培土不埋过茎基。采用高垄栽培，避免大水漫灌浸泡茎基。"
                      "移栽时用登记的杀菌剂蘸根或灌根预防，发现病株及早扒土晾茎并用药。",
       differentiate="与枯萎病区分：茎基腐病病斑在茎基部表面、凹陷缢缩，根部完好；"
                     "枯萎病茎基外观多正常而维管束变褐。"),

    _d(standard_name="番茄棉铃虫", aliases=["棉铃实夜蛾"], category="pest",
       crop_names=["番茄", "辣椒", "茄子"], pathogen="",
       severity="medium",
       symptoms="以幼虫蛀食果实：果面出现蛀孔，孔外常带虫粪，"
                "幼果被吃空或引起腐烂脱落；也食害花蕾和嫩茎。"
                "幼虫体色变化大，体侧有白色纵线，受惊乱爬。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 60},
       stage_susceptibility={"苗期": "低", "开花坐果期": "高", "结果期": "高", "采收期": "中"},
       control_advice="成虫期用性诱剂和杀虫灯诱杀，产卵高峰期释放赤眼蜂。"
                      "抓住卵孵化盛期至幼虫蛀果前喷施登记的生物农药（如苏云金杆菌、"
                      "氯虫苯甲酰胺类），蛀入果实后再打药效果差。",
       differentiate="与烟青虫区分：两者形态习性相近、常混发，防治方法相同，"
                     "重点都是抓「蛀果前」的时机。",
       occurrence="高温季节世代重叠，成虫趋光趋化性强。"),

    _d(standard_name="番茄枯萎病", aliases=["番茄萎蔫病"], category="disease",
       crop_names=["番茄"], pathogen="番茄尖镰孢番茄专化型（Fusarium oxysporum f.sp. lycopersici）",
       severity="medium",
       symptoms="发病初期仅植株一侧下部叶片变黄，中午萎蔫早晚恢复，"
                "反复数日后整株枯黄死亡但叶片不脱落；"
                "剖开茎基部维管束呈黄褐色。病程缓慢，常持续 15~30 天。",
       conditions={"temp_min": 25, "temp_max": 28, "humidity_min": 70},
       stage_susceptibility={"苗期": "低", "开花坐果期": "中", "结果期": "高", "采收期": "中"},
       control_advice="土传病害，重点在土壤处理：与水稻轮作或夏季高温闷棚，"
                      "选用抗病品种或嫁接苗，移栽前用木霉菌等生物菌剂处理土壤。"
                      "发病后可尝试登记的杀菌剂灌根，但治愈率有限，须及时清除重病株。",
       differentiate="与青枯病区分：枯萎病病程慢、叶片自下而上变黄、无菌脓；"
                     "青枯病病程急、叶绿而萎蔫、茎断面有乳白黏液。"),

    _d(standard_name="番茄细菌性斑点病", aliases=["番茄细菌性叶斑病"], category="disease",
       crop_names=["番茄"], pathogen="番茄细菌性斑点病假单胞菌（Pseudomonas syringae pv. tomato）",
       severity="low",
       symptoms="叶片上初为深褐色至黑色水浸状小斑点，直径 1~2 毫米，"
                "周围有黄色晕圈，后期病斑中央坏死穿孔；"
                "叶柄和茎上出现黑色条斑；幼果出现黑色小凸起斑点。",
       conditions={"temp_min": 18, "temp_max": 24, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "高", "开花坐果期": "中", "结果期": "中", "采收期": "低"},
       control_advice="冷凉多雨季节注意巡查。种子温水消毒，避免带菌苗移栽；"
                      "浇水避免喷溅传播，发病初期喷施铜制剂等细菌病害登记药剂，"
                      "喷药后遇雨应补喷。",
       differentiate="与细菌性斑疹病（番茄疮痂病）近似：两者都表现黑点黄晕，"
                     "防治思路一致（铜制剂），田间可不必强分。"),

    # ============================== 黄瓜（补 9 条） ============================== #
    _d(standard_name="黄瓜细菌性角斑病", aliases=["黄瓜角斑病"], category="disease",
       crop_names=["黄瓜"], pathogen="丁香假单胞菌黄瓜致病变种（Pseudomonas syringae pv. lachrymans）",
       severity="high",
       symptoms="叶片初为水浸状小点，扩大后因受叶脉限制呈多角形黄褐色病斑；"
                "湿度大时叶背病斑溢出白色菌脓，干后成白膜或粉末状；"
                "后期病斑中央干枯穿孔。瓜条上出现水浸状凹陷圆斑并溢菌脓，可烂瓜。",
       conditions={"temp_min": 18, "temp_max": 26, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "抽蔓期": "中", "开花结果期": "高", "采收期": "高"},
       control_advice="种子温汤浸种消毒。浇水后及时放风排湿，避免叶面长时间结露和人为碰伤叶片。"
                      "发病初期喷施铜制剂或噻唑锌类登记药剂，注意与真菌性药剂区分使用。",
       differentiate="与霜霉病是经典易混对：两者都是多角形斑 —— "
                     "角斑病斑后期穿孔、叶背溢白色菌脓、对光看有透光感；"
                     "霜霉病斑不穿孔、叶背长灰黑霉层、不透光。"),

    _d(standard_name="黄瓜灰霉病", aliases=["黄瓜灰葡萄孢病"], category="disease",
       crop_names=["黄瓜", "西葫芦"], pathogen="灰葡萄孢（Botrytis cinerea）",
       severity="high",
       symptoms="多从开败的雌花处开始侵染，花瓣腐烂长出灰褐色霉层，"
                "继而向幼瓜蔓延，瓜尖变软腐烂并长灰霉；"
                "叶片多从残留的花或落花处发病，形成近圆形或不规则褐色大斑。",
       conditions={"temp_min": 15, "temp_max": 23, "humidity_min": 90,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "抽蔓期": "中", "开花结果期": "高", "采收期": "高"},
       control_advice="低温高湿棚室重点病：及时摘除残花病瓜带出棚外，"
                      "阴雨天控制浇水并加强放风，棚内可下午闭棚升温后放湿。"
                      "发病初期用登记的腐霉利、异菌脲类药剂，灰霉菌易抗药，注意轮换作用机制。",
       differentiate="与菌核病区分：菌核病腐烂处可见白色絮状菌丝和鼠粪状黑色菌核，"
                     "灰霉病只有灰褐色霉粉。"),

    _d(standard_name="黄瓜疫病", aliases=["黄瓜疫霉病"], category="disease",
       crop_names=["黄瓜"], pathogen="甜瓜疫霉（Phytophthora melonis）",
       severity="high",
       symptoms="茎基部或节部出现暗绿色水浸状缢缩，病部以上植株迅速萎蔫青枯；"
                "叶片出现水浸状暗绿色大斑，软腐下垂；瓜条软腐凹陷，表面长稀疏白霉。"
                "整株病程急，几天内蔓延成片。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 85,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "高", "抽蔓期": "高", "开花结果期": "高", "采收期": "中"},
       control_advice="高垄栽培、膜下滴灌，避免雨季大水漫灌。发现中心病株立即拔除，"
                      "病穴撒石灰消毒。发病初期用登记的烯酰吗啉、霜脲氰类药剂灌根加喷雾。",
       differentiate="与枯萎病区分：疫病病程急、茎基水浸状缢缩发软、叶绿而蔫；"
                     "枯萎病病程慢、叶片黄化、维管束褐变。"),

    _d(standard_name="黄瓜蔓枯病", aliases=["黄瓜黑腐病"], category="disease",
       crop_names=["黄瓜", "甜瓜"], pathogen="瓜类黑腐小球壳菌（Mycosphaerella melonis）",
       severity="medium",
       symptoms="主要危害茎蔓和叶片。茎蔓近节部出现油浸状椭圆形病斑，"
                "后变灰褐色，表面密生黑色小粒点并溢出琥珀色胶质物（流胶），"
                "严重时蔓裂；叶片病斑近圆形，淡褐色，上有同心轮纹和黑点。",
       conditions={"temp_min": 20, "temp_max": 25, "humidity_min": 85},
       stage_susceptibility={"苗期": "低", "抽蔓期": "中", "开花结果期": "高", "采收期": "高"},
       control_advice="避免茎蔓机械伤口和过早摘除下部叶片造成的伤流。"
                      "合理密植控制湿度。发病初期用登记的苯醚甲环唑类药剂涂抹病蔓并全田喷雾。",
       differentiate="与疫病区分：蔓枯病病斑干燥、有黑点和流胶、病程慢；"
                     "疫病病斑水浸状软腐、病程急。"),

    _d(standard_name="黄瓜黑星病", aliases=["黄瓜疮痂病"], category="disease",
       crop_names=["黄瓜"], pathogen="瓜疮痂枝孢霉（Cladosporium cucumerinum）",
       severity="medium",
       symptoms="嫩茎嫩叶最敏感：叶片出现近圆形黄白色小斑，后期穿孔，"
                "边缘呈星纹状开裂；茎蔓病斑凹陷龟裂并流胶；"
                "瓜条出现暗绿色凹陷疮痂状病斑，溢出琥珀色胶状物后呈疮痂状畸形。",
       conditions={"temp_min": 17, "temp_max": 24, "humidity_min": 90},
       stage_susceptibility={"苗期": "高", "抽蔓期": "中", "开花结果期": "高", "采收期": "中"},
       control_advice="低温高湿棚室易发。种子消毒，棚室注意保温排湿。"
                      "发病初期喷施登记的氟硅唑、苯醚甲环唑类药剂，"
                      "重病棚配合硫磺熏蒸消毒。",
       differentiate="与黑斑病区分：黑星病病斑星状开裂流胶、瓜条疮痂畸形；"
                     "普通黑斑病不流胶、瓜条不畸形。"),

    _d(standard_name="黄瓜根结线虫病", aliases=["黄瓜线虫病"], category="disease",
       crop_names=["黄瓜", "番茄"], pathogen="南方根结线虫（Meloidogyne incognita）",
       severity="medium",
       symptoms="植株矮化黄化，生长点发育不良，中午萎蔫；"
                "根系上密生念珠状或球状根结，须根丛生，"
                "重病株根系畸形如「瘤状团」，剖开可见白色梨形雌虫。",
       conditions={"temp_min": 22, "temp_max": 30},
       stage_susceptibility={"苗期": "高", "抽蔓期": "中", "开花结果期": "高", "采收期": "中"},
       control_advice="夏季高温闷棚 + 石灰氮处理土壤效果最好；"
                      "定植前用淡紫拟青霉、厚孢轮枝菌等生物制剂处理定植穴；"
                      "生长期用登记的杀线虫剂随水灌根。重病棚与葱蒜类或水稻轮作。",
       differentiate="与枯萎病区分：线虫病根部必有根结瘤，枯萎病根部无瘤。"),

    _d(standard_name="黄瓜沤根", aliases=["黄瓜烂根"], category="physiological",
       crop_names=["黄瓜", "番茄", "辣椒"], pathogen="非生物性（低温高湿引起）",
       severity="low",
       symptoms="育苗期或刚定植时发生：幼苗根系不发新根，根皮发锈后腐烂，"
                "地上部叶片萎蔫变黄，生长停滞，严重时成片死苗。"
                "病苗拔起根部无病斑霉层，只表现为沤烂。",
       conditions={"temp_min": 8, "temp_max": 15, "humidity_min": 90},
       stage_susceptibility={"苗期": "高", "抽蔓期": "低", "开花结果期": "低", "采收期": "低"},
       control_advice="苗床保温控湿：地温稳定在 12℃ 以上再育苗，"
                      "阴雨天不浇水，湿度过大时撒干细土吸湿并通风。"
                      "已沤根的苗床及时松土散墒提高地温，促进发新根。",
       differentiate="与猝倒病区分：沤根是生理性烂根、无霉层、不传染成片猝倒；"
                     "猝倒病茎基水浸状缢缩倒伏并可见白色绵霉。"),

    _d(standard_name="黄瓜花打顶", aliases=["黄瓜顶头花"], category="physiological",
       crop_names=["黄瓜"], pathogen="非生物性（低温、干旱或过度控秧引起）",
       severity="low",
       symptoms="生长点不再伸长，顶端簇生大量小雌花并封顶，"
                "龙头紧抱不展叶，中下部叶片浓绿或发黄，"
                "植株节间极短，坐瓜少且易畸形。",
       conditions={"temp_min": 8, "temp_max": 15, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "抽蔓期": "高", "开花结果期": "高", "采收期": "中"},
       control_advice="多为低温寡照、土壤干旱或一次采瓜过多导致：提高夜温并适度浇水，"
                      "疏掉顶部密集雌花减少养分消耗，追施速效氮肥配合腐殖酸促根，"
                      "恢复后再正常留瓜。",
       differentiate="与病毒病区分：花打顶叶色正常、无斑驳花叶、疏花促壮后可恢复；"
                     "病毒病伴随花叶蕨叶且不可逆。"),

    _d(standard_name="黄瓜细菌性枯萎病", aliases=["黄瓜青枯病"], category="disease",
       crop_names=["黄瓜", "甜瓜"], pathogen="嗜维管束欧文氏菌（Erwinia tracheiphila）",
       severity="low",
       symptoms="叶片从一侧开始急性萎蔫，早晨轻、中午重，"
                "数日后整蔓青枯死亡但叶色仍绿；"
                "茎切断后挤压断面溢出乳白色黏液，维管束变褐。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 70},
       stage_susceptibility={"苗期": "低", "抽蔓期": "中", "开花结果期": "高", "采收期": "中"},
       control_advice="由黄瓜甲虫等咀嚼式害虫传病，先治虫再防病。"
                      "发现病蔓立即剪除带出田外。发病重的棚与禾本科作物轮作。",
       differentiate="与真菌性枯萎病区分：细菌性枯萎病程更快、叶色绿而蔫、有菌脓；"
                     "真菌枯萎病程慢、叶片黄化。"),

    # ============================== 辣椒（补 6 条） ============================== #
    _d(standard_name="辣椒病毒病", aliases=["辣椒花叶病"], category="disease",
       crop_names=["辣椒", "番茄"], pathogen="黄瓜花叶病毒（CMV）、烟草花叶病毒（TMV）等",
       severity="high",
       symptoms="花叶型叶片黄绿斑驳、皱缩；黄化型整叶褪绿变黄；"
                "坏死型茎上出现褐色条斑、果实上凹陷坏死斑；"
                "畸形型新叶细小丛生呈蕨叶状。病株落花落果严重、明显矮化。",
       conditions={"temp_min": 28, "temp_max": 35},
       stage_susceptibility={"苗期": "高", "开花期": "高", "结果期": "中"},
       control_advice="治虫防病是核心：及时防治蚜虫、白粉虱等传毒媒介，"
                      "苗期覆盖防虫网；选用抗病品种；接触操作先健株后病株。"
                      "发病初期喷氨基寡糖素、香菇多糖等缓解，配合叶面肥增强长势。",
       differentiate="与茶黄螨危害区分：茶黄螨危害叶缘下卷、叶背油亮有光泽，"
                     "打杀螨剂有效；病毒病斑驳畸形、无光泽、打螨剂无效。",
       occurrence="高温干旱利于媒介虫繁殖传毒，连作地和周边杂草多的地块发生重。"),

    _d(standard_name="辣椒根腐病", aliases=["辣椒烂根病"], category="disease",
       crop_names=["辣椒", "番茄"], pathogen="腐皮镰孢菌（Fusarium solani）",
       severity="medium",
       symptoms="定植后陆续发病：白天叶片萎蔫，早晚恢复，反复数日整株枯死；"
                "根系和根颈部皮层腐烂变褐，易剥离，侧根几乎全烂，"
                "维管束变褐但茎基表面不缢缩。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 80},
       stage_susceptibility={"苗期": "高", "开花期": "中", "结果期": "中"},
       control_advice="高垄栽培避免根部积水，雨后及时中耕散墒。"
                      "移栽前用木霉菌生物菌剂处理定植穴，发病初期用登记的杀菌剂灌根，"
                      "重病田与禾本科作物轮作。",
       differentiate="与疫病区分：根腐病病位在根部皮层、茎基不缢缩；"
                     "疫病茎基水浸状黑褐色缢缩、蔓延快。"),

    _d(standard_name="辣椒白粉病", aliases=["辣椒白粉菌叶斑"], category="disease",
       crop_names=["辣椒"], pathogen="鞑靼内丝白粉菌（Leveillula taurica）",
       severity="medium",
       symptoms="与常见瓜果白粉病不同：**霉层长在叶背面**，初为白色粉状小圆斑，"
                "正面对应处仅表现褪绿黄斑，边缘不明显；"
                "严重时大量叶片黄化脱落，果实暴露被日灼。",
       conditions={"temp_min": 20, "temp_max": 28, "humidity_min": 60},
       stage_susceptibility={"苗期": "低", "开花期": "中", "结果期": "高"},
       control_advice="收获后清除病残体。棚室注意通风降湿但该菌耐干燥，"
                      "主要靠药剂控制：发病初期喷施登记的苯醚甲环唑、氟菌唑类药剂，"
                      "重点喷叶背，7~10 天一次连续 2~3 次。",
       differentiate="诊断要点：辣椒叶面黄斑而叶背才有白粉，与黄瓜白粉病"
                     "「叶面直接铺白粉」不同，容易漏判为缺素。",
       occurrence="偏干燥的棚室也会流行（该菌耐干燥），温度 20~28℃ 最适。"),

    _d(standard_name="辣椒软腐病", aliases=["辣椒烂果病"], category="disease",
       crop_names=["辣椒", "番茄", "大白菜"], pathogen="胡萝卜软腐果胶杆菌（Pectobacterium carotovorum）",
       severity="medium",
       symptoms="果实初现水浸状暗绿色斑，随后果肉迅速软腐，"
                "有恶臭味，果皮完好而内部腐烂，失水后果皮干缩呈白色薄膜状挂枝。"
                "茎叶伤口处也可水烂。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "开花期": "中", "结果期": "高"},
       control_advice="细菌病害由伤口侵入：及时防治烟青虫、棉铃虫等蛀果害虫减少伤口，"
                      "整枝打杈选晴天。雨后及时喷施铜制剂等细菌性病害登记药剂保护果实。",
       differentiate="与日灼果后继发腐烂区分：日灼是果面朝阳面白色革质斑在前、"
                     "软腐在后；软腐病是水浸状软腐在前、与日晒位置无关。"),

    _d(standard_name="辣椒茶黄螨", aliases=["茶黄螨危害"], category="pest",
       crop_names=["辣椒", "茄子", "黄瓜", "番茄"], pathogen="",
       severity="medium",
       symptoms="成螨若螨聚集在嫩叶嫩芽吸汁：受害嫩叶边缘向下卷曲、"
                "叶背油亮有光泽、变窄变硬，嫩茎嫩芽黄褐色；"
                "果实受害果柄及萼片失去光泽变褐，果面粗糙呈锈色裂纹，俗称「牛皮果」。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 70},
       stage_susceptibility={"苗期": "中", "开花期": "高", "结果期": "高"},
       control_advice="体长仅 0.2 毫米肉眼难见，靠症状判断。用放大镜查叶背确认后，"
                      "用登记的杀螨剂重点喷嫩叶嫩芽背面，5~7 天一次连续 2~3 次；"
                      "螨类易抗药，注意轮换用药。",
       differentiate="与病毒病区分：茶黄螨危害叶缘下卷有油亮感、打杀螨剂后新叶恢复正常；"
                     "病毒病斑驳丛簇无光泽。两者田间易混，先用杀螨剂试探是常用鉴别手段。",
       occurrence="温暖高湿的棚室全年发生，以嫩组织危害为主。"),

    _d(standard_name="辣椒烟青虫", aliases=["烟夜蛾"], category="pest",
       crop_names=["辣椒", "番茄"], pathogen="",
       severity="medium",
       symptoms="幼虫蛀食花蕾和果实：花蕾被蛀后变黄脱落，"
                "果实被蛀成孔洞、孔口带虫粪，雨水灌入后整果腐烂脱落。"
                "幼虫绿色至暗褐色，体表有短毛。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 60},
       stage_susceptibility={"苗期": "低", "开花期": "高", "结果期": "高"},
       control_advice="性诱剂诱杀成虫，产卵期释放赤眼蜂。"
                      "关键在幼虫蛀果前（卵孵化盛期）喷施登记的苏云金杆菌或氯虫苯甲酰胺类，"
                      "蛀入果内后防效骤降。",
       differentiate="与棉铃虫混发，形态极近，田间按「钻蛀性夜蛾」统一防治即可。",
       occurrence="一年多代，越往南代数越多，成虫昼伏夜出趋光。"),

    # ============================== 小麦（补 7 条） ============================== #
    _d(standard_name="小麦纹枯病", aliases=["小麦尖眼点病"], category="disease",
       crop_names=["小麦"], pathogen="禾谷丝核菌（Rhizoctonia cerealis）",
       severity="high",
       symptoms="主要危害叶鞘和茎秆：返青拔节期叶鞘上出现淡黄色小斑，"
                "后发展成边缘褐色、中央灰白色的云纹状病斑；"
                "茎基部受害后花秆烂茎，抽穗后遇风雨倒伏或形成枯白穗。"
                "湿度大时病部可见白色蛛丝状菌核。",
       conditions={"temp_min": 15, "temp_max": 25, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "中", "灌浆期": "中"},
       control_advice="返青拔节期是用药关键期：田间病株率达 10% 左右开始用药，"
                      "用登记的井冈霉素、噻呋酰胺类药剂对准茎基部喷雾，重病田 10~15 天后再补一次。"
                      "避免过量施氮，适当降低播种量。",
       differentiate="与全蚀病区分：纹枯病是叶鞘云纹状斑、黑脚不明显；"
                     "全蚀病「黑脚」典型（茎基一、二节变黑），拔根可见根全部变黑。",
       occurrence="冬季温暖、春季多雨潮湿年份重，稻麦轮作田菌源多。"),

    _d(standard_name="小麦全蚀病", aliases=["小麦黑脚病"], category="disease",
       crop_names=["小麦"], pathogen="禾顶囊壳小麦变种（Gaeumannomyces graminis var. tritici）",
       severity="high",
       symptoms="典型「黑脚」症状：茎基第一、二节变黑腐烂，抹去可见黑色菌丝鞘；"
                "抽穗后病株成簇枯死形成白穗，拔起极省力，"
                "根系全部变黑腐烂。灌浆期白穗在田间呈点片分布并逐年扩大。",
       conditions={"temp_min": 12, "temp_max": 18, "humidity_min": 70},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="检疫性土传病害，重在预防：无病田不留病种、病麦残体不还田，"
                      "重病田改种非寄主作物 2~3 年。病田用登记的硅噻菌胺拌种处理，"
                      "增施有机肥和磷钾肥可明显减轻危害。",
       differentiate="与纹枯病区分：全蚀病「黑脚 + 根全黑 + 白穗成片」；"
                     "纹枯病云纹斑为主、根不黑、白穗零星。"),

    _d(standard_name="小麦叶锈病", aliases=["小麦黄疸病"], category="disease",
       crop_names=["小麦"], pathogen="小麦隐匿柄锈菌（Puccinia triticina）",
       severity="medium",
       symptoms="叶片上散生橘红色夏孢子堆，圆形至椭圆形、较小，"
                "不规则散生在叶面（正面为主），后期叶背出现暗黑色冬孢子堆。"
                "严重时叶片大量枯死，灌浆受阻，籽粒瘪瘦。",
       conditions={"temp_min": 15, "temp_max": 22, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="与条锈病同防同治：发病中心出现后用登记的三唑类杀菌剂"
                      "（戊唑醇、丙环唑等）全田喷雾，重病田 10 天后再喷一次。"
                      "选用抗病品种是最经济的手段。",
       differentiate="锈病三兄弟区分口诀：「条锈成行叶锈乱，秆锈是个大红斑」—— "
                     "条锈病孢子堆沿叶脉排列成虚线状、鲜黄；"
                     "叶锈病孢子堆散乱、橘红、较小；秆锈病孢子堆大、突破叶背、褐红。",
       occurrence="春季雨水多、露水重时流行快，菌源随气流远距离传播。"),

    _d(standard_name="小麦麦蜘蛛", aliases=["麦圆蜘蛛", "麦长腿蜘蛛"], category="pest",
       crop_names=["小麦"], pathogen="",
       severity="medium",
       symptoms="以成若虫刺吸麦叶汁液：受害叶片出现细小白点，"
                "继而黄化干枯，严重时麦苗成片枯死似火烧状。"
                "虫体极小红褐色，受惊即坠落，早春晴天中午在叶面爬行最活跃。",
       conditions={"temp_min": 8, "temp_max": 15, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="早春巡查下部叶片发现黄白点时用药：用登记的杀螨剂或阿维菌素类喷雾，"
                      "利用其假死坠落习性可结合浇水和中耕控制。"
                      "旱作田、连作田发生重，灌水对麦圆蜘蛛有直接抑制作用。",
       differentiate="与蚜虫区分：麦蜘蛛造成的黄白点细碎、虫在叶背有红点会跳落；"
                     "蚜虫聚集嫩茎嫩叶、有蜜露发亮。两者常混发，可一并防治。",
       occurrence="春季干旱少雨年份大发生，水浇田轻于旱地。"),

    _d(standard_name="小麦吸浆虫", aliases=["麦红吸浆虫"], category="pest",
       crop_names=["小麦"], pathogen="",
       severity="medium",
       symptoms="幼虫在麦壳内吸食灌浆汁液：受害麦粒瘪瘦发暗，"
                "穗外观正常但产量大减，剥开麦壳可见橙红色小幼虫。"
                "抽穗期成虫在穗上产卵，傍晚活动最明显。",
       conditions={"temp_min": 15, "temp_max": 20, "humidity_min": 70,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "返青拔节期": "低", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="抽穗前（化蛹盛期）用登记的毒死蜱颗粒剂拌土撒施杀土中幼虫，"
                      "或抽穗扬花期用高效氯氟氰菊酯类喷雾杀成虫。"
                      "老虫窝田连年防治才能压低虫口。",
       differentiate="与赤霉病区分：吸浆虫危害籽粒瘪但穗不红不烂；"
                     "赤霉病穗上有粉红霉层、整穗枯白。剥壳看虫即可确诊。",
       occurrence="抽穗期阴雨潮湿利于成虫产卵和幼虫入侵，干旱年份轻。"),

    _d(standard_name="小麦白粉病", aliases=["小麦白毛病"], category="disease",
       crop_names=["小麦"], pathogen="禾布氏白粉菌小麦专化型（Blumeria graminis f.sp. tritici）",
       severity="high",
       symptoms="叶片表面（正面为主）出现白色霉点，扩大成灰白色霉层，"
                "后期霉层中散生黑色小颗粒（闭囊壳）。"
                "病叶早枯，植株矮小，成穗率低。密植郁闭、旺长麦田最重。",
       conditions={"temp_min": 15, "temp_max": 20, "humidity_min": 70},
       stage_susceptibility={"苗期": "低", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="控制播种量和氮肥，防止贪青旺长。拔节孕穗期病情指数上升时"
                      "用登记的三唑酮、戊唑醇类药剂喷雾防治，重病田 7~10 天后再喷一次。",
       differentiate="与叶锈病区分：白粉病是白色粉状霉层在叶面、能擦掉；"
                     "叶锈病是橘红色孢子堆、擦不掉。两者可同治（三唑类均有效）。",
       occurrence="阴雨寡照、田间郁闭、氮肥偏多的麦田易流行。"),

    _d(standard_name="小麦灰飞虱", aliases=["灰飞虱危害"], category="pest",
       crop_names=["小麦", "水稻", "玉米"], pathogen="",
       severity="medium",
       symptoms="成若虫群集麦株基部刺吸汁液，麦苗变黄长势弱；"
                "更重要的危害是传播小麦丛矮病：病株严重矮化、分蘖增多呈丛簇状，"
                "叶片上有黄绿相间条纹，不能抽穗或抽穗不结实。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "中", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="重点防丛矮病：小麦出苗前后用登记的吡蚜酮、噻虫嗪类药剂灭虫防毒，"
                      "清除田边杂草破坏其越冬场所。已感丛矮病的麦株无法治愈，只能治虫控病。",
       differentiate="与蚜虫区分：灰飞虱体灰褐色、短翅、聚集在基部近地面处；"
                     "蚜虫绿色体软、多在嫩叶穗部。丛矮病株丛簇矮化可作田间佐证。",
       occurrence="稻麦轮作区和玉米小麦套种区虫源充足，秋季温暖利于传毒。"),

    # ============================== 玉米（补 8 条） ============================== #
    _d(standard_name="玉米小斑病", aliases=["玉米斑点病"], category="disease",
       crop_names=["玉米"], pathogen="玉蜀黍平脐蠕孢（Bipolaris maydis）",
       severity="medium",
       symptoms="叶片上出现黄褐色小椭圆形病斑（长 5~10 毫米），"
                "边缘紫红色或深褐色，有 2~3 圈同心轮纹；"
                "湿度大时病斑上生灰黑色霉层。比大斑病的病斑小而多，"
                "严重时病斑连片，叶片枯死。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="抽雄前后是防控窗口：发病初期用登记的苯醚甲环唑、"
                      "吡唑醚菌酯类药剂喷雾。收获后清除秸秆减少菌源，"
                      "与大豆等轮作，选种抗病品种。",
       differentiate="与大斑病区分：小斑病斑小（5~10 毫米）、有轮纹、边缘紫红；"
                     "大斑病斑大（可达 30 毫米以上）、灰绿色长梭形、无轮纹。"),

    _d(standard_name="玉米锈病", aliases=["玉米普通锈病"], category="disease",
       crop_names=["玉米"], pathogen="高粱柄锈菌（Puccinia sorghi）",
       severity="medium",
       symptoms="叶片两面散生淡黄色至黄褐色夏孢子堆，圆形突起，"
                "后期表皮破裂散出锈色粉末；严重时叶片布满锈斑提前枯死，"
                "影响灌浆。南方晚播夏玉米发生重。",
       conditions={"temp_min": 24, "temp_max": 28, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="发病初期用登记的三唑类杀菌剂喷雾，重病田 10 天后补喷一次。"
                      "选种抗锈品种，避免偏晚播，收获后清除田间秸秆。",
       differentiate="与小斑病区分：锈病是凸起的锈色粉状孢子堆、破裂散粉；"
                     "小斑病是平的褐色轮纹斑、不散粉。"),

    _d(standard_name="玉米丝黑穗病", aliases=["玉米乌米"], category="disease",
       crop_names=["玉米"], pathogen="丝轴黑粉菌（Sporisorium reilianum）",
       severity="high",
       symptoms="苗期一般不显症，抽穗后雄穗变形膨大、穗内充满黑粉；"
                "雌穗短小不吐花丝，整个变成黑粉包，黑粉散落后只剩丝状维管束。"
                "病株多不结穗，一株毁一株。",
       conditions={"temp_min": 15, "temp_max": 25, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "低", "抽穗扬花期": "高", "灌浆期": "低"},
       control_advice="种子带菌 + 土壤带菌，以拌种为核心："
                      "用登记的戊唑醇、福美双类种衣剂包衣或拌种。"
                      "重病田轮作 3 年、拔除病株在黑粉散出前带出田外深埋，"
                      "严禁病株还田（黑粉孢子在土中可存活多年）。",
       differentiate="与瘤黑粉区分：丝黑穗病只危害穗部、雌穗整穗变黑粉包；"
                     "瘤黑粉在茎叶果穗各部位长肿瘤、瘤内也是黑粉但病瘤大而畸形。",
       occurrence="春播低温、播深过大延长出苗期时侵染加重。"),

    _d(standard_name="玉米瘤黑粉病", aliases=["玉米黑粉病"], category="disease",
       crop_names=["玉米"], pathogen="玉蜀黍黑粉菌（Ustilago maydis）",
       severity="medium",
       symptoms="植株茎、叶、腋芽、雌雄穗等任何地上部位长出大小不一的白色肿瘤，"
                "外表有光泽的膜，破裂后散出黑色粉状孢子。"
                "病株上部生长受阻，果穗受害则减产明显。",
       conditions={"temp_min": 26, "temp_max": 32, "humidity_min": 70},
       stage_susceptibility={"苗期": "中", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="减少伤口是关键：防治玉米螟等蛀茎害虫、冰雹大风后及时巡查。"
                      "病瘤在黑粉散出前割除带出田外深埋。种子用登记药剂处理可减轻苗期感染。",
       differentiate="与丝黑穗区分：瘤黑粉长「瘤」且各部位都可发生；"
                     "丝黑穗只坏穗部不长茎叶瘤。两者防治都以拌种和清园为主。"),

    _d(standard_name="玉米蚜虫", aliases=["玉米蚜"], category="pest",
       crop_names=["玉米", "小麦"], pathogen="",
       severity="medium",
       symptoms="成若蚜群集心叶、雄穗和上部叶片背面刺吸汁液，"
                "分泌蜜露引起煤污，抽雄后密集雄穗上危害最重，"
                "受害株长势弱、灌浆不足，还能传播病毒。",
       conditions={"temp_min": 23, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "低", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="蚜量达到防治指标时用登记的吡虫啉、噻虫嗪类药剂"
                      "对准心叶和雄穗喷雾，或用颗粒剂丢心。"
                      "天敌（瓢虫、草蛉）多的田块可减少用药。",
       differentiate="与红蜘蛛区分：蚜虫在心叶雄穗聚集成团有蜜露；"
                     "红蜘蛛在叶背拉丝结网、叶面出黄白小点。"),

    _d(standard_name="玉米黏虫", aliases=["行军虫", "剃枝虫"], category="pest",
       crop_names=["玉米", "小麦", "水稻"], pathogen="",
       severity="high",
       symptoms="幼虫暴食叶片：低龄啃食叶肉留下表皮呈天窗状，"
                "高龄把叶片吃成缺刻甚至吃光只剩叶脉，"
                "一夜之间可把一块田吃秃并成群迁往邻田（故名行军虫）。"
                "幼虫体背有白色纵纹，头壳黄褐色有「八」字纹。",
       conditions={"temp_min": 18, "temp_max": 25, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "中", "灌浆期": "低"},
       control_advice="成虫期杀虫灯 + 糖醋液诱杀。抓住低龄幼虫期（三龄前）"
                      "用登记的氯虫苯甲酰胺、苏云金杆菌类药剂突击防治，"
                      "高龄幼虫抗药且暴食，务必早防。",
       differentiate="与草地贪夜蛾区分：黏虫头壳有「八」字纹、背线白色；"
                     "草地贪夜蛾头壳有白色「Y」形纹、尾足有黑斑。两者防治药剂相近，"
                     "但贪夜蛾迁飞暴发性更强，发现后建议同时上报当地植保部门。",
       occurrence="多雨潮湿年份大发生，具有远距离迁飞习性。"),

    _d(standard_name="玉米粗缩病", aliases=["玉米矮花叶病"], category="disease",
       crop_names=["玉米"], pathogen="水稻黑条矮缩病毒（经灰飞虱传播）",
       severity="high",
       symptoms="病株严重矮化（不及健株一半），叶片浓绿、宽短僵直，"
                "叶背叶脉上出现蜡白色突起条纹；不能抽穗或穗极小，"
                "俗称「万年青苗」。一旦显症无法恢复。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "中", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="治虫防病：播前用内吸性种衣剂包衣，苗期灭灰飞虱；"
                      "调整播期避开第一代灰飞虱迁入高峰（麦收前后），"
                      "清除田边杂草。重病田改种其他作物一季。",
       differentiate="与药害区分：粗缩病叶背有蜡白色隆起条、株型僵直丛簇；"
                     "除草剂药害多有整片一致的畸形黄化史可查。",
       occurrence="麦套玉米和蒜茬玉米苗期正好撞上灰飞虱高峰，发病最重。"),

    _d(standard_name="玉米茎基腐病", aliases=["玉米青枯病"], category="disease",
       crop_names=["玉米"], pathogen="禾谷镰孢、腐霉等多种病原复合侵染",
       severity="high",
       symptoms="灌浆中后期突然成片青枯：整株叶片自下而上迅速失水干枯呈灰绿色，"
                "茎基部变软发褐、易倒伏，根部变黑腐烂。"
                "从显症到全株枯死常只需 3~5 天，乳熟至蜡熟期遇雨后骤晴最易暴发。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 80,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "低", "返青拔节期": "低", "抽穗扬花期": "中", "灌浆期": "高"},
       control_advice="目前以预防为主：选种抗病品种，实行秸秆清除或充分腐熟还田，"
                      "种衣剂包衣。发病后无有效救治手段，重在改善田间排水降低发病条件。",
       differentiate="与丝黑穗区分：茎基腐在灌浆期急性青枯倒伏、根黑；"
                     "丝黑穗是穗部黑粉包。两者都可能「整株废掉」但时期与形态完全不同。"),

    # ============================== 水稻（补 8 条） ============================== #
    _d(standard_name="水稻白叶枯病", aliases=["白叶瘟"], category="disease",
       crop_names=["水稻"], pathogen="水稻黄单胞菌（Xanthomonas oryzae pv. oryzae）",
       severity="high",
       symptoms="叶片病斑沿叶缘或中脉从叶尖向下扩展，初为暗绿色水浸状短线，"
                "后变黄白色长条斑，病健交界呈波纹状；"
                "清晨湿度大时病斑上可见蜜黄色珠状菌脓。"
                "暴发时全田稻叶枯白，似火烧。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 85,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "中", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="细菌病害，重在防：严禁病田水串灌，台风暴雨前用噻唑锌、"
                      "铜制剂等登记细菌药剂预防，发病中心立即封锁防治并停止下风田块串灌。"
                      "病草病谷不还田。",
       differentiate="与生理性叶尖枯黄区分：白叶枯病斑有波纹状病健交界和菌脓；"
                     "生理性枯黄无水浸状前沿、无菌脓。",
       occurrence="台风暴雨造成的伤口是主要侵入通道，沿并发症重的沿海稻区高发。"),

    _d(standard_name="水稻稻曲病", aliases=["丰收病"], category="disease",
       crop_names=["水稻"], pathogen="稻曲菌（Ustilaginoidea virens）",
       severity="medium",
       symptoms="只危害谷粒：颖壳内形成黄绿色球形「稻曲球」，"
                "比健粒大数倍，表面后期龟裂散出墨绿色粉末（厚垣孢子）。"
                "一穗上常只有少数谷粒发病，但污染稻谷并产生毒素，商品价值大降。",
       conditions={"temp_min": 24, "temp_max": 32, "humidity_min": 88,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "低", "返青拔节期": "低", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="防治窗口极窄，抓「破口前 5~7 天」这一遍药："
                      "用登记的苯甲·丙环唑、氟环唑类在破口期喷雾，"
                      "抽穗期多雨的年份药后 5~7 天再补一次。偏施氮肥的田块发病重。",
       differentiate="与稻粒黑粉病区分：稻曲球黄绿色大颗粒、墨绿粉；"
                     "黑粉病谷粒内散出黑粉、粒形不膨大。两者防治都在抽穗前后。",
       occurrence="抽穗扬花期遇连阴雨、氮肥过量、贪青晚熟的田块发病重。"),

    _d(standard_name="水稻恶苗病", aliases=["徒长病"], category="disease",
       crop_names=["水稻"], pathogen="串珠镰孢（Fusarium fujikuroi）",
       severity="medium",
       symptoms="秧苗期显症最典型：病苗显著比健苗高而细弱（可高出三分之一以上），"
                "叶片淡黄、叶狭长，根系发育不良，"
                "部分病苗在抽穗前枯死，茎基后期可见淡红色霉层。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="种子传播为主，浸种消毒是唯一有效手段："
                      "用登记的咪鲜胺、氰烯菌酯类浸种剂严格按浓度和时间浸种（25~28℃ 浸 48~72 小时），"
                      "催芽时温度不过高。已显症的病苗无法治疗，应拔除。",
       differentiate="与徒长苗区分：恶苗病苗细弱淡黄且陆续枯死；"
                     "正常壮秧粗壮叶绿。田间「鹤立鸡群」的苗多是恶苗病。",
       occurrence="种子带菌率高、浸种不彻底时大发生，育秧高温催芽加重。"),

    _d(standard_name="水稻二化螟", aliases=["钻心虫", "蛀秆虫"], category="pest",
       crop_names=["水稻", "茭白"], pathogen="",
       severity="high",
       symptoms="幼虫钻蛀稻茎：分蘖期造成枯心苗（心叶枯死卷成假枯心，拔出可见茎内幼虫），"
                "孕穗抽穗期造成枯孕穗和白穗，蛀孔处有虫粪。"
                "幼虫黄褐色，体背有 5 条褐色纵线。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="性诱剂 + 杀虫灯诱杀成虫，保护利用螟黄赤眼蜂。"
                      "抓住卵孵化盛期用登记的氯虫苯甲酰胺、苏云金杆菌类药剂喷雾，"
                      "保持 3~5 厘米水层提高药效。枯心白穗出现时再打药为时已晚。",
       differentiate="与稻纵卷叶螟区分：二化螟是钻茎的（枯心白穗、茎内虫）；"
                     "卷叶螟是卷叶的（叶苞、束叶）。部位完全不同。"),

    _d(standard_name="水稻稻纵卷叶螟", aliases=["卷叶虫", "刮青虫"], category="pest",
       crop_names=["水稻"], pathogen="",
       severity="high",
       symptoms="幼虫吐丝纵卷单叶成管状苞，躲在苞内啃食上表皮和叶肉，"
                "叶面出现一条条白色条斑（刮白），严重时全田「白叶」，"
                "光合面积大减导致秕谷。束叶间可见黄绿色幼虫。",
       conditions={"temp_min": 24, "temp_max": 28, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "低", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="田间赶蛾查蛾，蛾高峰后 7~10 天（卵孵化高峰）用登记的"
                      "氯虫苯甲酰胺、阿维菌素类药剂喷雾，"
                      "傍晚施药效果好。迁入峰到来前可结合灯光诱杀。",
       differentiate="与二化螟区分：卷叶螟危害叶片结白苞、不蛀茎；"
                     "二化螟蛀茎造成枯心白穗。看到白色条斑卷叶就想到卷叶螟。",
       occurrence="随台风和西南气流远距离迁入，阴雨高湿年份世代重叠暴发。"),

    _d(standard_name="水稻稻蓟马", aliases=["稻蓟马危害"], category="pest",
       crop_names=["水稻"], pathogen="",
       severity="low",
       symptoms="成若虫刮吸秧苗嫩叶汁液：叶尖及其两侧出现白色至黄褐色卷缩干枯，"
                "秧苗受害后像火烧尖，严重时秧苗成片枯黄、返青慢。"
                "虫体黑褐色细小，抖动叶片可见虫子弹跳。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="秧苗期是重点防治期：用登记的噻虫嗪类药剂拌种（浸种时加药）"
                      "可保苗 20~30 天，或叶尖卷缩率达 5% 时用药喷雾。"
                      "适当推迟栽插错开虫峰。",
       differentiate="与缺素黄尖区分：蓟马危害叶尖卷缩干枯、可查到虫；"
                     "缺素黄尖叶片平展不卷、全田均匀。"),

    _d(standard_name="水稻细菌性条斑病", aliases=["细条病"], category="disease",
       crop_names=["水稻"], pathogen="水稻黄单胞菌水稻生致病变种（Xanthomonas oryzae pv. oryzicola）",
       severity="medium",
       symptoms="叶片上初为暗绿色水浸状半透明小条斑，"
                "沿叶脉扩展成长 1~2 厘米的黄褐色细条斑，"
                "对光看半透明；湿度大时条斑上溢出成串的蜡黄色小菌脓珠。"
                "是检疫性细菌病害。",
       conditions={"temp_min": 26, "temp_max": 32, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="不从病区调种。台风暴雨后用噻唑锌、中生菌素等登记细菌药剂防治，"
                      "发病田块水口隔离防串灌。病草不作覆盖物。",
       differentiate="与白叶枯病区分：细条病斑沿叶脉多条并行、窄而半透明、"
                     "菌脓成珠状密布；白叶枯病斑从叶缘大片扩展、波纹状边界。"),

    _d(standard_name="水稻稻苞虫", aliases=["直纹稻弄蝶"], category="pest",
       crop_names=["水稻"], pathogen="",
       severity="low",
       symptoms="幼虫吐丝把数片稻叶缀合成虫苞，藏身苞内啃食叶肉，"
                "叶面出现缺刻和白色膜斑，严重时虫苞累累、稻叶残缺。"
                "幼虫绿色纺锤形，头部有「王」字形褐纹，受惊吐丝下坠。",
       conditions={"temp_min": 24, "temp_max": 30, "humidity_min": 70},
       stage_susceptibility={"苗期": "低", "返青拔节期": "中", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="零星发生可人工捏苞或用扫帚扫落。虫量达标时"
                      "在低龄期用登记的苏云金杆菌、阿维菌素类喷雾，"
                      "傍晚幼虫出苞取食时用药效果最好。",
       differentiate="与稻纵卷叶螟区分：稻苞虫把多叶缀成大苞、虫大体绿；"
                     "卷叶螟单叶纵卷成细苞、虫小而黄绿。"),

    # ============================== 马铃薯（补 7 条） ============================== #
    _d(standard_name="马铃薯环腐病", aliases=["马铃薯轮腐病"], category="disease",
       crop_names=["马铃薯"], pathogen="环腐棒形杆菌（Clavibacter michiganensis subsp. sepedonicus）",
       severity="medium",
       symptoms="植株矮化萎蔫、叶缘上卷变黄，常一侧先显症；"
                "块茎横切面可见维管束环变黄褐色，用手挤压溢出乳黄色黏液，"
                "重病薯皮层与髓部分离呈环状腐烂，摇动时有空心响声。",
       conditions={"temp_min": 18, "temp_max": 25, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="细菌种传病害，切块播种是主要扩散途径："
                      "选用脱毒整薯播种，切刀用 75% 酒精或开水烫刀轮换消毒，"
                      "种薯温汤处理。发现病株连同薯块整穴拔除。",
       differentiate="与黑胫病区分：环腐病维管束环变褐、挤压出黏液、薯皮完整；"
                     "黑胫病茎基发黑腐烂、薯肉从髓心向外烂并有恶臭。",
       occurrence="窖藏温度偏高时病薯带菌率上升，来年种薯带病出苗即成病源。"),

    _d(standard_name="马铃薯黑胫病", aliases=["马铃薯黑腿病"], category="disease",
       crop_names=["马铃薯"], pathogen="胡萝卜软腐果胶杆菌马铃薯变种（Pectobacterium atrosepticum）",
       severity="medium",
       symptoms="幼苗矮小僵硬、叶色淡黄、顶部叶片向上卷；"
                "茎基部（黑胫）变黑腐烂，湿度大时极易拔起；"
                "块茎从脐部（连接茎的一端）开始髓心腐烂发黑，有恶臭。",
       conditions={"temp_min": 18, "temp_max": 24, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "中", "灌浆期": "低"},
       control_advice="种薯带菌为主：选用无病脱毒种薯，整薯播种，"
                      "切刀消毒同环腐病。雨季及时排水，收后晾干入窖，"
                      "窖温控制在 1~4℃ 低温通风贮藏。",
       differentiate="与青枯病区分：黑胫病茎基发黑有恶臭、薯从脐部烂；"
                     "青枯病维管束变褐、挤压黏液多、薯皮常完好。"),

    _d(standard_name="马铃薯病毒病", aliases=["马铃薯皱缩花叶病"], category="disease",
       crop_names=["马铃薯"], pathogen="马铃薯Y病毒（PVY）、马铃薯X病毒（PVX）、卷叶病毒（PLRV）等",
       severity="high",
       symptoms="花叶型：叶片深浅绿斑驳、皱缩畸形；卷叶型：下部叶片边缘向上卷成筒状、"
                "革质化、叶背发红紫；病株矮化，块茎变小、畸形，"
                "连年留种则种性退化（俗称「串头」）。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "中", "灌浆期": "低"},
       control_advice="防蚜是核心：出苗后防蚜虫（尤其迁飞有翅蚜），"
                      "高海拔冷凉区建立脱毒种薯繁育基地；"
                      "生产田不留种，定期换用脱毒种薯是唯一根治途径。",
       differentiate="与缺素区分：病毒病叶片卷曲皱缩伴斑驳、矮化；"
                     "缺素叶片黄化但平整无斑驳。",
       occurrence="高温干旱年份蚜虫活跃，传毒重；二季作区退化快。"),

    _d(standard_name="马铃薯疮痂病", aliases=["马铃薯放线菌病"], category="disease",
       crop_names=["马铃薯", "甜菜"], pathogen="疮痂链霉菌（Streptomyces scabies）",
       severity="low",
       symptoms="块茎表面出现褐色木栓化疮痂状病斑，凸起或凹陷，"
                "表皮粗糙如痂壳，深浅不一；只影响薯皮商品性，"
                "植株地上部通常无症状。",
       conditions={"temp_min": 20, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "低", "返青拔节期": "低", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="块茎形成期保持土壤均匀湿润（此期干旱是主要诱因）；"
                      "避免在碱性土壤种薯（该菌喜碱），种前可施硫磺粉调酸；"
                      "与非寄主作物轮作，不用未腐熟农家肥。",
       differentiate="与粉痂病区分：疮痂病斑干燥木栓化、不规则；"
                     "粉痂病斑内散出褐色粉末状休眠孢子球、皮下有海绵状组织。"),

    _d(standard_name="马铃薯青枯病", aliases=["马铃薯细菌性枯萎病"], category="disease",
       crop_names=["马铃薯", "番茄", "辣椒"], pathogen="青枯雷尔氏菌（Ralstonia solanacearum）",
       severity="high",
       symptoms="植株急性萎蔫而叶色尚绿，早晚短暂恢复后枯死；"
                "块茎维管束变褐，切面挤压溢出乳白色菌脓，"
                "芽眼处常渗出带菌泥土样物。高温高湿田块成片死株。",
       conditions={"temp_min": 25, "temp_max": 35, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "高"},
       control_advice="检疫性细菌病害：用无病脱毒种薯，切刀严格消毒，"
                      "病田 3 年内不种茄科作物，病穴撒石灰。"
                      "发病初期灌根可缓解但难根除，重在种薯净化。",
       differentiate="与环腐病区分：青枯病急性青枯萎蔫、维管束环轻变褐；"
                     "环腐病慢性卷叶黄化、维管束环明显褐变并呈环状腐烂。"),

    _d(standard_name="马铃薯二十八星瓢虫", aliases=["酸浆瓢虫"], category="pest",
       crop_names=["马铃薯", "茄子", "番茄"], pathogen="",
       severity="low",
       symptoms="成幼虫啃食叶肉留下表皮呈网状「天窗」，"
                "叶面可见密集平行排列的细密食痕，严重时叶片枯萎整株死亡；"
                "也啃食薯块表面形成深浅不一的凹痕。"
                "成虫为黄褐色半球形瓢虫，两鞘翅共 28 个黑斑。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "返青拔节期": "高", "抽穗扬花期": "高", "灌浆期": "中"},
       control_advice="成虫越冬出蛰期和产卵高峰期用登记的氯氰菊酯类喷雾，"
                      "卵块颜色鲜黄成块、易人工摘除。利用成虫假死性清晨振落捕杀。",
       differentiate="与有益瓢虫区分：二十八星瓢虫体较大、28 个黑斑、鞘翅有细毛；"
                     "七星瓢虫 7 个黑斑、捕食蚜虫，不要误杀。"),

    _d(standard_name="马铃薯块茎蛾", aliases=["马铃薯麦蛾"], category="pest",
       crop_names=["马铃薯", "番茄", "烟草"], pathogen="",
       severity="low",
       symptoms="幼虫蛀食叶片形成透明弯曲隧道，也沿芽眼蛀入薯块形成弯曲虫道，"
                "孔口有虫粪和丝网，贮运期危害最重，"
                "薯块被蛀后易感染软腐病菌整薯烂掉。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "返青拔节期": "中", "抽穗扬花期": "中", "灌浆期": "中"},
       control_advice="田间培土要厚（防成虫在露出地面的薯块上产卵），"
                      "贮藏期用纱网封窗、敌敌畏挂条熏蒸，"
                      "入库前挑除带虫薯块。性诱剂可用于监测和诱杀。",
       differentiate="与蛴螬区分：块茎蛾孔口有丝网虫粪、虫道弯曲在芽眼附近、虫体小；"
                     "蛴螬孔大而圆、虫体肥白、多在田间危害。"),

    # ============================== 苹果（补 8 条） ============================== #
    _d(standard_name="苹果白粉病", aliases=["苹果白毛病"], category="disease",
       crop_names=["苹果"], pathogen="白叉丝单囊壳（Podosphaera leucotricha）",
       severity="medium",
       symptoms="主要危害新梢嫩叶：受害新梢叶片细长卷曲、布满白粉、"
                "节间缩短甚至枯死；病芽萌发晚、干瘦；"
                "幼果受害在萼洼处出现白色粉斑，后期形成锈斑。",
       conditions={"temp_min": 18, "temp_max": 25, "humidity_min": 60},
       stage_susceptibility={"苗期": "中", "开花期": "高", "结果期": "中"},
       control_advice="剪除病梢（白粉肉眼可见）是省钱省药的核心手段，剪后带出果园烧毁；"
                      "萌芽期和落花后用登记的三唑类（三唑酮、苯醚甲环唑）药剂各喷一次。"
                      "避免偏施氮肥徒长。",
       differentiate="与白粉虱危害区分：白粉病是菌丝粉层、擦掉后叶面有黄斑；"
                     "虫粉是虫体和蜡质、见虫可见。该菌耐干燥，干旱年份也流行。"),

    _d(standard_name="苹果炭疽病", aliases=["苹果苦腐病"], category="disease",
       crop_names=["苹果"], pathogen="胶孢炭疽菌（Colletotrichum gloeosporioides）",
       severity="high",
       symptoms="果实近成熟期发病：果面初为淡褐色水浸状圆斑，迅速扩大软腐，"
                "病斑凹陷并出现同心轮纹排列的黑色小点（分生孢子盘），"
                "潮湿时溢出粉红色黏孢子团。病果脱落或干缩成僵果挂在树上。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 85,
                   "rain_hours_min": 4},
       stage_susceptibility={"苗期": "低", "开花期": "低", "结果期": "高"},
       control_advice="幼果期开始是防控关键：谢花后 10 天起用登记的"
                      "咪鲜胺、苯醚甲环唑类药剂 10~15 天一次。"
                      "清除僵果病枝减少菌源，套袋栽培效果显著。",
       differentiate="与轮纹病区分：炭疽病病斑凹陷软腐、同心黑点排轮纹、粉红黏液；"
                     "轮纹病病斑不凹陷、水烂状、无粉红黏液。两者都危害果实。"),

    _d(standard_name="苹果斑点落叶病", aliases=["苹果褐纹病"], category="disease",
       crop_names=["苹果"], pathogen="苹果链格孢强毒株系（Alternaria mali）",
       severity="high",
       symptoms="嫩叶期即开始侵染：叶片出现直径 3~6 毫米的红褐色圆形病斑，"
                "中心深褐边缘紫褐，潮湿时病斑背面有黑色霉层；"
                "病斑破裂穿孔，叶片早落。春秋两季新梢期危害最重，"
                "元帅系品种高度感病。",
       conditions={"temp_min": 22, "temp_max": 28, "humidity_min": 80,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "开花期": "中", "结果期": "高"},
       control_advice="新梢速长期（5~6 月、8~9 月）两个高峰期用药："
                      "用登记的代森锰锌、多抗霉素类交替喷雾。"
                      "感病品种（元帅系、嘎啦）套袋前重点防。及时剪除病梢。",
       differentiate="与褐斑病区分：斑点落叶病斑小而圆、霉层在叶背病斑上；"
                     "褐斑病斑大而不规则、有同心环纹、后期整叶黄化脱落。"),

    _d(standard_name="苹果锈病", aliases=["苹果赤星病"], category="disease",
       crop_names=["苹果"], pathogen="山田胶锈菌（Gymnosporangium yamadae）",
       severity="medium",
       symptoms="叶面出现橙黄色圆形病斑，中心有橙黄色小粒点（性孢子器），"
                "后期病斑背面隆起长出灰白色羊须状毛管（锈孢子器）；"
                "幼果受害在果面形成橙黄色斑点导致畸形。"
                "只在苹果和桧柏类转主寄主共存时发生。",
       conditions={"temp_min": 15, "temp_max": 25, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "中", "开花期": "高", "结果期": "中"},
       control_advice="根治靠清除菌源：果园周围 2~3 公里内的桧柏、龙柏"
                      "春季前剪除菌瘿或喷药封锁。苹果萌芽至幼果期（担孢子飞散期）"
                      "用登记的三唑类药剂保护性喷雾 2~3 次。",
       differentiate="与褐斑病区分：锈病斑橙黄色圆形、背面有羊须状毛管、"
                     "与桧柏相邻必发；褐斑病褐色不规则、无毛管。"),

    _d(standard_name="苹果绣线菊蚜", aliases=["苹果黄蚜"], category="pest",
       crop_names=["苹果", "梨"], pathogen="",
       severity="low",
       symptoms="成若蚜群集嫩芽嫩叶背面刺吸：受害叶向叶背横卷、"
                "布满黄色蚜虫和白色蜕皮壳，分泌蜜露污染叶果并诱发煤污。"
                "新梢生长受抑制，幼树危害尤重。",
       conditions={"temp_min": 20, "temp_max": 27, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "开花期": "高", "结果期": "中"},
       control_advice="卷叶前（萌芽后新梢抽发期）用登记的吡虫啉、"
                      "双丙环虫酯类药剂喷雾；保护瓢虫、食蚜蝇等天敌；"
                      "剪除受害严重的徒长新梢。蚜虫世代极快，发现要早治。",
       differentiate="与苹果瘤蚜区分：黄蚜体鲜黄、叶横卷；"
                     "瘤蚜体暗绿、叶纵卷变厚脆且有瘤状凸起。防治药剂相近。"),

    _d(standard_name="苹果桃小食心虫", aliases=["桃小", "食心虫"], category="pest",
       crop_names=["苹果", "桃", "梨", "枣"], pathogen="",
       severity="high",
       symptoms="幼虫蛀果：蛀孔小而圆、流出白色泪珠状果胶，干后成白色蜡膜；"
                "幼虫在果内纵横蛀食并排粪成「豆沙馅」，"
                "果面变形呈「猴头果」。脱果孔大而明显。",
       conditions={"temp_min": 20, "temp_max": 27, "humidity_min": 60},
       stage_susceptibility={"苗期": "低", "开花期": "低", "结果期": "高"},
       control_advice="性诱剂诱捕监测 + 树盘覆膜杀越冬出土幼虫；"
                      "性诱剂连续诱到成虫后一周左右（卵果率达 1%）"
                      "用登记的氯虫苯甲酰胺、毒死蜱类喷雾杀卵和初孵幼虫，"
                      "蛀入果内后无法防治。",
       differentiate="与梨小食心虫区分：桃小蛀孔有蜡膜泪珠、果肉豆沙馅、猴头果；"
                     "梨小蛀孔周围变褐、直达果心。性诱剂可分虫种监测。",
       occurrence="一年 1~2 代，幼虫在树盘土壤 3~10 厘米处结茧越冬，雨后出土整齐。"),

    _d(standard_name="苹果绵蚜", aliases=["血色蚜虫"], category="pest",
       crop_names=["苹果"], pathogen="",
       severity="medium",
       symptoms="群集在枝干剪锯口、伤疤、新梢基部和根部吸汁："
                "被害处形成肿瘤状突起，虫体表面覆盖大量白色绵絮状蜡丝，"
                "挤压有红色汁液（故称血色蚜虫）；根部受害形成根瘤，树势衰弱。",
       conditions={"temp_min": 15, "temp_max": 25, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "开花期": "低", "结果期": "高"},
       control_advice="检疫性害虫。休眠期刮除粗皮翘皮并用石硫合剂清园；"
                      "生长期用登记的吡虫啉、噻虫嗪类涂抹或喷雾，"
                      "重点喷剪锯口和肿瘤处；剪除受害枝条烧毁。",
       differentiate="与白粉虱等区分：绵蚜在枝干和根部、白色绵毛状蜡丝、红色体液；"
                     "粉虱在叶片背面。剪锯口「长白毛带红水」即绵蚜特征。"),

    _d(standard_name="苹果苦痘病", aliases=["苹果苦陷病"], category="physiological",
       crop_names=["苹果"], pathogen="非生物性（缺钙引起）",
       severity="low",
       symptoms="果实表面出现深绿色或暗红色凹陷圆斑，"
                "皮下果肉坏死变褐、海绵状，味苦；"
                "多发生在萼洼附近和大果上，贮藏期加重。钙不足是根本原因。",
       conditions={"temp_min": 0, "temp_max": 45, "humidity_min": 0},
       stage_susceptibility={"苗期": "低", "开花期": "低", "结果期": "高"},
       control_advice="补钙是关键：谢花后 4~6 周内（果实吸收钙的高峰）"
                      "叶面喷施糖醇钙、氯化钙类叶面肥 2~3 次；"
                      "控制氮肥避免旺长；贮藏期保持低温高湿减缓发病。",
       differentiate="与炭疽病区分：苦痘病斑皮下海绵状苦味、果面凹陷但无霉层；"
                     "炭疽病软腐、有同心黑点和粉红黏液。切开果肉一尝便知。"),

    # ============================== 跨作物（补 8 条） ============================== #
    _d(standard_name="地老虎", aliases=["切根虫", "土蚕"], category="pest",
       crop_names=["玉米", "蔬菜", "马铃薯"], pathogen="",
       severity="medium",
       symptoms="幼虫夜间咬断幼苗近地面茎部，清晨可见整株苗被切倒在地或拖入土洞，"
                "苗期缺苗断垄是其典型危害状。高龄幼虫灰褐色体粗壮，"
                "受惊卷成 C 形，白天躲藏于被害株附近的浅土层中。",
       conditions={"temp_min": 15, "temp_max": 25, "humidity_min": 60},
       stage_susceptibility={"苗期": "高", "返青拔节期": "中", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="清晨顺断苗的土洞挖除幼虫最直接；"
                      "糖醋液和杀虫灯诱杀成虫；用辛硫磷颗粒剂拌毒土撒苗根或毒饵诱杀。"
                      "杂草多的地块产卵多，播前除草很重要。",
       differentiate="与蛴螬区分：地老虎切苗在地表处、虫灰褐卷曲；"
                     "蛴螬在地下啃食根系种薯、虫白色肥壮 C 形。"),

    _d(standard_name="蛴螬", aliases=["金龟子幼虫", "白土蚕"], category="pest",
       crop_names=["花生", "马铃薯", "玉米", "蔬菜"], pathogen="",
       severity="medium",
       symptoms="幼虫在地下啃食萌发种子、幼根和块茎："
                "苗期咬断根系致苗枯死（断口参差不齐），"
                "薯块和花生荚果被蛀成孔洞；成虫金龟子啃食果树嫩芽叶片。"
                "虫体乳白色、体肥多皱褶、头红褐色、常蜷曲成 C 形。",
       conditions={"temp_min": 20, "temp_max": 28, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "返青拔节期": "高", "抽穗扬花期": "中", "灌浆期": "中"},
       control_advice="播前用辛硫磷颗粒剂或毒死蜱颗粒剂拌土撒施并浅锄混土；"
                      "生长期发现危害可用药液灌根。成虫期灯光诱杀金龟子。"
                      "未腐熟厩肥招引产卵，务必腐熟后再施用。",
       differentiate="与地老虎区分：蛴螬在地下啃根吃薯、体白肥 C 形；"
                     "地老虎咬断地表茎、体灰褐。挖开被害株根部即可分辨。"),

    _d(standard_name="蜗牛与蛞蝓", aliases=["旱螺蛳", "鼻涕虫"], category="pest",
       crop_names=["蔬菜", "甘蓝", "辣椒", "马铃薯"], pathogen="",
       severity="low",
       symptoms="啃食嫩叶幼果：叶面形成孔洞或缺刻，孔洞边缘光滑，"
                "爬过处留下银白色发亮的黏液痕迹（胶状膜），"
                "这是确认的最好证据。阴雨后和清晨危害明显。",
       conditions={"temp_min": 15, "temp_max": 25, "humidity_min": 85,
                   "rain_hours_min": 2},
       stage_susceptibility={"苗期": "高", "返青拔节期": "中", "抽穗扬花期": "低", "灌浆期": "低"},
       control_advice="四聚乙醛颗粒剂撒于行间诱杀效果最好；"
                      "清除田边杂草和堆石（白天地里躲藏场所），"
                      "傍晚在田间放湿草堆或瓦片诱集清晨集中消灭。",
       differentiate="与跳甲区分：蜗牛蛞蝓留下银白黏液道、孔洞光滑；"
                     "跳甲啃叶背成密集小孔、无黏液、虫会跳。"),

    _d(standard_name="朱砂叶螨", aliases=["红蜘蛛", "棉红蜘蛛"], category="pest",
       crop_names=["黄瓜", "辣椒", "茄子", "番茄", "豆类"], pathogen="",
       severity="medium",
       symptoms="聚集叶背刺吸：叶面先出现针尖大小的黄白色失绿斑点，"
                "继而整叶灰白枯焦脱落，叶背可见红色小螨和丝网，"
                "高温干燥时 5~7 天繁殖一代、爆发式蔓延。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 0},
       stage_susceptibility={"苗期": "中", "开花期": "高", "结果期": "高"},
       control_advice="干旱棚室重点防：发现中心株（黄白斑点叶）立即用药，"
                      "用登记的阿维菌素、螺螨酯类杀螨剂重点喷叶背，"
                      "5~7 天一次连续 2~3 次并轮换药剂；"
                      "增加空气湿度可抑制繁殖。",
       differentiate="与茶黄螨区分：叶螨叶面黄白细点密集、叶背红点拉网；"
                     "茶黄螨危害嫩叶卷曲油亮、叶面无细点。两者可同杀螨剂兼治。",
       occurrence="高温干燥（25~32℃、湿度 60% 以下）时指数增长，连栋棚风口附近先发。"),

    _d(standard_name="斜纹夜蛾", aliases=["莲纹夜蛾"], category="pest",
       crop_names=["甘蓝", "芋头", "辣椒", "番茄", "大豆"], pathogen="",
       severity="medium",
       symptoms="低龄幼虫群集叶背啃食叶肉留下上表皮呈灰白筛网状，"
                "三龄后分散暴食成孔洞缺刻，一夜可吃光整株叶片；"
                "幼虫体色黑褐，体背有橙黄色纵线和月牙形黑斑。",
       conditions={"temp_min": 25, "temp_max": 30, "humidity_min": 80},
       stage_susceptibility={"苗期": "高", "开花期": "中", "结果期": "高"},
       control_advice="低龄期的「筛网状危害中心」是最佳防治靶标："
                      "发现即对中心用氯虫苯甲酰胺、苏云金杆菌类登记药剂点喷并全田普治；"
                      "成虫用性诱剂和杀虫灯诱杀。卵成块产在叶背，可人工摘卵叶。",
       differentiate="与甜菜夜蛾区分：斜纹夜蛾体背有橙黄纵线月牙斑、群集危害；"
                     "甜菜夜蛾体色绿至黑褐、体背有浅色气门后线。防治药剂可通用。",
       occurrence="夏秋季高温年份暴发，成虫迁飞能力强。"),

    _d(standard_name="甜菜夜蛾", aliases=["贪夜蛾"], category="pest",
       crop_names=["甘蓝", "大葱", "辣椒", "花生"], pathogen="",
       severity="medium",
       symptoms="幼虫啃食叶片成孔洞缺刻，钻蛀大葱管叶、辣椒果实内部危害，"
                "虫粪排在孔口；低龄吐丝结薄网群集叶背。"
                "幼虫体色多变（绿至黑褐），气门后上方有白色小点连线。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 60},
       stage_susceptibility={"苗期": "高", "开花期": "中", "结果期": "高"},
       control_advice="抗药性强，务必低龄期（三龄前）防治并轮换不同作用机制的"
                      "登记药剂（氯虫苯甲酰胺、甲维盐、虫螨腈等），傍晚施药；"
                      "杀虫灯 + 性诱剂降低虫口基数。",
       differentiate="与斜纹夜蛾区分：甜菜夜蛾体色多变但气门上线有白点、"
                     "常钻蛀葱管果实内部；斜纹夜蛾橙黄纵线群集叶面暴食。"),

    _d(standard_name="美洲斑潜蝇", aliases=["蔬菜斑潜蝇"], category="pest",
       crop_names=["番茄", "黄瓜", "豆类", "辣椒"], pathogen="",
       severity="medium",
       symptoms="幼虫在叶片内潜食叶肉：叶面出现灰白色蛇形弯曲隧道，"
                "隧道终端常有黑色小点（虫粪），叶片上隧道密集时"
                "光合面积大减、叶片早枯。成虫产卵刺孔在叶面留下白色小点。",
       conditions={"temp_min": 22, "temp_max": 30, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "开花期": "中", "结果期": "高"},
       control_advice="悬挂黄色粘板监测和诱杀成虫；摘除带隧道的老叶带出棚外销毁；"
                      "幼虫低龄期（隧道 2 厘米内）用灭蝇胺、阿维菌素类登记药剂喷雾，"
                      "蛀道深长后防效差。",
       differentiate="与潜叶蛾区分：美洲斑潜蝇隧道灰白蛇形、末端黑点虫粪、"
                     "主要在蔬菜瓜果；豌豆彩潜蝇隧道更细。按「潜叶类」统一用药即可。"),

    _d(standard_name="烟粉虱", aliases=["银叶粉虱"], category="pest",
       crop_names=["番茄", "黄瓜", "辣椒", "茄子", "甘蓝"], pathogen="",
       severity="high",
       symptoms="成若虫群集叶背刺吸汁液，分泌蜜露诱发煤污病，"
                "叶面发黑影响光合；危害番茄时可传播黄化曲叶病毒（TYLCV），"
                "造成「矮缩黄化减产绝收」；虫体极小白而微黄，受惊起飞成白雾状。",
       conditions={"temp_min": 25, "temp_max": 32, "humidity_min": 0},
       stage_susceptibility={"苗期": "高", "开花期": "高", "结果期": "高"},
       control_advice="防重于治：棚室通风口装 40 目防虫网、张挂黄色粘板；"
                      "释放丽蚜小蜂是效果最好的生物防治；"
                      "用登记的螺虫乙酯、双丙环虫酯类轮换喷雾，"
                      "打药重点喷叶背并早防（该虫极易抗药）。",
       differentiate="与白粉虱区分：烟粉虱体型更小、前翅合拢呈屋脊状、"
                     "传播 TYLCV 引起番茄黄化曲叶病；温室白粉虱体型大、"
                     "主要在温室危害。田间两者常混发，防治方法相同。",
       occurrence="设施内全年繁殖，夏季高温世代极短，7~9 月虫量最大。"),
]

# ---------------------------------------------------------------------------
# 鉴别类 / 通用知识长文档（第 4 批，2026-09-26）
#
# 为什么单列一批：第 2 批扩充后评测暴露出「同作物近似条目互相稀释召回」，
# 而鉴别类文档本身就是知识库里最缺、农户最需要的内容 ——
# 「A 和 B 怎么区分」直接回答混淆问题，也把标题感知切块器真正用起来
# （这批每篇 600~900 字、3~4 节，会被切成多个独立可检索的 chunk）。
# ---------------------------------------------------------------------------
GENERAL_KNOWLEDGE_EXT: list[dict] = [
    {
        "title": "小麦蚜虫和麦蜘蛛怎么区分",
        "category": "general",
        "crop_names": ["小麦"],
        "disease_name": "小麦蚜虫",
        "growth_stage": None,
        "tags": ["鉴别", "蚜虫", "麦蜘蛛"],
        "source": "公开农业植保知识整理（待补充文献出处）",
        "content": "## 识别要点\n"
                   "两者都是春季麦苗上的「小虫」，但危害部位和外观完全不同：\n"
                   "**蚜虫**体软、绿色或黄绿色，聚成密集一团爬在麦穗、嫩茎和嫩叶上，"
                   "虫体表面和周围发黏发亮（分泌蜜露，后期还会招来黑色煤污）；\n"
                   "**麦蜘蛛**是红褐色的小螨，体表光滑不发黏，多在叶片背面活动，"
                   "受害叶面出现密集的针尖大小黄白色失绿小点，虫受惊会迅速坠落逃走。\n"
                   "## 危害表现差异\n"
                   "蚜虫危害后叶片发黄卷曲、蜜露发亮；麦蜘蛛危害后叶片灰白似火烧、"
                   "叶片上看不到黏液。抓一把麦叶抖一抖：蚜虫粘在叶上不落，"
                   "麦蜘蛛纷纷落地。\n"
                   "## 防治要点\n"
                   "蚜虫用吡虫啉类、噻虫嗪类；麦蜘蛛用阿维菌素类杀螨剂，"
                   "并结合浇水镇压。两类虫常混发，联苯菊酯类可兼治。\n"
                   "注意：具体药剂以登记标签和当地植保部门指导意见为准。",
    },
    {
        "title": "玉米螟和草地贪夜蛾怎么区分",
        "category": "general",
        "crop_names": ["玉米"],
        "disease_name": "玉米螟",
        "growth_stage": None,
        "tags": ["鉴别", "玉米螟", "草地贪夜蛾", "黏虫"],
        "source": "公开农业植保知识整理（待补充文献出处）",
        "content": "## 识别要点\n"
                   "三种钻蛀/暴食性夜蛾类害虫在玉米上常被混为一谈，看幼虫头部即可分辨：\n"
                   "**玉米螟**幼虫头壳深褐色无白纹，蛀孔成一排排整齐的小孔"
                   "（「一排箭孔」是它的招牌），多在心叶和茎秆内危害；\n"
                   "**草地贪夜蛾**幼虫头壳有明显的白色「Y」形纹，"
                   "第 8、9 腹节各有两个深褐色斑，危害心叶时孔洞大小不一、"
                   "伴随大量颗粒状虫粪，暴发性强；\n"
                   "**黏虫**头壳有浅褐色「八」字纹，成群啃食叶片成缺刻，"
                   "会整群迁移（故名行军虫）。\n"
                   "## 危害部位对照\n"
                   "玉米螟：心叶排孔、茎内隧道、雄穗折断；\n"
                   "草地贪夜蛾：心叶不规则孔洞加虫粪堆积，也能蛀果穗；\n"
                   "黏虫：主要吃叶，孔洞边缘不整齐。\n"
                   "## 防治要点\n"
                   "三者都以「低龄幼虫期」为防治窗口，氯虫苯甲酰胺类、"
                   "苏云金杆菌类均有效；草地贪夜蛾发现后建议同时上报当地植保部门。\n"
                   "注意：具体药剂以登记标签为准。",
    },
    {
        "title": "农药为什么越用越没效果：抗药性的产生与延缓",
        "category": "safety",
        "crop_names": [],
        "disease_name": None,
        "growth_stage": None,
        "tags": ["抗药性", "科学用药", "轮换用药"],
        "source": "公开农业植保知识整理（待补充文献出处）",
        "content": "## 现象\n"
                   "同一个药刚上市时「一打就灵」，用几年后剂量越加越大却越打越没效 —— "
                   "这就是抗药性：病原菌或害虫群体里，天生带抗性基因的个体被药筛选出来，"
                   "繁殖几年后整个种群都「不怕」这种药了。\n"
                   "## 为什么会越来越快\n"
                   "三类做法在加速筛选：一年打同一个药十几次；"
                   "见效慢就随意加量；复配的同成分换汤不换药（商品名不同、"
                   "有效成分相同）。灰霉病、白粉虱、小菜蛾这类繁殖快的对象抗药性发展最快。\n"
                   "## 怎么延缓\n"
                   "一要**轮换作用机制**：看包装上的作用机制分类（如 FRAC/IRAC 编号），"
                   "连续用药不超过 2~3 次就换成不同编号的药；\n"
                   "二要**按标签剂量**用足，剂量不足等于替抗性个体做「体检式筛选」；\n"
                   "三要**配合农业防治**降基数：清园、轮作、通风降湿，"
                   "虫口病原少了，打药次数自然少。\n"
                   "## 已经产生抗性怎么办\n"
                   "停用当前药剂，换不同机制的新药剂，并配合天敌、诱杀等非化学手段；"
                   "拿不准时拍下药袋和症状找当地植保站咨询。\n"
                   "注意：具体药剂以登记标签为准。",
    },
    {
        "title": "叶部病斑三大类型对照：霉层、轮纹、角斑",
        "category": "general",
        "crop_names": [],
        "disease_name": None,
        "growth_stage": None,
        "tags": ["鉴别", "病斑", "霜霉病", "白粉病", "细菌性"],
        "source": "公开农业植保知识整理（待补充文献出处）",
        "content": "## 为什么要先看病斑类型\n"
                   "叶部病害几十种，但农民能自己判断的第一步只有三问："
                   "有没有霉层？有没有轮纹？病斑受不受叶脉限制？"
                   "这三问能直接分出真菌卵菌、真菌子囊菌和细菌三大类，选药方向就对了。\n"
                   "## 第一型：叶背有霉层\n"
                   "代表是霜霉病类和叶霉病类。特征：病斑正面黄色、背面长灰黑或紫灰霉层，"
                   "病斑受叶脉限制呈多角形。属于卵菌/真菌，用烯酰吗啉、"
                   "霜脲氰类或防治叶霉病的药剂。\n"
                   "## 第二型：叶面直接铺白粉\n"
                   "代表是白粉病类。特征：白粉长在叶面正面、像撒面粉、能擦掉，"
                   "叶背没有霉层。用苯醚甲环唑、三唑酮类。注意辣椒白粉病反常："
                   "粉在叶背，叶面只见黄斑。\n"
                   "## 第三型：水浸状角斑、穿孔、菌脓\n"
                   "代表是细菌性角斑病等细菌病害。特征：病斑水浸状、对光半透明、"
                   "后期穿孔，湿度大时叶背有白色或蜡黄色黏液（菌脓），"
                   "干后成白膜。必须用铜制剂等细菌药剂，真菌药剂打细菌病无效。\n"
                   "## 拿不准时的办法\n"
                   "拍三张照片：叶正面、叶背面、整株，"
                   "送智能诊断或找植保员复核，再决定用药方向。\n"
                   "注意：具体药剂以登记标签为准。",
    },
    {
        "title": "蛀果类害虫的共同规律与防治时机",
        "category": "general",
        "crop_names": ["番茄", "辣椒", "苹果", "桃"],
        "disease_name": None,
        "growth_stage": None,
        "tags": ["鉴别", "棉铃虫", "烟青虫", "桃小食心虫"],
        "source": "公开农业植保知识整理（待补充文献出处）",
        "content": "## 为什么蛀果虫要「抢在蛀果前」\n"
                   "棉铃虫、烟青虫、桃小食心虫的共同点是：卵产在果面或花蕾上，"
                   "幼虫孵化后先啃食果皮几小时，随后钻进果实内部 —— "
                   "钻进去之后，任何药剂都打不到虫，打药只能杀还没进果的卵和幼虫。\n"
                   "## 怎么抓住时机\n"
                   "看物候：番茄辣椒现蕾开花后、苹果谢花后 3~4 周，"
                   "开始每周查果；看标志：果面出现小蛀孔、"
                   "孔口带虫粪或白色胶珠，说明已有虫蛀入，"
                   "此时重点是「保住没被蛀的果」而不是救被蛀的果。\n"
                   "## 三个省药的招\n"
                   "一用性诱剂：挂诱芯监测成虫高峰，高峰后一周内是喷药窗口；\n"
                   "二用生物农药：苏云金杆菌、甲维盐对低龄幼虫效果好、对天敌友好；\n"
                   "三清园：捡净落果烂果、冬季刮翘皮，来年虫口基数能降一半。\n"
                   "## 果面胶珠和粪粒的辨认\n"
                   "苹果桃小食心虫蛀孔流出白色泪珠状果胶、干成蜡膜；"
                   "番茄棉铃虫蛀孔带黑色颗粒虫粪。看到这两个标志，"
                   "虫已入果，马上转入选果保护策略。\n"
                   "注意：具体药剂以登记标签为准。",
    },
]

# ---------------------------------------------------------------------------
# 原始薄文档的增厚版（第 4 批续）
#
# runner 的升级规则：库中已有同名文档、且这里的内容比库里的明显更长（>20%），
# 就更新并重建该文档的向量索引。原始 30 条是短段落写法，扩充后被更详尽的
# 新条目挤出 top3（评测 19/22/34 暴露）—— 增厚而非另立新条目，
# 因为标题不变才不会在库里产生「两个讲同一件事的文档」。
# ---------------------------------------------------------------------------
UPGRADED_DOCS: dict[str, str] = {
    "小麦蚜虫": (
        "## 识别要点\n"
        "麦蚜聚集在麦穗、穗下茎和上部嫩叶背面，绿色或黄绿色成团；"
        "穗期危害最重：吸食灌浆汁液导致千粒重下降，"
        "麦穗上虫子爬成一层，周围黏糊糊发亮（蜜露），后期招来黑色煤污。\n"
        "## 与麦蜘蛛的区分\n"
        "麦蜘蛛红褐色、在叶背活动、受害叶面是密集黄白小点、没有蜜露；"
        "蚜虫体软发黏、聚在穗部嫩茎。抖动麦株：蚜虫黏着不落，"
        "蜘蛛纷纷坠地逃走。\n"
        "## 发生条件\n"
        "气温 15~25℃、天气干旱少雨时繁殖最快，春季两三周就能翻几番；"
        "穗期阴雨和大风对它有抑制作用。\n"
        "## 防治要点\n"
        "抽穗扬花后每周查穗：百穗蚜量 500 头以上或蜜露明显时用药，"
        "用登记的吡虫啉、噻虫嗪类药剂对准穗部；"
        "穗期也是瓢虫、食蚜蝇、蚜茧蜂等天敌活跃期，"
        "天敌数量大时优先保护、只挑治重虫田。\n"
        "注意：具体药剂以登记标签和当地植保部门指导意见为准。"),
    "玉米螟": (
        "## 识别要点\n"
        "心叶期看「花叶」：新展开的叶片上出现整齐的横排小孔，"
        "这是幼虫在卷着的心叶里啃食留下的招牌特征；"
        "抽雄后看茎秆：有带虫粪的蛀孔，孔口挂黑色颗粒；"
        "穗柄被蛀后果穗下垂、茎秆遇风折断。\n"
        "## 与草地贪夜蛾、黏虫的区分\n"
        "草地贪夜蛾孔洞大小不一、心叶里虫粪大量堆积、幼虫头壳有白色 Y 形纹；"
        "黏虫主要啃食叶片成缺刻、头壳是「八」字纹；"
        "玉米螟的「整齐横排小孔」最有辨识度。\n"
        "## 发生条件\n"
        "气温 20~30℃、相对湿度 60% 以上利于发生；"
        "越冬秸秆处理不彻底的田块虫源最大；一年 2~4 代，越往南代数越多。\n"
        "## 防治要点\n"
        "心叶末期（大喇叭口期）是防治关键窗口：颗粒剂丢心或用登记的"
        "氯虫苯甲酰胺类药剂喷雾；成虫期用性诱剂和杀虫灯诱杀；"
        "冬季粉碎秸秆需配合深耕或离田处理，直接堆放等于给幼虫盖被子。\n"
        "注意：具体药剂以登记标签和当地植保部门指导意见为准。"),
    "农药安全间隔期与采收前用药": (
        "## 什么是安全间隔期\n"
        "最后一次施药到采收之间必须间隔的最少天数，"
        "让农药残留降解到安全标准以下。不同药剂、不同作物的间隔期不同，"
        "必须看所用产品标签，不能凭感觉估。\n"
        "## 最容易踩的三个坑\n"
        "一、临近采收还在打药 —— 残留超标，检出后可能整批退货；\n"
        "二、把 A 药的间隔期当成 B 药的 —— 同是杀菌剂，间隔期可能差十几天；\n"
        "三、加大剂量却不延长间隔期 —— 加量后残留降解更慢，实际需要隔更久。\n"
        "## 怎么管理才不出事\n"
        "打药当时就记四件事：日期、地块、药剂名、稀释倍数；"
        "采收前按标签间隔期从采收日**倒推**最后打药日，倒推不过来宁可晚采；"
        "多茬轮作的地块按最短茬口作物的标准执行更稳妥。\n"
        "## 临近采收发现问题必须补药时\n"
        "优先选低毒、短间隔期的登记药剂，并和收购方确认；"
        "拿不准就咨询当地植保站，不要照抄网上的「通用天数」。\n"
        "注意：具体天数以所用产品登记标签为准。"),
}
