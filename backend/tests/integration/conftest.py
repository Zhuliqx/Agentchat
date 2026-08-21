"""pytest 公共配置：确保 backend 与 tests 目录在 sys.path，便于导入 app 包与测试辅助。"""
from __future__ import annotations

import sys
from pathlib import Path

# integration/ 子目录下：__file__ -> integration -> tests -> backend
BACKEND = Path(__file__).resolve().parent.parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
# tests/ 目录（供 from helpers import ...）
TESTS = Path(__file__).resolve().parent.parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))