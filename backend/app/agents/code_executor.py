"""受限 Python 代码执行器（子进程隔离 + 安全沙箱）。

供 code_agent 使用：在**独立子进程**中执行用户代码片段，返回 stdout / 错误。
设计（对本地个人使用场景的务实防护）：
- **子进程隔离**：代码在独立 Python 进程运行，主进程用 `subprocess.run(timeout=...)`
  强制超时 kill——即使单行死循环（如 `while True: pass`）也能可靠中断
- **危险能力禁用**：文件读写、网络、子进程、系统调用、危险内置（open/eval/exec）
- **模块白名单**：math/json/datetime/random/collections 等纯计算标准库
- **输出截断**：限制 stdout/stderr 长度，防止刷屏
- **无持久状态**：每次执行全新 globals，不保留上次状态

> ⚠️ 这是"受限沙箱"，不是强隔离（如 Docker/seccomp）。若需运行不可信代码，请改用容器隔离。
"""
from __future__ import annotations

import builtins
import io
import json
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# 项目根目录（供子进程定位 app 包）：code_executor.py -> agents -> app -> backend
_BACKEND_DIR = Path(__file__).resolve().parents[2]

# 允许暴露给代码的安全内置子集（纯计算/常用工具，无 IO/系统能力）
_ALLOWED_BUILTIN_NAMES = frozenset(
    {
        "print", "len", "range", "enumerate", "zip", "map", "filter", "sorted",
        "reversed", "sum", "min", "max", "abs", "round", "int", "float", "str",
        "bool", "list", "dict", "set", "tuple", "type", "isinstance", "issubclass",
        "id", "hash", "repr", "format", "any", "all", "divmod", "pow", "oct",
        "hex", "bin", "chr", "ord", "next", "iter", "slice", "staticmethod",
        "classmethod", "property", "compile", "getattr", "setattr", "hasattr",
        "callable", "object", "super", "ascii", "bytes", "bytearray", "complex",
        "frozenset", "memoryview",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "AttributeError", "ZeroDivisionError", "StopIteration", "RuntimeError",
        "ArithmeticError", "OverflowError", "NotImplementedError",
        "True", "False", "None",
    }
)

# 允许 import 的模块白名单（仅纯计算 / 标准数据结构）
_ALLOWED_MODULES = frozenset(
    {
        "math", "json", "datetime", "random", "collections", "itertools",
        "functools", "re", "statistics", "decimal", "fractions", "string",
        "heapq", "bisect", "copy", "operator", "typing", "uuid",
    }
)


def _build_safe_builtins() -> dict:
    """构造受限 builtins：白名单子集 + 白名单 __import__。"""
    safe: dict = {
        name: getattr(builtins, name)
        for name in _ALLOWED_BUILTIN_NAMES
        if hasattr(builtins, name)
    }
    _real_import = builtins.__import__

    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        base = name.split(".")[0]
        if base not in _ALLOWED_MODULES:
            raise ImportError(
                f"模块 '{name}' 不在允许列表中（安全限制：仅支持纯计算标准库）"
            )
        return _real_import(name, globals, locals, fromlist, level)

    safe["__import__"] = _safe_import
    return safe


def _run_isolated(code: str, timeout: float, max_output: int) -> dict:
    """在受限环境执行用户代码（**仅在子进程内被调用**）。"""
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    result: dict = {"stdout": "", "stderr": "", "error": None}
    safe_builtins = _build_safe_builtins()
    safe_globals = {"__builtins__": safe_builtins, "__name__": "__main__"}
    start = time.monotonic()

    def _tracer(frame, event, arg):
        if event == "line" and time.monotonic() - start > timeout:
            raise TimeoutError(f"代码执行超过 {timeout:.0f}s 限制")
        return _tracer

    old_trace = sys.gettrace()
    sys.settrace(_tracer)
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, safe_globals)
    except TimeoutError as exc:
        result["error"] = str(exc)
    except SystemExit:
        result["error"] = "代码不允许调用 exit()/sys.exit()（安全限制）"
    except BaseException:
        result["error"] = traceback.format_exc(limit=3)
    finally:
        sys.settrace(old_trace)

    result["stdout"] = stdout_buf.getvalue()[:max_output]
    result["stderr"] = stderr_buf.getvalue()[:max_output]
    return result


def execute_code(code: str, timeout: float = 15.0, max_output: int = 8000) -> dict:
    """在独立子进程中执行 Python 代码，返回 {"stdout", "stderr", "error"}。

    子进程由 subprocess 管理：`timeout` 超时后强制 kill，
    即使单行死循环也能可靠中断，且隔离主进程状态。
    """
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(_BACKEND_DIR)!r})\n"
        "from app.agents.code_executor import _run_isolated\n"
        "code = sys.stdin.buffer.read().decode('utf-8', 'ignore')\n"
        f"r = _run_isolated(code, {timeout!r}, {max_output!r})\n"
        # ensure_ascii=True：子进程 stdout 编码可能非 UTF-8，用 ASCII 转义跨进程安全传输
        "sys.stdout.write(json.dumps(r, ensure_ascii=True))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            input=code.encode("utf-8"),
            capture_output=True,
            timeout=timeout + 1.0,  # 子进程启动/import 留 1s 余量
        )
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "",
            "error": f"代码执行超过 {timeout:.0f}s 限制（已强制终止）",
        }

    # 解析子进程 stdout 中的 JSON 结果
    try:
        result = json.loads(proc.stdout.decode("utf-8", "ignore"))
    except (ValueError, UnicodeDecodeError):
        result = {
            "stdout": "",
            "stderr": proc.stderr.decode("utf-8", "ignore")[:max_output],
            "error": "执行器异常：无法解析子进程输出",
        }
    return result
