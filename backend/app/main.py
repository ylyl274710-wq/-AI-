"""FastAPI 应用入口。

启动方式（开发）：
    cd backend
    PYTHONUTF8=1 ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import PROJECT_ROOT, settings
from app.core.db import ping

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()

    # ★ 数据库不通时只警告，不阻止启动。
    #   理由：这个服务的主要价值在「诊断」，而诊断依赖知识库；
    #   知识库不通时接口自然会报错，报错信息比「进程起不来」更容易定位。
    #   反过来，如果直接退出，容器会被反复重启，日志里只剩一堆启动失败，
    #   反而看不到真正的连接错误。
    ok, msg = ping()
    if ok:
        logger.info("数据库连接正常")
    else:
        logger.warning("数据库不可用：%s（服务仍会启动，接口会返回错误）", msg)

    if not settings.llm_enabled:
        logger.warning("未配置 LLM API Key，将走模板降级模式（答案质量下降）")
    if not settings.vision_enabled:
        logger.warning("未配置视觉模型，图片诊断将只记录不识别")

    yield
    logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=("农业病虫害智能诊断与农事决策 Agent。"
                 "视觉模型负责看，RAG 负责查，规则引擎负责管，"
                 "LLM 负责理解和表达，Agent 负责串联任务。"),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)

# 本地存储模式下，图片直接由后端提供；用 MinIO 时这个目录是空的，
# 挂载不会报错但也不会有请求进来。
_media_dir = settings.storage_dir / "media"
if _media_dir.is_dir():
    app.mount("/media", StaticFiles(directory=str(_media_dir)), name="media")

# 前端由后端一起托管，这样农户只需要记一个地址，也不用额外起静态服务器。
# 同时也支持直接用 file:// 打开 index.html（靠 CORS 放行 null origin）。
_frontend_dir = PROJECT_ROOT / "frontend"
if _frontend_dir.is_dir():
    # ★ 前端资源必须 no-cache：零依赖前端的 JS 互相有版本耦合
    #   （core.js 的 store 方法被页面 js 调用），浏览器启发式缓存会让
    #   「新版页面 + 旧版 core.js」混合运行，报 setDiagSession is not a
    #   function 这类错（2026-09-27 实测）。JS/CSS/HTML 一共 10 个文件，
    #   每次 revalidate 的成本可以忽略，换来的永远是最新代码。
    class _NoCacheStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            resp = await super().get_response(path, scope)
            if getattr(resp, "media_type", "") in (
                    "text/html", "text/css",
                    "application/javascript", "text/javascript"):
                resp.headers["Cache-Control"] = "no-cache, must-revalidate"
            return resp

    app.mount("/app", _NoCacheStaticFiles(directory=str(_frontend_dir), html=True),
              name="frontend")


# --------------------------------------------------------------------------- #
# 统一异常响应
# --------------------------------------------------------------------------- #


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request,
                                      exc: Exception) -> JSONResponse:
    """兜底异常处理。

    ★ 对外只说「服务器内部错误」，细节只进日志。
      这类系统会在异常信息里带上 SQL、文件路径甚至配置片段，
      直接回给前端等于把内部结构暴露出去（难点 14）。
      但 request_id 要回，便于用户报错时对上日志。
    """
    import uuid

    rid = uuid.uuid4().hex[:12]
    logger.exception("未处理异常 [%s] %s %s", rid, request.method,
                     request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试",
                 "request_id": rid})


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "app": settings.app_name,
        "web": "/app/",
        "docs": "/docs",
        "api": settings.api_prefix,
        "health": f"{settings.api_prefix}/health",
    }
