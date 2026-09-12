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

# 集成测试固定测试用户：模块级 fixture 会自清理；这里再加一道 session 级兜底，
# 防止 pytest 被强杀/模块夹具异常时把数据留到下一轮。
_KNOWN_TEST_USERS = (
    "test-rag-incremental",
    "test-vector-reconcile",
    "test-cleanup-isolation",
)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest hook 签名
    """整轮测试结束后强制清理测试用户（DB 不可用时静默跳过）。"""
    try:
        from helpers import purge_test_user
    except Exception:  # noqa: BLE001 - 收集阶段失败时不阻断退出
        return
    for user_id in _KNOWN_TEST_USERS:
        try:
            purge_test_user(user_id, timeout=10)
        except Exception:  # noqa: BLE001 - 清理失败不改变测试结论
            pass
