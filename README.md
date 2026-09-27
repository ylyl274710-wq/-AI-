# 农业病虫害智能诊断与农事决策 Agent

一个面向农户的病虫害智能诊断系统：拍照/描述症状 → 多模态识别 + RAG 检索 → 置信度闸门 → 规则引擎校验 → 流式回答。无 Key 也能完整跑通（全链路降级设计），配 Key 后 LLM / 视觉 / 天气自动切换真实服务。

[![CI](https://github.com/USERNAME/REPO/actions/workflows/ci.yml/badge.svg)](https://github.com/USERNAME/REPO/actions/workflows/ci.yml) <!-- 推送到 GitHub 后把 USERNAME/REPO 换成实际仓库 -->

## 量化指标（评测驱动迭代，非自评）

| 验证项 | 结果 | 说明 |
|---|---|---|
| HTTP 端到端冒烟 | **82/82** | 注册登录、诊断链路、权限隔离、SSE、图片链路、对话落库 |
| 检索评测集（36 条口语提问） | hit@1 **69%** / hit@3 **97%** | 基线 67%/81% → 别名映射 + 标题命中 + 语料扩充逐步量化提升 |
| 知识库规模 | 101 病虫害条目 / 112 篇文档 / 119 chunk | 结构化发生条件（conditions）为风险计算与正文渲染的唯一事实来源 |
| pytest 单元 + e2e | **34 项** | 切块不变量 / 改写映射 / 风险打分纯函数 + 5 条全链路脚本 |
| 前端真实浏览器 e2e | 30 项 | Playwright + 系统 Chrome，真实填表/点击/截图 |
| 各专项自检 | 切块 16 / 视觉接线 8 / 分类器接线 10 / 工具循环 6 | `backend/scripts/check_*.py`，均可独立运行 |
| 对话能力 | 多轮记忆 + token 级流式 + LLM 自主调工具 | consult 路径 Function calling 循环，实测单次 278~519 个 delta |

## 架构

```
┌──────────────┐   SSE    ┌─────────────────────────────────────────────┐
│ 前端（零依赖  │◄────────►│ FastAPI  /api/v1                            │
│ 8 个 js 文件）│          │                                             │
└──────────────┘          │  Agent FSM（自研 9 节点状态机）               │
                          │  intent→context→diagnose→retrieve→          │
┌──────────────┐          │  rule_check→compose→persist→remind          │
│ PostgreSQL   │          │     │            │                          │
│ + pgvector   │◄────────►│     │  闸门A     │  闸门B                    │
│ 19 张业务表   │  RRF 混合 │     │  置信度不足 │  规则不通过不出药          │
└──────────────┘  检索     │     ▼            ▼                          │
┌──────────────┐          │  VLM(Qwen3-VL) + 本地分类器(ONNX, 可选)       │
│ Redis / MinIO│          │  LLM(硅基流动) / 和风天气 / 本地降级 mock      │
└──────────────┘          └─────────────────────────────────────────────┘
```

## 快速开始

```bash
# 1) 环境（Python 3.12）
python -m venv .venv && .venv/Scripts/pip install -r backend/requirements.txt

# 2) 下载本地 Embedding 模型（约 92MB，无 Key 也能跑的前提）
.venv/Scripts/python.exe scripts/fetch_embedding_model.py

# 3) 初始化便携基础设施 + 种子数据（幂等，可重复执行）
.venv/Scripts/python.exe backend/app/seed/runner.py

# 4) 一键启动（PG / Redis / MinIO / 后端四合一，含看门狗与单实例锁）
.venv/Scripts/python.exe scripts/infra.py serve

# 打开：http://127.0.0.1:8001/app/   （演示账号 demo / demo1234）
```

在 `backend/.env` 填入 LLM Key（硅基流动）与和风天气 Key 后重启，诊断、视觉、天气自动切换真实服务；不填则全链路使用本地降级，**降级状态在 `/health` 与界面上可见**。

## 核心设计决策（为什么这么做）

1. **两种 Agent 形态并存**——诊断是确定的 9 节点 FSM（可观测性优先，节点级工具白名单，越权留痕）；
   咨询是 Function calling 工具循环（LLM 自主决定调天气/知识库/地块工具，只收集证据，回答仍走统一出口纪律）。
   确定性的流程用管线，开放的问题用工具循环——按问题性质选形态，而不是全盘倒向某一种。
2. **置信度是算出来的，不是模型自报的**——`margin × agreement × coverage` 三个分量都可复算、可进评测；闸门 A 拦截时同样落库，保证「有多少问题没答上来」可统计。
3. **降级必须可见**——天气/存储/LLM/分类器四条依赖各有降级路径，降级状态通过 `/health` 的 `storage`、天气的 `is_mock+caveat`、分类器的 `available` 显式暴露给前端，禁止「服务活着但功能全废却报 up」。
4. **评测驱动**——检索质量由 36 条口语提问的评测集守护（`check_retrieval.py`），切块器有 16 条不变量（`check_chunker.py`），改任何一处先看评测再合入。
5. **结构化 conditions 是唯一事实来源**——病害的温湿度/降雨阈值只写一份，风险打分与给农户看的正文都从它渲染，杜绝两处手写漂移。

## 测试

```bash
.venv/Scripts/python.exe -m pytest -m "not e2e"   # CI 同款：纯单元，无需基础设施
.venv/Scripts/python.exe -m pytest                # 本地全量（含 5 条 e2e 脚本，需 PG/后端在跑）
node --check frontend/js/*.js                     # 前端语法
cd frontend && node tools/selftest-js-lex.mjs     # 词法器自测 18 项
```

## 目录结构

```
backend/
  app/
    agent/        # FSM、节点工具、提示词
    api/v1/       # 35 条路由（auth/chat/diagnosis/farm/knowledge/pesticide/task/weather/health）
    core/         # 配置、DB、安全（bcrypt+PyJWT）
    models/       # 19 张表（SQLAlchemy 2.0 + 软删除 mixin）
    seed/         # 幂等种子数据（含文档升级机制）
    services/     # RAG（切块/改写/检索/嵌入）、天气、规则引擎、置信度、分类器、存储
  scripts/        # 冒烟 82 项、检索评测、接线检查（pytest e2e 层复用同一批脚本）
  alembic/        # 3 个迁移版本
frontend/         # 零依赖静态前端 + 纯 Node 静态检查工具
scripts/          # infra.py 基础设施管理器、PV 分类器训练脚本
docs/             # 问题与解决总结（提炼版）+ 实施问题清单（D1~D34 全记录）
                  # 知识库维护指南（如何添加/更新知识条目）
models/           # PV 分类器标签映射（pv_labels.json）
```

## 已知限制与路线图

诚实清单（详见 `docs/实施问题清单.md` 的 60+ 项记录）：

- [ ] PV 分类器模型待训练：`scripts/train_pv_classifier.py` 已备好，需在 Colab/GPU 上跑（数据集 PlantVillage 增强版 38 类）
- [ ] 诊断准确率无 ground truth 基准：已有检索 hit rate，尚缺「症状描述→期望病害」的端到端标注评测集
- [ ] Celery 异步任务：配置位已留，提醒推送未接
- [ ] 关键词检索为 Python 侧 BM25：千级 chunk 内可用，更大规模需迁移 PG 全文索引
- [ ] 生产部署：未容器化；JWT_SECRET 等需按 `.env.example` 换生产值
