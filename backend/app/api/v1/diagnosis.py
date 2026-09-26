"""病虫害诊断与对话（文档第三章主链路）。

两个入口：
  POST /diagnosis         一次性返回
  POST /diagnosis/stream  SSE 流式，边跑边把节点进度推给前端

★ 为什么必须有流式入口：一次诊断要跑 3~10 秒（改写 + 检索 + 规则校验 + 生成），
  中间还要处理上传的图片。没有进度反馈时农户会以为页面卡死然后刷新，
  而刷新等于重复提交，多烧一次模型调用。SSE 把每个节点的完成事件推出去，
  前端就能显示「正在查知识库…」「正在核对农药登记…」。
"""
from __future__ import annotations

import json
import uuid

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                     UploadFile, status)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.fsm import AgriAgent
from app.agent.state import INTENT_LABELS, AgentContext
from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.diagnosis import DiagnosisImage, DiagnosisRecord
from app.models.user import User
from app.schemas import (Citation, ConfidenceOut, DiagnoseOut, TraceStepOut)

router = APIRouter(prefix="/diagnosis", tags=["诊断"])

# ★ 上限必须在入口处拦，不能等进了 Agent 再校验：图片会一路被 base64 编码
#   进视觉模型的请求体，4 张 8MB 的图就是 40MB+ 的请求，模型端直接超时或 413。
#   单张上限读配置（settings.max_upload_mb），避免这里和别处各写一个数字。
MAX_IMAGES = 4
MAX_IMAGE_BYTES = settings.max_upload_mb * 1024 * 1024


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _parse_shot_types(raw: str | None) -> list[str]:
    """shot_types 从表单来，可能是 JSON 数组字符串，也可能是逗号分隔。"""
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return [s.strip() for s in raw.split(",") if s.strip()]
    if isinstance(val, str):
        return [val]
    if isinstance(val, list):
        return [str(v) for v in val]
    return []


async def _read_images(files: list[UploadFile]) -> list[bytes]:
    if len(files) > MAX_IMAGES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"最多上传 {MAX_IMAGES} 张图片")
    out: list[bytes] = []
    for f in files:
        if not f.filename:
            continue
        raw = await f.read()
        if not raw:
            continue
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"图片 {f.filename} 超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB")
        out.append(raw)
    return out


def _to_out(ctx: AgentContext) -> DiagnoseOut:
    """把 Agent 上下文摊平成响应体。

    ★ 只暴露「能解释得清」的字段。confidence 的三个分量、rule 的逐产品
      通过/拦截原因、tool_calls 都原样给出去 —— 农户看不懂没关系，
      但出问题时要能顺着这些字段查回「哪一步查了什么、为什么被拦」，
      这是文档难点 6（Agent 失控）与难点 13（结果无法验证）的落地手段。
    """
    conf: ConfidenceOut | None = None
    c = ctx.confidence
    if c is not None:
        conf = ConfidenceOut(
            score=round(float(getattr(c, "score", 0.0) or 0.0), 4),
            margin=round(float(getattr(c, "margin", 0.0) or 0.0), 4),
            agreement=round(float(getattr(c, "agreement", 0.0) or 0.0), 4),
            coverage=round(float(getattr(c, "coverage", 0.0) or 0.0), 4),
            passed=bool(getattr(c, "passed", False)),
            rejected=bool(getattr(c, "rejected", False)),
            reasons=list(getattr(c, "reasons", []) or []),
            missing_shots=list(getattr(c, "missing_shots", []) or []),
            method=getattr(c, "method", None),
        )

    citations = [
        Citation(index=i + 1,
                 title=str(cit.get("title") or cit.get("disease_name") or ""),
                 source=cit.get("source_name") or cit.get("source"),
                 url=cit.get("source_url") or cit.get("url"))
        for i, cit in enumerate(ctx.citations or [])
    ]

    trace = [
        TraceStepOut(node=t.node, status=t.status, elapsed_ms=t.elapsed_ms,
                     detail=t.detail or None, tools=list(t.tools or []))
        for t in (ctx.trace or [])
    ]

    return DiagnoseOut(
        diagnosis_id=ctx.diagnosis_id,
        session_id=ctx.session_id,
        state=ctx.state.value,
        intent=ctx.intent,
        answer=ctx.answer or "",
        answer_sections=dict(ctx.answer_sections or {}),
        top1=ctx.user_context.get("top1"),
        confidence=conf,
        citations=citations,
        trace=trace,
        tool_calls=list(ctx.tool_calls or []),
        risk=list(ctx.risk or []),
        rule=(ctx.rule_result.to_dict() if ctx.rule_result is not None else None),
        tasks_created=list(ctx.tasks_created or []),
        degraded=bool(ctx.degraded),
        llm_model=ctx.llm_model,
        token_usage=dict(ctx.token_usage or {}) or None,
        errors=list(ctx.errors or []),
    )


def _build_ctx(*, user: User, query: str, crop_name: str | None,
               field_id: int | None, growth_stage: str | None,
               session_id: str | None, shot_types: list[str],
               images: list[bytes]) -> AgentContext:
    return AgentContext(
        user_id=user.id,
        session_id=session_id or uuid.uuid4().hex[:24],
        query=query,
        images=images,
        image_shot_types=shot_types,
        crop_name=crop_name,
        field_id=field_id,
        growth_stage=growth_stage,
    )


# --------------------------------------------------------------------------- #
# 一次性诊断
# --------------------------------------------------------------------------- #


@router.post("", response_model=DiagnoseOut, summary="诊断 / 咨询（一次性返回）")
async def diagnose(
    query: str = Form("", description="农户的原话"),
    crop_name: str | None = Form(None),
    field_id: int | None = Form(None),
    growth_stage: str | None = Form(None),
    session_id: str | None = Form(None),
    shot_types: str | None = Form(None, description="JSON 数组或逗号分隔"),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DiagnoseOut:
    images = await _read_images(files)
    if not images and not (query or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "请上传照片或描述一下症状")

    ctx = _build_ctx(user=user, query=query, crop_name=crop_name,
                     field_id=field_id, growth_stage=growth_stage,
                     session_id=session_id,
                     shot_types=_parse_shot_types(shot_types),
                     images=images)
    agent = AgriAgent(db)
    await agent.run(ctx)
    return _to_out(ctx)


# --------------------------------------------------------------------------- #
# 流式诊断
# --------------------------------------------------------------------------- #


def _sse(event: str, data: dict) -> str:
    """SSE 帧。

    ★ json.dumps 必须 ensure_ascii=False：默认会输出 \\uXXXX，
      中文在前端显示没问题，但日志与抓包时完全不可读，
      排查「用户到底问了什么」时会很痛苦。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream", summary="诊断 / 咨询（SSE 流式）")
async def diagnose_stream(
    query: str = Form(""),
    crop_name: str | None = Form(None),
    field_id: int | None = Form(None),
    growth_stage: str | None = Form(None),
    session_id: str | None = Form(None),
    shot_types: str | None = Form(None),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    images = await _read_images(files)
    if not images and not (query or "").strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "请上传照片或描述一下症状")

    ctx = _build_ctx(user=user, query=query, crop_name=crop_name,
                     field_id=field_id, growth_stage=growth_stage,
                     session_id=session_id,
                     shot_types=_parse_shot_types(shot_types),
                     images=images)

    async def gen():
        # ★ 队列不用 asyncio.Queue 而用 list：Agent 的 on_event 是同步回调，
        #   而同步回调里往 asyncio.Queue 塞东西必须走 call_soon_threadsafe，
        #   在本进程内没必要绕。列表 + 游标足够，且顺序天然正确。
        events: list[dict] = []

        def on_event(ev: dict) -> None:
            events.append(ev)

        agent = AgriAgent(db, on_event=on_event)

        yield _sse("start", {"session_id": ctx.session_id,
                             "images": len(images)})

        import asyncio
        task = asyncio.create_task(agent.run(ctx))

        # 边跑边把已经产生的事件推出去。Agent 节点内部是异步的，
        # 每 50ms 轮询一次事件列表即可 —— 比在每个节点里 await 队列简单，
        # 也不会因为某个节点卡住而丢事件。
        cursor = 0
        while not task.done():
            while cursor < len(events):
                yield _sse("progress", events[cursor])
                cursor += 1
            await asyncio.sleep(0.05)

        # 收尾：把剩余事件与终态一起送出去
        while cursor < len(events):
            yield _sse("progress", events[cursor])
            cursor += 1

        try:
            await task
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"message": f"诊断过程出错：{exc}"})
            return

        payload = _to_out(ctx).model_dump(mode="json")
        yield _sse("final", payload)
        yield _sse("done", {"state": ctx.state.value,
                            "intent": ctx.intent,
                            "intent_label": INTENT_LABELS.get(ctx.intent, "")})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关掉 nginx 之类的缓冲，否则进度会攒到最后一起吐
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# 历史
# --------------------------------------------------------------------------- #


@router.get("/history", summary="我的诊断历史")
def history(limit: int = Query(20, ge=1, le=100),
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> list[dict]:
    rows = db.scalars(
        select(DiagnosisRecord)
        .where(DiagnosisRecord.user_id == user.id,
               DiagnosisRecord.deleted_at.is_(None))
        .order_by(DiagnosisRecord.id.desc()).limit(limit)).all()
    return [{
        "id": r.id,
        "status": r.status,
        "intent": (r.result or {}).get("intent"),
        "top1": r.top1_disease_name,
        "confidence": r.confidence,
        "input_text": r.input_text,
        "answer_text": r.answer_text,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.get("/{diagnosis_id}", summary="单条诊断详情")
def detail(diagnosis_id: int, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)) -> dict:
    rec = db.scalar(select(DiagnosisRecord).where(
        DiagnosisRecord.id == diagnosis_id,
        DiagnosisRecord.user_id == user.id,
        DiagnosisRecord.deleted_at.is_(None)))
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "诊断记录不存在")

    imgs = db.scalars(select(DiagnosisImage)
                      .where(DiagnosisImage.diagnosis_id == rec.id)).all()
    return {
        "id": rec.id,
        "status": rec.status,
        "input_source": rec.input_source,
        "input_text": rec.input_text,
        "confidence": rec.confidence,
        "confidence_detail": rec.confidence_detail,
        "result": rec.result,
        "answer_text": rec.answer_text,
        "degraded": rec.degraded,
        "llm_model": rec.llm_model,
        "elapsed_ms": rec.elapsed_ms,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "images": [{"url": i.url, "thumb_key": i.thumb_key,
                    "shot_type": i.shot_type, "width": i.width,
                    "height": i.height, "exif_stripped": i.exif_stripped}
                   for i in imgs],
    }


@router.post("/{diagnosis_id}/feedback", summary="反馈诊断结果对不对")
def feedback(diagnosis_id: int, resolved: bool,
             note: str = Query("", max_length=500),
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)) -> dict:
    """农户说「对」或「不对」。

    ★ 这不是可有可无的功能：文档难点 13 说结果无法验证，而这是唯一的
      真实标注来源。累积起来既能算准确率，也是后续做 badcase 库的原料。
    """
    from datetime import datetime, timezone

    rec = db.scalar(select(DiagnosisRecord).where(
        DiagnosisRecord.id == diagnosis_id,
        DiagnosisRecord.user_id == user.id))
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "诊断记录不存在")
    rec.feedback_resolved = resolved
    rec.feedback_note = note or None
    rec.feedback_at = datetime.now(timezone.utc)
    db.add(rec)
    db.commit()
    return {"ok": True, "diagnosis_id": rec.id, "resolved": resolved}
