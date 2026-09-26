"""HTTP 层端到端冒烟：真的起服务、真的发请求。

与 scripts/smoke_e2e.py 的区别：
  · smoke_e2e.py  直接构造 AgentContext 调 AgriAgent，绕过 HTTP —— 验证业务逻辑
  · 本脚本        走 uvicorn 的真实 HTTP 栈 —— 验证路由 / 鉴权 / 序列化 / multipart

★ 两者都必须有。只跑前者，路由层的参数名写错、依赖注入漏了、Pydantic 模型
  字段对不上，全都测不出来 —— 这些恰恰是最容易出错的部分。

用法（服务需已在本机运行）：
    PYTHONUTF8=1 ../.venv/Scripts/python.exe scripts/smoke_http.py
    PYTHONUTF8=1 ../.venv/Scripts/python.exe scripts/smoke_http.py --base http://127.0.0.1:8001/api/v1
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_BASE = "http://127.0.0.1:8001/api/v1"
TIMEOUT = 180

_pass = 0
_fail = 0
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [OK]   {name}")
    else:
        _fail += 1
        _failures.append(f"{name} — {detail}")
        print(f"  [FAIL] {name}  {detail}")
    return cond


# --------------------------------------------------------------------------- #
# HTTP 原语
# --------------------------------------------------------------------------- #


def _multipart(fields: dict, files: list[tuple[str, str, bytes]]
               ) -> tuple[bytes, str]:
    """手写 multipart 编码。

    ★ 不引 requests：这个项目的验证脚本要能在「只有标准库」的环境里跑。
      依赖越少，换台机器越不容易因为装不上包而卡住。
    """
    boundary = "----agri" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        if v is None:
            continue
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{k}"\r\n\r\n'
            f"{v}\r\n".encode("utf-8"))
    for name, filename, data in files:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\n'
            f"Content-Type: image/jpeg\r\n\r\n".encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _encode(path: str) -> str:
    """URL 里的非 ASCII 必须编码。

    ★ 踩过的坑：urllib 不会自动编码，`?crop_name=番茄` 直接抛
      UnicodeEncodeError: 'ascii' codec can't encode characters。
      而中文参数在这个项目里是常态（作物名、病害名、反馈内容），
      所以统一在这里处理，避免每个调用点各写一遍 quote() 又漏掉一个。
      只编码 query 部分，路径分隔符与 = & 保持原样。
    """
    if "?" not in path:
        return urllib.parse.quote(path, safe="/")
    p, _, q = path.partition("?")
    return urllib.parse.quote(p, safe="/") + "?" + urllib.parse.quote(q,
                                                                      safe="=&")


def call(method: str, path: str, *, token: str | None = None,
         body: dict | None = None, form: dict | None = None,
         files: list[tuple[str, str, bytes]] | None = None,
         base: str = DEFAULT_BASE, timeout: int = TIMEOUT):
    """返回 (status_code, parsed_body)。HTTP 错误也返回而不是抛异常，
    因为「返回 401」本身就是要断言的行为。"""
    headers: dict[str, str] = {}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None or files:
        data, ctype = _multipart(form or {}, files or [])
        headers["Content-Type"] = ctype
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(base + _encode(path), data=data,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw or "null")
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def sse(path: str, *, token: str, form: dict,
        base: str = DEFAULT_BASE) -> list[tuple[str, dict]]:
    """读一条 SSE 流，返回 [(event, data), ...]。

    ★ 用 urllib 手工解析而不是引 sseclient：要断言的是「事件顺序对不对」，
      手工解析反而看得更清楚，也不会因为库对空行/注释的处理差异而误判。
    """
    data, ctype = _multipart(form, [])
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": ctype,
                                          "Authorization": f"Bearer {token}"},
                                 method="POST")
    events: list[tuple[str, dict]] = []
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        event = "message"
        for line in resp:
            text = line.decode("utf-8").rstrip("\r\n")
            if text.startswith("event:"):
                event = text[6:].strip()
            elif text.startswith("data:"):
                try:
                    events.append((event, json.loads(text[5:].strip())))
                except json.JSONDecodeError:
                    events.append((event, {"raw": text[5:].strip()}))
            elif not text:
                event = "message"
    return events


def make_leaf_jpeg() -> bytes:
    """造一张「叶片」测试图：绿底 + 浅色斑点。

    ★ 必须是真的能解码的 JPEG，不能塞几个随机字节 —— 服务端会走
      PIL 解码 + EXIF 剥离，随便塞字节只会测出「图片解析失败」，
      测不到真正的上传链路。
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 480), (86, 140, 60))
    draw = ImageDraw.Draw(img)
    for i in range(70):
        x = (i * 37) % 430 + 25
        y = (i * 71) % 430 + 25
        draw.ellipse([x, y, x + 20, y + 15], fill=(214, 212, 186))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base

    print(f"目标：{base}\n")

    # ---------------- 1. 健康检查 ----------------
    print("[1] 健康检查")
    st, h = call("GET", "/health", base=base)
    check("GET /health 返回 200", st == 200, f"status={st}")
    if isinstance(h, dict):
        check("数据库 up", h.get("database") == "up", str(h.get("database")))
        check("redis up", h.get("redis") == "up", str(h.get("redis")))
        check("minio up", h.get("minio") == "up", str(h.get("minio")))
        check("知识库有文档", (h.get("knowledge_docs") or 0) > 0,
              f"docs={h.get('knowledge_docs')}")
        check("知识库有 chunk", (h.get("knowledge_chunks") or 0) > 0,
              f"chunks={h.get('knowledge_chunks')}")
        print(f"        llm={h.get('llm_enabled')} vision={h.get('vision_enabled')} "
              f"embedding={h.get('embedding_mode')}/{h.get('embedding_dim')}")

    # ---------------- 2. 鉴权 ----------------
    print("\n[2] 鉴权")
    st, _ = call("GET", "/farm/crops", base=base)
    check("无 token 访问受保护端点 → 401", st == 401, f"status={st}")

    suffix = uuid.uuid4().hex[:6]
    username = f"smoke_{suffix}"
    st, reg = call("POST", "/auth/register", base=base, body={
        "username": username, "password": "smoke1234",
        "display_name": "冒烟测试", "city": "郑州"})
    check("注册新用户 → 201", st == 201, f"status={st} body={reg}")

    st, tok = call("POST", "/auth/login", base=base, body={
        "username": username, "password": "smoke1234"})
    check("登录 → 200 且拿到 token",
          st == 200 and isinstance(tok, dict) and bool(tok.get("access_token")),
          f"status={st} body={tok}")
    token = (tok or {}).get("access_token", "")

    st, bad = call("POST", "/auth/login", base=base, body={
        "username": username, "password": "wrong-password"})
    check("密码错误 → 401", st == 401, f"status={st}")
    if isinstance(bad, dict):
        check("错误信息不泄露账号是否存在",
              "用户名或密码" in str(bad.get("detail", "")),
              str(bad.get("detail")))

    st, me = call("GET", "/auth/me", token=token, base=base)
    check("GET /auth/me → 200", st == 200, f"status={st}")
    if isinstance(me, dict):
        check("新用户默认开启老人模式", me.get("elder_mode") is True,
              f"elder_mode={me.get('elder_mode')}")

    # ---------------- 3. 农田档案 ----------------
    print("\n[3] 农田档案")
    st, crops = call("GET", "/farm/crops", token=token, base=base)
    check("作物字典非空", st == 200 and isinstance(crops, list) and len(crops) > 0,
          f"status={st} n={len(crops) if isinstance(crops, list) else '?'}")

    st, farm = call("POST", "/farm/farms", token=token, base=base, body={
        "name": "冒烟测试大棚", "city": "郑州市", "district": "中牟县",
        "total_area_mu": 3.5})
    check("创建农场 → 201", st == 201, f"status={st} body={farm}")
    farm_id = (farm or {}).get("id")

    st, field = call("POST", f"/farm/farms/{farm_id}/fields", token=token,
                     base=base, body={"name": "1 号棚", "area_mu": 1.2,
                                      "soil_type": "沙壤土"})
    check("创建地块 → 201", st == 201, f"status={st} body={field}")
    field_id = (field or {}).get("id")

    crop_id = crops[0]["id"] if isinstance(crops, list) and crops else None
    st, grow = call("POST", "/farm/growth-records", token=token, base=base, body={
        "field_id": field_id, "crop_id": crop_id, "stage": "开花期",
        "variety": "金棚 1 号"})
    check("创建生长记录 → 201", st == 201, f"status={st} body={grow}")

    st, ov = call("GET", "/farm/overview", token=token, base=base)
    check("首页总览 → 200", st == 200, f"status={st} body={str(ov)[:200]}")
    if isinstance(ov, dict):
        check("总览含当前作物", len(ov.get("current_crops") or []) > 0,
              str(ov.get("current_crops")))
        check("总览含天气", ov.get("weather") is not None,
              str(ov.get("weather")))
        if ov.get("weather"):
            check("模拟天气带免责声明",
                  bool(ov["weather"].get("caveat"))
                  or not ov["weather"].get("is_mock"),
                  str(ov["weather"].get("caveat")))
        print(f"        风险条目 {len(ov.get('risks') or [])} 条"
              + (f"，最高：{ov['risks'][0]['disease_name']} "
                 f"{ov['risks'][0]['risk_level']}"
                 if ov.get("risks") else ""))

    # ---------------- 4. 越权 ----------------
    print("\n[4] 越权防护")
    other = f"smoke_other_{suffix}"
    call("POST", "/auth/register", base=base, body={
        "username": other, "password": "smoke1234"})
    st, tok2 = call("POST", "/auth/login", base=base, body={
        "username": other, "password": "smoke1234"})
    token2 = (tok2 or {}).get("access_token", "")

    st, _ = call("PATCH", f"/farm/farms/{farm_id}", token=token2, base=base,
                 body={"name": "我改别人的农场"})
    check("他人改我的农场 → 404", st == 404, f"status={st}")

    st, _ = call("DELETE", f"/farm/farms/{farm_id}", token=token2, base=base)
    check("他人删我的农场 → 404", st == 404, f"status={st}")

    st, other_farms = call("GET", "/farm/farms", token=token2, base=base)
    check("他人的农场列表为空",
          st == 200 and other_farms == [], f"status={st} body={other_farms}")

    # ---------------- 5. 知识库 ----------------
    print("\n[5] 知识库")
    st, stats = call("GET", "/knowledge/stats", token=token, base=base)
    check("GET /knowledge/stats → 200", st == 200, f"status={st}")
    if isinstance(stats, dict):
        check("没有孤儿 chunk", (stats.get("chunks_orphan") or 0) == 0,
              f"orphan={stats.get('chunks_orphan')}")
        check("没有未向量化 chunk", (stats.get("chunks_unembedded") or 0) == 0,
              f"unembedded={stats.get('chunks_unembedded')}")
        print(f"        文档 {stats.get('documents')} 篇"
              f"（已审核 {stats.get('documents_approved')}）"
              f"，chunk {stats.get('chunks')} 个")

    st, diseases = call("GET", "/knowledge/diseases?crop_name=番茄", token=token,
                        base=base)
    check("按作物查病害 → 200 且有结果",
          st == 200 and isinstance(diseases, list) and len(diseases) > 0,
          f"status={st} n={len(diseases) if isinstance(diseases, list) else '?'}")

    st, sr = call("POST", "/knowledge/search", token=token, base=base, body={
        "query": "叶背有白毛，像撒了一层面粉", "crop_name": "番茄", "top_k": 5})
    check("检索 → 200", st == 200, f"status={st}")
    if isinstance(sr, dict):
        hits = sr.get("hits") or []
        check("检索有命中", len(hits) > 0, f"n={len(hits)}")
        if hits:
            top = hits[0]
            check("命中含 RRF 明细",
                  "fused_score" in top and isinstance(top.get("ranks"), dict),
                  str(top.get("ranks")))
            print(f"        改写后：{sr.get('query_rewritten')}")
            print(f"        Top1：{top.get('disease_name')} "
                  f"fused={top.get('fused_score')} ranks={top.get('ranks')}")

    # ---------------- 6. 农药与规则引擎 ----------------
    print("\n[6] 农药与规则引擎（闸门 B）")
    st, prods = call("GET", "/pesticide/products", token=token, base=base)
    check("产品清单 → 200 且非空",
          st == 200 and isinstance(prods, list) and len(prods) > 0,
          f"status={st}")
    if isinstance(prods, list) and prods:
        check("产品带数据免责声明", bool(prods[0].get("data_caveat")),
              str(prods[0].get("data_caveat"))[:60])

    st, rc = call("POST", "/pesticide/rule-check", token=token, base=base,
                  body={"crop_name": "番茄", "disease_name": "番茄晚疫病",
                        "growth_stage": "结果期"})
    check("规则校验 → 200", st == 200, f"status={st}")
    if isinstance(rc, dict):
        check("规则校验有 checked 计数", (rc.get("checked") or 0) > 0,
              f"checked={rc.get('checked')}")
        for item in (rc.get("approved") or [])[:2]:
            check(f"放行项「{item['product_name']}」带标签剂量原文",
                  bool(item.get("label_dosage_text")),
                  str(item.get("label_dosage_text"))[:40])
        print(f"        校验 {rc.get('checked')} 个产品，"
              f"通过 {len(rc.get('approved') or [])}，"
              f"拦截 {len(rc.get('blocked') or [])}")

    st, rc2 = call("POST", "/pesticide/rule-check", token=token, base=base,
                   body={"crop_name": "番茄", "disease_name": "番茄黄化曲叶病毒病"})
    check("病毒病 → 硬拦截（无登记药）",
          st == 200 and isinstance(rc2, dict)
          and rc2.get("hard_blocked") is True
          and len(rc2.get("approved") or []) == 0,
          f"hard_blocked={rc2.get('hard_blocked') if isinstance(rc2, dict) else '?'} "
          f"approved={len(rc2.get('approved') or []) if isinstance(rc2, dict) else '?'}")

    st, usage = call("POST", "/pesticide/usage", token=token, base=base, body={
        "field_id": field_id, "product_name_raw": "测试用药",
        "active_ingredient_name": "嘧菌酯", "resistance_group": "11",
        "note": "冒烟测试"})
    check("补记用药 → 201", st == 201, f"status={st} body={usage}")
    if isinstance(usage, dict):
        check("补记来源标记为 manual",
              usage.get("record_source") == "manual",
              str(usage.get("record_source")))

    st, ulist = call("GET", "/pesticide/usage", token=token, base=base)
    check("用药记录可读回",
          st == 200 and isinstance(ulist, list) and len(ulist) > 0,
          f"status={st}")

    # ---------------- 7. 任务 ----------------
    print("\n[7] 农事任务")
    st, task = call("POST", "/tasks", token=token, base=base, body={
        "title": "去棚里看看叶子", "task_type": "patrol",
        "due_date": "2026-09-26", "priority": 1})
    check("创建任务 → 201", st == 201, f"status={st} body={task}")
    task_id = (task or {}).get("id")

    st, tlist = call("GET", "/tasks?status=pending", token=token, base=base)
    check("任务列表可读", st == 200 and isinstance(tlist, list), f"status={st}")

    st, done = call("POST", f"/tasks/{task_id}/done?feedback=看过了，好多了",
                    token=token, base=base)
    check("标记完成 → 200 且状态为 done",
          st == 200 and isinstance(done, dict) and done.get("status") == "done",
          f"status={st} body={done}")
    if isinstance(done, dict):
        check("完成时记录了反馈", bool(done.get("feedback")),
              str(done.get("feedback")))

    st, _ = call("DELETE", f"/tasks/{task_id}", token=token, base=base)
    check("软删除任务 → 204", st == 204, f"status={st}")
    st, tlist2 = call("GET", "/tasks?status=done", token=token, base=base)
    check("软删后不再出现",
          st == 200 and all(t["id"] != task_id for t in (tlist2 or [])),
          str(tlist2))

    # ---------------- 8. 天气 ----------------
    print("\n[8] 天气")
    st, w = call("GET", f"/weather/current?field_id={field_id}", token=token,
                 base=base)
    check("当前天气 → 200", st == 200, f"status={st} body={str(w)[:150]}")
    if isinstance(w, dict):
        check("天气含结构化数值（温/湿/雨/风）",
              w.get("temp_avg") is not None and w.get("humidity_avg") is not None
              and w.get("rainfall_hours") is not None
              and w.get("wind_level") is not None,
              str(w))

    # ★★ D28 回归：不带 field_id 时**不能**把所有地块的记录混在一起累加。
    #   判据刻意落在**物理不变量**上（降雨小时数不可能超过「天数 × 24」），
    #   而不是落在「等于某个具体数值」上 —— 后者会随日期、随库里的存量数据
    #   变化，跑一次绿一次红，最后被人当成噪声忽略掉。
    #   实测踩过：修复前这个接口返回 rainfall_hours = 111.2（3 天），
    #   因为它把 10 来个地块同一天的记录全 sum 了一遍。
    st, w_nofield = call("GET", "/weather/current?days=3", token=token, base=base)
    if isinstance(w_nofield, dict) and st == 200:
        rain = w_nofield.get("rainfall_hours")
        check("★ 3 天累计降雨 ≤ 72 小时（跨地块污染回归）",
              rain is not None and rain <= 72,
              f"rainfall_hours={rain}（>72 说明把多个地块的记录加在一起了）")
        # ★★ D29 回归：窗口内缺日期要补，不能「有任意一行就整段返回」。
        #   旧逻辑会让 record_date 永久停在第一次写入的那几天。
        #   判据给一天余量，避免跨零点跑测试时假红。
        rd = str(w_nofield.get("record_date") or "")
        from datetime import date as _date, timedelta as _td
        floor = (_date.today() - _td(days=1)).isoformat()
        check("★ 天气日期不会永久过期（D29 回归）", rd >= floor,
              f"record_date={rd}，期望 ≥ {floor}（今天 {_date.today().isoformat()}）")
    else:
        check("不带 field_id 查天气 → 200", st == 200, f"status={st}")

    st, rk = call("GET", "/weather/risk?crop_name=番茄&growth_stage=开花期",
                  token=token, base=base)
    check("风险列表 → 200", st == 200, f"status={st}")
    if isinstance(rk, list) and rk:
        check("风险条目只给建议、不给用药指令",
              all("用药" not in (r.get("advice") or "") for r in rk),
              str(rk[0].get("advice")))
        # ★ 数字要经得起农户看。跨地块污染会一路传到**文案**里 ——
        #   理由栏曾出现「累计降雨 111.2 小时（需 ≥4 小时）」这种物理上
        #   不可能的数字（3 天最多 72 小时）。这种数字一旦被农户看到，
        #   整个风险提示的可信度就没了，所以要在文案层再兜一道。
        import re as _re
        bad = [x for r in rk for x in (r.get("reasons") or [])
               if (m := _re.search(r"累计降雨\s*([\d.]+)\s*小时", x))
               and float(m.group(1)) > 72]
        check("★ 风险理由里的降雨小时数也 ≤ 72", not bad, str(bad))
        print(f"        {len(rk)} 条风险，最高 {rk[0]['disease_name']} "
              f"{rk[0]['risk_level']}({rk[0]['score']})")

    # ---------------- 9. 诊断（文字） ----------------
    print("\n[9] 诊断（纯文字，无模型降级模式）")
    st, dg = call("POST", "/diagnosis", token=token, base=base, form={
        "query": "番茄叶子背面有一层白毛，像撒了面粉一样",
        "crop_name": "番茄", "growth_stage": "开花期",
        "field_id": str(field_id)})
    check("POST /diagnosis → 200", st == 200, f"status={st} body={str(dg)[:300]}")
    if isinstance(dg, dict):
        check("诊断有回答", bool(dg.get("answer")), str(dg.get("answer"))[:120])
        check("诊断有执行轨迹", len(dg.get("trace") or []) > 0,
              f"n={len(dg.get('trace') or [])}")
        check("诊断有诊断记录 id", dg.get("diagnosis_id") is not None,
              str(dg.get("diagnosis_id")))
        check("诊断落库了 tool_calls", isinstance(dg.get("tool_calls"), list),
              str(dg.get("tool_calls"))[:80])
        print(f"        终态={dg.get('state')} 意图={dg.get('intent')} "
              f"top1={dg.get('top1')} degraded={dg.get('degraded')}")
        if dg.get("confidence"):
            c = dg["confidence"]
            print(f"        置信度={c.get('score')} "
                  f"(margin={c.get('margin')} agree={c.get('agreement')} "
                  f"cover={c.get('coverage')}) passed={c.get('passed')}")
        nodes = [t["node"] for t in (dg.get("trace") or [])]
        check("轨迹无重复相邻节点",
              all(nodes[i] != nodes[i + 1] for i in range(len(nodes) - 1)),
              str(nodes))
        print(f"        轨迹：{' -> '.join(nodes)}")

    st, dg2 = call("POST", "/diagnosis", token=token, base=base, form={
        "query": "黄瓜叶子为什么发黄？", "crop_name": "黄瓜"})
    check("「为什么」类问题不误判成诊断",
          st == 200 and isinstance(dg2, dict) and dg2.get("intent") == "consult",
          f"intent={dg2.get('intent') if isinstance(dg2, dict) else '?'}")

    # ---------------- 10. 诊断（图片） ----------------
    print("\n[10] 诊断（带图片）")
    jpeg = make_leaf_jpeg()
    st, dg3 = call("POST", "/diagnosis", token=token, base=base,
                   form={"query": "叶子上有斑点", "crop_name": "番茄",
                         "shot_types": json.dumps(["leaf_back"])},
                   files=[("files", "leaf.jpg", jpeg)])
    check("带图片诊断 → 200", st == 200, f"status={st} body={str(dg3)[:300]}")
    if isinstance(dg3, dict):
        check("图片诊断没有图片处理错误",
              not any("image" in e for e in (dg3.get("errors") or [])),
              str(dg3.get("errors")))
        # ★ 这两条断言是补上的。之前只断言了「返回 200」，结果漏掉了一个
        #   严重缺陷：无视觉模型时接口返回 200、state=needs_more_info，
        #   但 answer 是空字符串、diagnosis_id 是 null —— 前端表现为
        #   「弹出一个空白气泡，且没法关联到任何记录」。
        #   状态码对不等于功能对。
        check("带图诊断有回答内容（不是空白气泡）", bool(dg3.get("answer")),
              f"answer={dg3.get('answer')!r}")
        check("带图诊断有诊断记录 id（拦截也要落库）",
              dg3.get("diagnosis_id") is not None,
              str(dg3.get("diagnosis_id")))
        print(f"        终态={dg3.get('state')} 意图={dg3.get('intent')} "
              f"top1={dg3.get('top1')} id={dg3.get('diagnosis_id')}")
        print(f"        回答：{(dg3.get('answer') or '')[:100]}")

    if isinstance(dg3, dict) and dg3.get("diagnosis_id"):
        st, det = call("GET", f"/diagnosis/{dg3['diagnosis_id']}", token=token,
                       base=base)
        check("诊断详情 → 200", st == 200, f"status={st}")
        if isinstance(det, dict):
            check("诊断记录标记为图片来源",
                  det.get("input_source") == "image",
                  str(det.get("input_source")))
            check("图片已入库",
                  len(det.get("images") or []) > 0,
                  f"images={len(det.get('images') or [])}")
            if det.get("images"):
                img = det["images"][0]
                check("图片已剥离 EXIF", img.get("exif_stripped") is True,
                      str(img.get("exif_stripped")))
                check("图片有宽高记录",
                      bool(img.get("width")) and bool(img.get("height")),
                      f"{img.get('width')}x{img.get('height')}")

    # ---------------- 11. 反馈 ----------------
    print("\n[11] 反馈闭环")
    st, fb = call("POST", f"/diagnosis/{dg.get('diagnosis_id')}/feedback"
                          f"?resolved=true&note=确实是白粉病", token=token,
                  base=base)
    check("提交反馈 → 200", st == 200, f"status={st} body={fb}")

    # ---------------- 12. 历史与对话 ----------------
    print("\n[12] 历史与对话")
    st, hist = call("GET", "/diagnosis/history", token=token, base=base)
    check("诊断历史非空",
          st == 200 and isinstance(hist, list) and len(hist) >= 3,
          f"status={st} n={len(hist) if isinstance(hist, list) else '?'}")

    st, sess = call("GET", "/chat/sessions", token=token, base=base)
    check("会话列表非空",
          st == 200 and isinstance(sess, list) and len(sess) > 0,
          f"status={st} n={len(sess) if isinstance(sess, list) else '?'}")
    if isinstance(sess, list) and sess:
        sid = sess[0]["session_id"]
        st, msgs = call("GET", f"/chat/sessions/{sid}", token=token, base=base)
        check("会话消息成对（user+assistant）",
              st == 200 and isinstance(msgs, list) and len(msgs) >= 2,
              f"status={st} n={len(msgs) if isinstance(msgs, list) else '?'}")
        roles = [m["role"] for m in (msgs or [])]
        check("消息含 assistant 回复", "assistant" in roles, str(roles))
        print(f"        会话 {sid[:12]}… 共 {len(msgs)} 条")

    # ---------------- 13. SSE 流式 ----------------
    print("\n[13] SSE 流式诊断")
    try:
        evs = sse("/diagnosis/stream", token=token, base=base, form={
            "query": "番茄叶子上有褐色斑点，边缘发黄", "crop_name": "番茄"})
        kinds = [e for e, _ in evs]
        check("SSE 有 start 事件", "start" in kinds, str(kinds[:6]))
        check("SSE 有 progress 事件（节点进度）", "progress" in kinds,
              str(kinds))
        check("SSE 有 final 事件", "final" in kinds, str(kinds))
        check("SSE 有 done 事件", "done" in kinds, str(kinds))
        final = next((d for e, d in evs if e == "final"), None)
        check("final 事件带完整回答",
              isinstance(final, dict) and bool(final.get("answer")),
              str(final)[:150] if final else "无 final")
        prog_nodes = [d.get("node") for e, d in evs if e == "progress"]
        print(f"        事件序列：{' -> '.join(kinds)}")
        print(f"        进度节点：{prog_nodes}")
    except Exception as exc:  # noqa: BLE001
        check("SSE 流式诊断", False, f"{type(exc).__name__}: {exc}")

    # ---------------- 14. 清理 ----------------
    print("\n[14] 清理测试数据")
    st, _ = call("DELETE", f"/farm/farms/{farm_id}", token=token, base=base)
    check("删除测试农场 → 204", st == 204, f"status={st}")
    st, farms_after = call("GET", "/farm/farms", token=token, base=base)
    check("软删后农场列表不含它",
          st == 200 and all(f["id"] != farm_id for f in (farms_after or [])),
          str(farms_after))

    # ---------------- 汇总 ----------------
    print("\n" + "=" * 62)
    total = _pass + _fail
    print(f"通过 {_pass}/{total}，失败 {_fail}")
    if _failures:
        print("\n失败明细：")
        for f in _failures:
            print(f"  · {f}")
    print("=" * 62)
    return 1 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
