"""Python 代码执行器（默认一次性容器沙箱）。

供 code_agent 使用，安全边界在容器层：
- code_exec_mode=docker（默认）：docker run --network none --read-only、非 root、
  drop capabilities、禁提权、CPU/内存/PID 限额，代码经 stdin 传入；
- code_exec_mode=subprocess：旧式本机子进程 + 内置白名单，**仅本地调试用，
  不是安全边界**（对象图可逃逸，勿用于不可信代码）。
"""
from __future__ import annotations

import builtins
import io
import json
import subprocess
import sys
import time
import traceback
import uuid
from contextlib import redirect_stderr, redirect_stdout

from app.config import BASE_DIR as _BACKEND_DIR
from app.config import settings

# 允许暴露给代码的安全内置子集（仅 subprocess 调试模式使用）
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

# 允许 import 的模块白名单（仅 subprocess 调试模式使用）
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


def _run_subprocess(code: str, timeout: float, max_output: int) -> dict:
    """旧式本机子进程执行（仅调试用，不是安全边界）。"""
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


def _run_docker(code: str, timeout: float, max_output: int) -> dict:
    """在一次性容器中执行代码（安全边界）。

    容器参数：无网络、根文件系统只读、非 root、drop 全部 capabilities、
    禁提权、CPU/内存/PID 限额；代码经 stdin 传入，结果经 stdout JSON 返回。
    """
    name = f"agentchat-code-{uuid.uuid4().hex[:12]}"
    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", name,
        "--network", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "10001:10001",
        "--memory", "256m",
        "--memory-swap", "256m",
        "--cpus", "0.5",
        "--pids-limit", "64",
        "--stop-timeout", "2",
        settings.code_exec_image,
        str(timeout), str(max_output),
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=code.encode("utf-8"),
            capture_output=True,
            timeout=timeout + 6.0,  # 容器启动/退出留余量
        )
    except FileNotFoundError:
        return {
            "stdout": "",
            "stderr": "",
            "error": "Docker CLI 不可用：code_exec_mode=docker 需要本机可执行 docker",
        }
    except subprocess.TimeoutExpired:
        try:
            # 宿主超时杀掉 docker CLI 后容器可能残留：按名字强制清理
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
        return {
            "stdout": "",
            "stderr": "",
            "error": f"代码执行超过 {timeout:.0f}s 限制（容器已强制终止）",
        }

    if proc.returncode != 0:
        return {
            "stdout": "",
            "stderr": proc.stderr.decode("utf-8", "ignore")[:max_output],
            "error": (
                f"容器执行失败（exit={proc.returncode}）："
                "请确认已构建镜像 docker build -t agentchat-code-runner "
                "-f backend/docker/code-runner/Dockerfile backend/docker/code-runner"
            ),
        }
    try:
        return json.loads(proc.stdout.decode("utf-8", "ignore"))
    except (ValueError, UnicodeDecodeError):
        return {
            "stdout": "",
            "stderr": proc.stderr.decode("utf-8", "ignore")[:max_output],
            "error": "执行器异常：无法解析容器输出",
        }


def execute_code(code: str, timeout: float = 15.0, max_output: int = 8000) -> dict:
    """执行不可信 Python 代码，返回 {"stdout", "stderr", "error"}。

    code_exec_mode=docker（默认）：一次性容器 + 网络/文件系统/资源限制，
    这是唯一的安全边界；subprocess 仅用于本地调试。
    """
    mode = getattr(settings, "code_exec_mode", "subprocess").lower()
    if mode == "docker":
        return _run_docker(code, timeout, max_output)
    return _run_subprocess(code, timeout, max_output)
