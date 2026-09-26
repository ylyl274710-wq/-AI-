"""v1 路由汇总。

挂载顺序不影响匹配（每个子路由都有独立 prefix），但按「无前缀 → 有前缀」
排列便于阅读。
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (auth, chat, diagnosis, farm, health, knowledge,
                        pesticide, task, weather)

api_router = APIRouter()

api_router.include_router(health.router)      # /health /ready
api_router.include_router(auth.router)        # /auth/*
api_router.include_router(farm.router)        # /farm/*
api_router.include_router(diagnosis.router)   # /diagnosis/*
api_router.include_router(chat.router)        # /chat/*
api_router.include_router(knowledge.router)   # /knowledge/*
api_router.include_router(pesticide.router)   # /pesticide/*
api_router.include_router(task.router)        # /tasks/*
api_router.include_router(weather.router)     # /weather/*

__all__ = ["api_router"]
