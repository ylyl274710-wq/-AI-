"""pytest 全局配置：把 backend 加进 sys.path，使 `import app.*` 在任何 CWD 下可用。"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
