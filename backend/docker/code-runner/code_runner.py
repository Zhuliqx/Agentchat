"""容器内代码执行入口（只读 stdin 代码 → stdout JSON）。

本文件是安全边界内的“不可信代码宿主”：镜像不包含业务源码/密钥，
容器以 --network none / --read-only / 非 root / 资源限额运行。
"""
from __future__ import annotations

import io
import json
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout


def _run(code: str, timeout: float, max_output: int) -> dict:
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    result: dict = {"stdout": "", "stderr": "", "error": None}
    globals_dict = {"__name__": "__main__"}
    start = time.monotonic()

    def _tracer(frame, event, arg):
        if event == "line" and time.monotonic() - start > timeout:
            raise TimeoutError(f"代码执行超过 {timeout:.0f}s 限制")
        return _tracer

    old_trace = sys.gettrace()
    sys.settrace(_tracer)
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(code, globals_dict)
    except TimeoutError as exc:
        result["error"] = str(exc)
    except SystemExit:
        result["error"] = "代码不允许调用 exit()/sys.exit()"
    except BaseException:
        result["error"] = traceback.format_exc(limit=3)
    finally:
        sys.settrace(old_trace)

    result["stdout"] = stdout_buf.getvalue()[:max_output]
    result["stderr"] = stderr_buf.getvalue()[:max_output]
    return result


if __name__ == "__main__":
    code = sys.stdin.buffer.read().decode("utf-8", "ignore")
    timeout = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    max_output = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    sys.stdout.write(json.dumps(_run(code, timeout, max_output), ensure_ascii=True))
