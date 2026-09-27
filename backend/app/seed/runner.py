"""种子数据导入。

设计成幂等的：重复执行不会产生重复数据，方便反复重建环境。
用法：
    python -m app.seed.runner            # 导入全部（含向量化）
    python -m app.seed.runner --no-index # 只导数据，不建向量索引
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.farm import Crop, CropGrowthRecord, Farm, Field
from app.models.knowledge import (AgriculturalKnowledge, DiseaseRecord,
                                  KnowledgeSource)
from app.models.misc import FarmTask
from app.models.pesticide import (PesticideActiveIngredient, PesticideProduct,
                                  PesticideUsage)
from app.models.user import ROLE_FARMER, User
from app.seed.knowledge_data import CROPS, DISEASES, GENERAL_KNOWLEDGE, SOURCES
from app.seed.knowledge_data_ext import (DISEASES_EXT, GENERAL_KNOWLEDGE_EXT,
                                          UPGRADED_DOCS)

# 原始条目 + 扩充条目（knowledge_data_ext.py，构建器生成、conditions 单一事实来源）
ALL_DISEASES = DISEASES + DISEASES_EXT
from app.seed.pesticide_data import (INGREDIENTS, PRODUCTS,
                                        REGISTRATION_UPDATES)

logging.basicConfig(level=logging.INFO,
                    format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("seed")

DEMO_USER = {"username": "demo", "password": "demo1234",
             "display_name": "演示农户", "phone": "13800000000", "city": "郑州"}


def seed_sources(db: Session) -> dict[str, KnowledgeSource]:
    out: dict[str, KnowledgeSource] = {}
    for item in SOURCES:
        obj = db.scalar(select(KnowledgeSource).where(
            KnowledgeSource.name == item["name"]))
        if obj is None:
            obj = KnowledgeSource(**item)
            db.add(obj)
            db.flush()
        out[item["name"]] = obj
    logger.info("知识来源: %d 条", len(out))
    return out


def seed_crops(db: Session) -> dict[str, Crop]:
    out: dict[str, Crop] = {}
    for item in CROPS:
        obj = db.scalar(select(Crop).where(Crop.name == item["name"]))
        if obj is None:
            obj = Crop(**item)
            db.add(obj)
            db.flush()
        out[item["name"]] = obj
    logger.info("作物: %d 种", len(out))
    return out


# 数据文件里的病害条目同时服务于两张表：
#   - disease_records          : 结构化字段（conditions / stage_susceptibility ...）
#   - agricultural_knowledge   : 正文 content
# 两边字段并不重合，所以往 DiseaseRecord 传参前必须按模型实际列过滤。
# 这里用模型列做白名单 + 反向检查，避免出现「数据文件加了字段、runner 却
# 静默忽略」或「runner 直接把未知字段传进构造函数报 TypeError」两种情况。
_DISEASE_COLUMNS = {c.name for c in DiseaseRecord.__table__.columns}


def seed_diseases(db: Session, sources: dict[str, KnowledgeSource],
                  ) -> int:
    n = 0
    for item in ALL_DISEASES:
        data = dict(item)
        src_name = data.pop("source", None)
        data.pop("content", None)   # 正文只进 agricultural_knowledge
        unknown = set(data) - _DISEASE_COLUMNS
        if unknown:
            raise ValueError(
                f"DISEASES 条目「{item['standard_name']}」含 DiseaseRecord "
                f"未知字段 {sorted(unknown)}；请同步更新模型或 seed 数据")
        obj = db.scalar(select(DiseaseRecord).where(
            DiseaseRecord.standard_name == data["standard_name"]))
        if obj is not None:
            continue
        src = sources.get(src_name)
        obj = DiseaseRecord(**data, source_id=src.id if src else None,
                            version=1, status="active")
        db.add(obj)
        n += 1
    db.flush()
    logger.info("病害/虫害条目: 新增 %d 条", n)
    return n


def seed_knowledge(db: Session, sources: dict[str, KnowledgeSource]) -> int:
    """把病害条目和通用条目都写成知识文档。"""
    n = 0
    upgrades = 0
    docs: list[dict] = []
    for item in ALL_DISEASES:
        docs.append({
            "title": item["standard_name"],
            "category": item["category"],
            "crop_names": item["crop_names"],
            "disease_name": item["standard_name"],
            "growth_stage": None,
            "content": item["content"],
            "tags": item.get("aliases", []) + [item["category"]],
            "source": item.get("source"),
        })
    docs.extend(GENERAL_KNOWLEDGE + GENERAL_KNOWLEDGE_EXT)

    for item in docs:
        # 第 4 批的增厚版内容（UPGRADED_DOCS）优先于原始 content
        content = UPGRADED_DOCS.get(item["title"], item["content"])
        obj = db.scalar(select(AgriculturalKnowledge).where(
            AgriculturalKnowledge.title == item["title"]))
        if obj is not None:
            # 升级机制：库里的内容明显短于最新版本时更新并重建索引
            if len(content) > len(obj.content or "") * 1.2:
                obj.content = content
                upgrades += 1
            continue
        src = sources.get(item.get("source"))
        obj = AgriculturalKnowledge(
            title=item["title"], category=item.get("category"),
            crop_names=item.get("crop_names") or [],
            disease_name=item.get("disease_name"),
            growth_stage=item.get("growth_stage"),
            content=item["content"], tags=item.get("tags") or [],
            source_id=src.id if src else None,
            version=1, status="active",
            # ★ 对应文档难点 4：人工审核通过后才进生产知识库
            #   这里的种子数据已整理但尚未补充文献出处，故标记为 approved
            #   仅供演示；正式上线前应改为 pending 并逐条复核。
            review_status="approved")
        db.add(obj)
        n += 1
    db.flush()
    logger.info("知识文档: 新增 %d 篇，增厚升级 %d 篇", n, upgrades)
    return n


def seed_pesticides(db: Session) -> tuple[int, int]:
    for item in INGREDIENTS:
        if db.scalar(select(PesticideActiveIngredient).where(
                PesticideActiveIngredient.standard_name == item["standard_name"])):
            continue
        db.add(PesticideActiveIngredient(**item))
    db.flush()

    n = 0
    upgraded = 0
    # PRODUCTS 是完整条目（新增用）；REGISTRATION_UPDATES 是部分条目
    # （只声明要补的维度），与已存在产品按集合并集——只增不减
    for item in PRODUCTS + REGISTRATION_UPDATES:
        obj = db.scalar(select(PesticideProduct).where(
                PesticideProduct.product_name == item["product_name"]))
        if obj is not None:
            # 登记信息升级：作物/防治对象/阶段三个列表取并集（只增不减——
            # 登记范围只会变宽，收窄必须走人工复核，不能靠种子数据静默覆盖）
            for field in ("registered_crops", "target_pests", "applicable_stages"):
                old_set = set(getattr(obj, field) or [])
                merged = sorted(old_set | set(item.get(field) or []))
                if set(merged) != old_set:   # ★ 按集合比较：顺序重排不算升级
                    setattr(obj, field, merged)
                    upgraded += 1
            continue
        db.add(PesticideProduct(**item, version=1, status="active"))
        n += 1
    db.flush()
    logger.info("有效成分: %d 条；农药产品: 新增 %d 条，登记信息升级 %d 处",
                len(INGREDIENTS), n, upgraded)
    return len(INGREDIENTS), n


def seed_demo_user(db: Session) -> User:
    from app.core.security import hash_password
    user = db.scalar(select(User).where(User.username == DEMO_USER["username"]))
    if user is None:
        user = User(username=DEMO_USER["username"],
                    display_name=DEMO_USER["display_name"],
                    phone=DEMO_USER["phone"], city=DEMO_USER["city"],
                    hashed_password=hash_password(DEMO_USER["password"]),
                    role=ROLE_FARMER, elder_mode=True)
        db.add(user)
        db.flush()
        logger.info("演示账号: %s / %s", DEMO_USER["username"], DEMO_USER["password"])
    return user


def seed_demo_farm(db: Session, user: User, crops: dict[str, Crop]) -> None:
    if db.scalar(select(Farm).where(Farm.owner_id == user.id)):
        return
    farm = Farm(owner_id=user.id, name="村东头大棚", province="河南省",
                city="郑州市", district="中牟县", lat=34.72, lng=113.98,
                total_area_mu=3.5, remark="两个日光温室，主要种番茄和黄瓜")
    db.add(farm)
    db.flush()

    f1 = Field(farm_id=farm.id, name="1号棚", area_mu=1.5, soil_type="壤土",
               irrigation_type="滴灌", lat=34.721, lng=113.981)
    f2 = Field(farm_id=farm.id, name="2号棚", area_mu=2.0, soil_type="壤土",
               irrigation_type="沟灌", lat=34.722, lng=113.982)
    db.add_all([f1, f2])
    db.flush()

    tomato = crops["番茄"]
    cucumber = crops["黄瓜"]
    db.add_all([
        CropGrowthRecord(field_id=f1.id, crop_id=tomato.id, variety="粉果",
                         stage="结果期",
                         sowing_date=date.today() - timedelta(days=95),
                         stage_start_date=date.today() - timedelta(days=25),
                         expected_harvest_date=date.today() + timedelta(days=20),
                         is_current=True),
        CropGrowthRecord(field_id=f2.id, crop_id=cucumber.id, variety="津优",
                         stage="开花结果期",
                         sowing_date=date.today() - timedelta(days=55),
                         stage_start_date=date.today() - timedelta(days=10),
                         expected_harvest_date=date.today() + timedelta(days=35),
                         is_current=True),
    ])
    db.flush()

    # 两条历史用药记录：一条与白名单产品关联，用于演示抗性提醒
    prod = db.scalar(select(PesticideProduct).where(
        PesticideProduct.product_name == "吡唑醚菌酯乳油"))
    db.add_all([
        PesticideUsage(user_id=user.id, field_id=f1.id, crop_id=tomato.id,
                       product_id=prod.id if prod else None,
                       product_name_raw="吡唑醚菌酯乳油",
                       active_ingredient_name="吡唑醚菌酯",
                       resistance_group="FRAC 11",
                       used_at=date.today() - timedelta(days=9),
                       record_source="manual", confirmed=True,
                       note="防治早疫病"),
        PesticideUsage(user_id=user.id, field_id=f1.id, crop_id=tomato.id,
                       product_id=prod.id if prod else None,
                       product_name_raw="吡唑醚菌酯乳油",
                       active_ingredient_name="吡唑醚菌酯",
                       resistance_group="FRAC 11",
                       used_at=date.today() - timedelta(days=3),
                       record_source="manual", confirmed=True,
                       note="复配使用"),
    ])
    db.flush()
    logger.info("演示农田/地块/生长记录/历史用药 已创建")


def seed_demo_tasks(db: Session, user: User) -> None:
    if db.scalar(select(FarmTask).where(FarmTask.user_id == user.id)):
        return
    field = db.scalar(select(Field).join(Farm, Field.farm_id == Farm.id)
                      .where(Farm.owner_id == user.id))
    db.add_all([
        FarmTask(user_id=user.id, field_id=field.id if field else None,
                 title="下地巡田，重点看叶片背面",
                 task_type="patrol", due_date=date.today() + timedelta(days=1),
                 detail="近期湿度偏高，注意检查叶背是否有霉层",
                 priority=1, created_by_node="seed"),
        FarmTask(user_id=user.id, field_id=field.id if field else None,
                 title="追肥：结果期钾肥",
                 task_type="fertilize", due_date=date.today() + timedelta(days=4),
                 priority=2, created_by_node="seed"),
    ])
    db.flush()


def run(index: bool = True, demo: bool = True) -> dict:
    settings.ensure_dirs()
    db = SessionLocal()
    stats: dict = {}
    try:
        sources = seed_sources(db)
        crops = seed_crops(db)
        stats["diseases"] = seed_diseases(db, sources)
        stats["knowledge"] = seed_knowledge(db, sources)
        stats["ingredients"], stats["products"] = seed_pesticides(db)
        if demo:
            user = seed_demo_user(db)
            seed_demo_farm(db, user, crops)
            seed_demo_tasks(db, user)
        db.commit()

        if index:
            from app.services.rag.indexer import KnowledgeIndexer
            logger.info("开始向量化（首次运行需要下载 Embedding 模型，请耐心等待）")
            stats["chunks"] = KnowledgeIndexer(db).reindex_all()
        else:
            stats["chunks"] = 0
    finally:
        db.close()
    logger.info("种子数据导入完成: %s", stats)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-index", action="store_true", help="跳过向量化")
    ap.add_argument("--no-demo", action="store_true", help="不创建演示数据")
    a = ap.parse_args()
    try:
        run(index=not a.no_index, demo=not a.no_demo)
    except Exception:
        logger.exception("种子数据导入失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
