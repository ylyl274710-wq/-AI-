"""临时探针：验证 backend/.env 里的 LLM 配置能否**真的调通**。

★ 为什么必须实测，不能只看 `llm_enabled`：
  那个标志只检查 `LLM_API_KEY` 是否非空，**完全不验证 base_url 和模型名**。
  模型名写错时它照样返回 true，真正的报错要等到用户第一次提问才暴露 ——
  而那时前端只会弹一句「模型调用失败」，看不出是模型名的问题。

★ 这个探针会真的发一次请求（消耗几个 token），但不会碰任何业务数据。
"""
from __future__ import annotations

import asyncio
import base64
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.services.llm import LLMClient  # noqa: E402


def make_png(w: int = 64, h: int = 64) -> str:
    """用标准库拼一张 w×h 的 PNG，返回 data URL（不依赖 Pillow）。

    ★ 为什么不能用 1×1 的像素图：
      硅基流动的 Qwen3-VL 系列会**直接拒收**，报
      400 code 20015 `height(1) or width(1) must be larger than 28`。
      第一版探针用的就是 1×1，于是报了一个**看起来像配置错误**的错 ——
      而实际是探针自己的测试数据不合规。
      测试数据不合规导致的失败最容易被误读成产品缺陷，所以这里
      干脆自己画一张尺寸合规、有内容的图（左半深绿 / 右半浅绿 / 一条亮色对角线，
      用来验证模型确实「看到了」而不是只回了个泛泛的答案）。
    """
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # 每行的 filter type，0 = None
        for x in range(w):
            if abs(x - y) < 2:
                raw += bytes((230, 240, 120))   # 对角线：亮黄绿
            elif x < w // 2:
                raw += bytes((40, 110, 50))     # 左半：深绿
            else:
                raw += bytes((130, 180, 80))    # 右半：浅绿

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
           + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def main() -> None:
    print("=" * 68)
    print("配置")
    print("=" * 68)
    print(f"  base_url     = {settings.llm_base_url}")
    print(f"  model        = {settings.llm_model}")
    print(f"  api_key      = {settings.llm_api_key[:6]}…(长度 {len(settings.llm_api_key)})")
    print(f"  timeout      = {settings.llm_timeout_seconds}s  retries={settings.llm_max_retries}")

    c = LLMClient()
    print(f"  available    = {c.available}")

    print()
    print("=" * 68)
    print("1. 最小对话调用（chat）")
    print("=" * 68)
    try:
        r = await asyncio.wait_for(
            c.chat([{"role": "user", "content": "只回复两个字：你好"}]),
            timeout=settings.llm_timeout_seconds + 10)
        print(f"  [OK] {r!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {exc}")

    print()
    print("=" * 68)
    print("2. 结构化输出（chat_json，诊断链路真正用的那条）")
    print("=" * 68)
    try:
        r = await asyncio.wait_for(
            c.chat_json(
                [{"role": "user",
                  "content": '输出 JSON：{"ok": true, "msg": "测试"}'}],
                schema_hint='{"ok": bool, "msg": str}'),
            timeout=settings.llm_timeout_seconds + 10)
        print(f"  [OK] {r!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {exc}")

    print()
    print("=" * 68)
    print("3. 视觉调用（拍照诊断用的那条）")
    print("=" * 68)
    print(f"  effective_vision_model = {settings.effective_vision_model}")
    print("  （若它等于 llm_model，说明 LLM_VISION_MODEL 是空的 —— 而主模型多半不是 VLM）")
    # ★ 尺寸必须 > 28px，见 make_png() 的注释
    px = make_png(64, 64)
    try:
        r = await asyncio.wait_for(
            c.vision(prompt="这张图里有什么？形状和颜色各说一句。",
                     image_data_urls=[px]),
            timeout=settings.llm_timeout_seconds + 20)
        print(f"  [OK] {r!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {exc}")

    print()
    print("=" * 68)
    print("结论怎么读")
    print("=" * 68)
    print("  · 1、2 通 + 3 不通 → 文本链路可用，拍照诊断不可用（去补 LLM_VISION_MODEL）")
    print("  · 1 不通 401      → base_url 和 key 不是同一家（本项目 key 属于硅基流动）")
    print("  · 全部不通        → 先看 base_url 能否访问，再看 key")


if __name__ == "__main__":
    asyncio.run(main())
