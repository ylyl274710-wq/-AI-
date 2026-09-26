#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检索评测集：农户口语提问 → 期望命中的病害/知识条目。

★ 为什么必须有它：知识库每次扩充（加文档、改切块、调 RRF 权重）都可能
  让某类提问的召回变差，而「变差」不报错 —— 只有对着固定的评测集跑一遍
  才看得见。这里是整个 RAG 链路的「回归测试基准」。

评测设计：
  - 查询全部是**口语化症状描述**（不出现专业病名），这是真实农户的输入形态；
    另留少量直接点名病名的「管线题」验证基本链路没断。
  - 判定标准：top-k 里出现期望的 disease_name，或期望的文档标题。
  - 改写用 local_rewrite（确定性、零模型调用），测的是**检索本身的地板**，
    不混入 LLM 改写的波动。LLM 改写只会更好，不会更差。
  - 不带 crop 过滤 —— 模拟首页/闲聊场景的最难情况。

用法：
    python backend/scripts/check_retrieval.py            # 全量跑，报命中率
    python backend/scripts/check_retrieval.py -v         # 逐条显示命中详情
退出码：hit@3 < 0.60 视为回归（返回 1），否则 0。

已知硬题（2026-09-26，语料 112 篇）：
  - 题 22「玉米心叶排孔」：本地改写补不出「钻心虫」这类专业词，
    玉米螟的薄文档竞争不过增厚的草地贪夜蛾条目。生产路径用 LLM 改写
    会补专业词，预期优于本地地板；后续靠继续增厚玉米螟条目解决。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal  # noqa: E402
from app.models.farm import Crop  # noqa: E402
from app.services.rag.retriever import HybridRetriever  # noqa: E402
from app.services.rag.rewrite import local_rewrite  # noqa: E402

# (口语查询, 期望 disease_name, 期望标题关键词) —— 后两者二选一即可，都填按「或」判定
EVAL_CASES: list[tuple[str, str | None, str | None]] = [
    # ---- 番茄 ----
    # ★ 出题标准（修题 1/19/27 后沉淀）：症状描述必须选「这个病独有」的特征，
    #   不能写多个近缘病都说得通的泛化描述 —— 否则测的不是检索，是题目歧义。
    ("番茄叶片出现开水烫过的暗绿色大斑，蔓延特别快，叶背有稀疏白霉", "番茄晚疫病", None),
    ("西红柿叶子上褐色的斑一圈一圈像靶子，慢慢扩大", "番茄早疫病", None),
    ("番茄刚开完的花烂了，上面长了层灰色的毛", "番茄灰霉病", None),
    ("番茄新叶皱缩发卷，植株矮矮的，叶子黄绿相间", "番茄黄化曲叶病毒病", None),
    ("番茄叶背面有褐色霉层像发霉了，从下部老叶开始", "番茄叶霉病", None),
    ("西红柿叶子上全是小白蛾子，一碰就呼啦啦飞起来", "番茄白粉虱", None),
    ("番茄晚疫病用什么药治", "番茄晚疫病", None),
    # ---- 黄瓜 ----
    ("黄瓜叶子上有多角形的黄斑，早上叶背面还有一层灰黑霉", "黄瓜霜霉病", None),
    ("黄瓜叶子上像撒了一层面粉一样的白毛，能擦掉", "黄瓜白粉病", None),
    ("黄瓜中午蔫早晚又缓过来，过几天整棵枯死，茎基部变褐色", "黄瓜枯萎病", None),
    ("黄瓜叶上有黄褐色小斑点，后来干破了一个孔", "黄瓜靶斑病", None),
    ("黄瓜叶背面爬满绿色小虫，叶子卷了还发黏发亮", "黄瓜蚜虫", None),
    # ---- 辣椒 ----
    ("辣椒果子上凹陷的褐色病斑，上面还有一圈同心排列的小黑点", "辣椒炭疽病", None),
    ("辣椒屁股那头发黑腐烂，果子还没红就掉了", "辣椒脐腐病", None),
    ("辣椒茎基部发黑水烂，整棵蔫了，下雨后更严重", "辣椒疫病", None),
    ("辣椒嫩叶皱皱巴巴，花里有很多很小会跳的虫", "辣椒蓟马", None),
    # ---- 小麦 ----
    ("小麦穗上有粉红色的霉，整穗变白不灌浆", "小麦赤霉病", None),
    ("麦叶上有黄色条纹状的锈斑，手一抹蹭一手黄粉", "小麦条锈病", None),
    ("麦穗上和嫩茎爬满绿色小虫，叶子黏糊糊的发亮", "小麦蚜虫", "麦蚜"),
    # ---- 玉米 ----
    ("玉米叶上大块灰绿色长条斑，后期连成一片枯掉", "玉米大斑病", None),
    ("玉米叶子被咬出大小不一的孔，心叶里有虫粪，虫子晚上出来吃", "玉米草地贪夜蛾", None),
    ("玉米心叶被蛀成一排排整齐的小孔，掰开里面有虫", "玉米螟", "玉米螟和草地贪夜蛾"),
    # ---- 水稻 ----
    ("稻叶上有梭子形的褐斑，中间灰白边缘褐色", "水稻稻瘟病", None),
    ("水稻下部叶鞘上有云纹一样的斑，一圈一圈像云朵", "水稻纹枯病", None),
    ("稻子基部密密麻麻小虫，稻穗发黑还长了煤球一样的霉", "水稻稻飞虱", None),
    # ---- 马铃薯 ----
    ("土豆叶片像开水烫过一样，叶背有白霉，几天蔓延全田", "马铃薯晚疫病", None),
    ("土豆叶子上褐色斑点带一圈圈轮纹，从下部老叶先发", "马铃薯早疫病", None),
    # ---- 苹果 ----
    ("苹果树干上长疙瘩瘤子，树皮粗糙开裂还流水", "苹果轮纹病", None),
    ("苹果叶上褐色斑块边缘不整齐，后来整叶变黄早落", "苹果褐斑病", None),
    ("苹果树皮腐烂发软有酒糟味，还往外渗红褐色汁", "苹果腐烂病", None),
    ("苹果叶背密密麻麻红色小虫，还拉丝网，叶面有黄白小点", "苹果红蜘蛛", None),
    # ---- 通用 / 安全 ----
    ("叶子发黄到底是缺肥还是有病，新叶老叶黄得不一样", None, "叶片发黄"),
    ("拍照给专家看，照片应该怎么拍才清楚", None, "拍摄"),
    ("打了药之后多少天才能摘果子卖", None, "安全间隔期"),
    ("同一个药老打，效果越来越不如以前了咋回事", None, "抗药"),
    ("大棚里湿度大，病害来得猛，怎么控湿", None, "湿度"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        crop_rows = list(db.scalars(select(Crop)).all())
        crops = [c.name for c in crop_rows]
        aliases = {a: c.name for c in crop_rows
                   for a in (c.aliases or []) if isinstance(c.aliases, list)}
        retriever = HybridRetriever(db)
        hits1 = hits3 = 0
        fails: list[str] = []
        for i, (q, want_disease, want_title) in enumerate(EVAL_CASES, 1):
            rq = local_rewrite(q, known_crops=crops, crop_aliases=aliases)
            result = retriever.retrieve(rq, top_k=3)
            got = [(c.disease_name, c.title) for c in result.chunks]

            def hit(chunk) -> bool:  # noqa: ANN001
                if want_disease and (chunk.disease_name == want_disease
                                     or (chunk.title or "") == want_disease):
                    return True
                if want_title and want_title in (chunk.title or ""):
                    return True
                return False

            top = result.chunks
            h1 = bool(top) and hit(top[0])
            h3 = any(hit(c) for c in top)
            hits1 += h1
            hits3 += h3
            if not h3:
                fails.append(f"{i}. {q}\n      期望: {want_disease or want_title}  实际top3: {got}")
            if args.verbose:
                mark = "✓" if h3 else ("△" if h1 else "✗")
                detail = got[0] if got else ("（无结果）", "")
                print(f"  {mark} [{i:2d}] {q[:24]:<26s} top1={detail[1] or detail[0]}")
        n = len(EVAL_CASES)
        print(f"\n命中统计：hit@1 = {hits1}/{n}（{hits1 / n:.0%}）  "
              f"hit@3 = {hits3}/{n}（{hits3 / n:.0%}）")
        if fails:
            print(f"\n未命中 {len(fails)} 条：")
            for f in fails:
                print(f"  {f}")
        floor = 0.60
        if hits3 / n < floor:
            print(f"\n[回归] hit@3 低于 {floor:.0%} 下限")
            return 1
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
