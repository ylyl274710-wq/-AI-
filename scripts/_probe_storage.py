"""临时探针：MinIO 的 license 限制到底影响哪一层。

背景 —— _infra/logs/minio.log 里有一条：
    WARN: No valid license found, running in offline mode.
          All S3 operations are denied.

而 app/services/storage.py 的降级判据只有 bucket_exists()，
不含写入。所以要分别验证：

  1. MinioStorage 直连时，bucket_exists / put_object / remove_object
     各自是否真的被拒 —— 定位「拒到哪一层」
  2. get_storage() 最终选中哪个后端 —— 定位「生产路径实际走哪条」
  3. ★ 最关键：如果 bucket_exists 能过、put_object 被拒，
     那降级逻辑就是个假保险 —— 探测通过、上传时才炸。

★ 这是只读 + 一次写入探针对象（key 带 probe/ 前缀，测完删掉），
  不碰任何业务数据。跑完可以删掉本文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.services.storage import MinioStorage, get_storage, reset_storage  # noqa: E402

KEY = "probe/license-check.txt"
PAYLOAD = b"hello-agri"


def step(label: str, fn) -> None:
    try:
        r = fn()
        print(f"  [OK  ] {label} -> {r!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {label} -> {type(exc).__name__}: {exc}")


print("=" * 68)
print("0. 配置")
print("=" * 68)
print(f"  minio_endpoint = {settings.minio_endpoint}")
print(f"  minio_bucket   = {settings.minio_bucket}")
print(f"  minio_secure   = {settings.minio_secure}")

print()
print("=" * 68)
print("1. MinioStorage 直连 —— 逐层看哪个操作被拒")
print("=" * 68)
ms = None
try:
    ms = MinioStorage()
    print("  构造 MinioStorage：成功")
except Exception as exc:  # noqa: BLE001
    print(f"  构造 MinioStorage：失败 {type(exc).__name__}: {exc}")

if ms is not None:
    step("bucket_exists()", lambda: ms.client.bucket_exists(ms.bucket))
    step("healthy()", ms.healthy)
    step("put_object()", lambda: ms.put(KEY, PAYLOAD, "text/plain"))
    step("stat_object()", lambda: ms.client.stat_object(ms.bucket, KEY).size)
    step("remove_object()", lambda: ms.delete(KEY))

print()
print("=" * 68)
print("2. get_storage() 实际选中谁（生产路径）")
print("=" * 68)
reset_storage()
st = get_storage()
print(f"  >>> 选中的后端: {st.name}")
print(f"  >>> 存储位置: {getattr(st, 'base', settings.minio_endpoint)}")

print()
print("=" * 68)
print("3. ★ 假保险检验：探测能过但写入会炸吗？")
print("=" * 68)
if ms is not None:
    probe_ok = False
    write_ok = False
    try:
        probe_ok = bool(ms.client.bucket_exists(ms.bucket))
    except Exception:  # noqa: BLE001
        probe_ok = False
    try:
        ms.put("probe/write-check.txt", PAYLOAD, "text/plain")
        write_ok = True
        ms.delete("probe/write-check.txt")
    except Exception:  # noqa: BLE001
        write_ok = False

    print(f"  健康探针 bucket_exists : {'通过' if probe_ok else '不通过'}")
    print(f"  真实写入 put_object    : {'成功' if write_ok else '失败'}")
    if probe_ok and not write_ok:
        print("  ★★ 命中假保险：探针说健康、上传时才炸 —— 降级逻辑形同虚设")
    elif not probe_ok:
        print("  ✓ 探针会拦下来，能正确降级到本地磁盘")
    else:
        print("  ✓ 读写都正常，MinIO 可用")

print()
print("=" * 68)
print("4. 收尾：清掉探针可能留下的对象")
print("=" * 68)
if ms is not None:
    step("remove_object(probe/)", lambda: ms.delete(KEY))
